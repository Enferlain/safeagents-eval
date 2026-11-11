"""Offline replay using one fixed trusted model on an owned temporary server."""
import base64
import json
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from decision_models import MODEL_SPECS, get_model
from observation import Store
from observe import ENDPOINT, PrivateParser

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = '/mnt/d/textgen/2026/llama-b12236-bin-win-cuda-13.5-x64/llama-server.exe'
NATIVE_BINARY = r'D:\textgen\2026\llama-b12236-bin-win-cuda-13.5-x64\llama-server.exe'
POWERSHELL = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'


def _powershell(script):
    # Capture in memory for the caller's owner-only logs, never run.ps's shared file.
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()


def health_request():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('redirect refused')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(ENDPOINT + '/health', timeout=3) as response:
        return json.load(response)


def _port_free():
    with socket.socket() as sock:
        sock.settimeout(.5)
        return sock.connect_ex(('127.0.0.1', 18874)) != 0


def _owned_process(query, launch):
    found = json.loads(_powershell('ConvertTo-Json -Compress -InputObject @(' + query +
                                   ' | Select-Object ProcessId,ExecutablePath,CommandLine)'))
    if not isinstance(found, list) or len(found) != 1:
        raise RuntimeError('owned model process not identified')
    process = found[0]
    pid = process.get('ProcessId')
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError('invalid owned process ID')
    if str(process.get('ExecutablePath', '')).casefold() != NATIVE_BINARY.casefold():
        raise RuntimeError('owned executable mismatch')
    command = process.get('CommandLine')
    tokens = shlex.split(command, posix=False) if isinstance(command, str) else []
    if len(tokens) != len(launch) + 1 or tokens[0].strip('"').casefold() != NATIVE_BINARY.casefold() or tokens[1:] != launch:
        raise RuntimeError('owned command mismatch')
    return process


def run_model(store, model_key, limit=0, include_probes=False):
    model = get_model(model_key)
    eligible = any(r.get('payload') is not None and
                   (r.get('origin', 'traffic') == 'traffic' or include_probes)
                   for r in store.read('calls.jsonl'))
    if not eligible:
        print('No eligible captures; temporary replay model was not started.')
        return 0
    if not _port_free():
        raise RuntimeError('temporary port occupied; refusing launch')
    run_id = uuid.uuid4().hex
    alias = 'observation-replay-' + model_key + '-' + run_id
    logs = Store(store.path / 'replay-runs' / run_id)
    launch = ['-m', model['model_path'], '--host', '127.0.0.1', '--port', '18874',
              '-c', '8192', '-np', '1', '-ngl', 'all', '--device', 'CUDA0', '--split-mode', 'none',
              '-fa', 'on', '-b', '8192', '-ub', '8192', '--fit', 'off',
              '--no-webui', '--no-agent', '--offline', '--alias', alias]
    logs.write_text('launch.json', json.dumps({'model_key': model_key, 'binary': NATIVE_BINARY,
                                             'args': launch}, indent=2) + '\n')
    query = ('Get-CimInstance Win32_Process -Filter "name = \'llama-server.exe\'" | '
             "Where-Object { $_.ExecutablePath -eq '" + NATIVE_BINARY +
             "' -and $_.CommandLine -like '*--port 18874*--alias " + alias + "*' }")
    server = None
    owned = None
    for name in ('server.stdout.log', 'server.stderr.log', 'replay.stdout.log', 'replay.stderr.log'):
        logs.write_text(name, '')
    print('Starting temporary local ' + model['label'] + ' replay model.', flush=True)
    try:
        with (logs.path / 'server.stdout.log').open('a') as out, (logs.path / 'server.stderr.log').open('a') as err:
            server = subprocess.Popen([EXECUTABLE, *launch], stdout=out, stderr=err)
        time.sleep(2)
        owned = _owned_process(query, launch)
        logs.write_text('owned-process.json', json.dumps(owned, indent=2) + '\n')
        deadline = time.monotonic() + 180
        while True:
            if server.poll() is not None:
                raise RuntimeError('temporary model exited; inspect private logs')
            try:
                if health_request().get('status') == 'ok':
                    break
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                # Startup retries remain visible without recording private exception text.
                logs.write_text('readiness-error.txt', type(exc).__name__ + '\n')
            if time.monotonic() > deadline:
                raise TimeoutError('temporary model readiness timeout')
            time.sleep(1)
        command = [sys.executable, str(ROOT / 'trial/observe.py'), '--store', str(store.path),
                   'replay', '--model', model_key, '--endpoint', ENDPOINT]
        if limit:
            command.extend(['--limit', str(limit)])
        if include_probes:
            command.append('--include-probes')
        with (logs.path / 'replay.stdout.log').open('a') as out, (logs.path / 'replay.stderr.log').open('a') as err:
            result = subprocess.run(command, cwd=ROOT, check=False, stdout=out, stderr=err)
        return result.returncode
    finally:
        if server is not None:
            # Even startup failures may leave a Windows child; rediscover only our
            # exact unique alias/executable/arguments. Never stop a port's occupant.
            if owned is None:
                owned = _owned_process(query, launch)
            pid = owned['ProcessId']
            encoded_command = base64.b64encode(owned['CommandLine'].encode('utf-8')).decode('ascii')
            expected = "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded_command + "'))"
            cleanup = _powershell(f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
                                  f"if ($p -and $p.ExecutablePath -eq '{NATIVE_BINARY}' -and $p.CommandLine -ceq ({expected})) "
                                  f"{{ Stop-Process -Id {pid} -ErrorAction Stop; 'stopped exact owned replay model' }} "
                                  "elseif (!$p) { 'owned replay model already exited' } "
                                  "else { throw 'Process identity changed; refusing stop' }")
            logs.write_text('cleanup.txt', cleanup + '\n')
            server.wait(timeout=20)
            for _ in range(30):
                if _port_free():
                    break
                time.sleep(.5)
            else:
                raise RuntimeError('temporary replay port was not released')


def main(argv=None, *, store_factory=None):
    try:
        parser = PrivateParser(description=__doc__, allow_abbrev=False)
        parser.add_argument('--model', required=True, choices=tuple(MODEL_SPECS))
        parser.add_argument('--store', type=Path, default=ROOT / 'trial/private-observation')
        parser.add_argument('--limit', type=int, default=0, help='maximum scores; 0 means all eligible captures')
        parser.add_argument('--include-probes', action='store_true')
        args = parser.parse_args(argv)
        if args.limit < 0:
            raise ValueError('limit must be nonnegative')
        store = (store_factory or Store)(args.store)
        return run_model(store, args.model, args.limit, args.include_probes)
    except Exception as exc:  # noqa: BLE001 -- private CLI boundary emits exception type only.
        print('error: ' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
