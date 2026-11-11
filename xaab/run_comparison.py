"""Owned temporary servers for the approved Edge/QEV XAAB comparison.
Parent process executes only local server/runner programs, never scenario text.
The classifier itself runs inside the existing bubblewrap boundary.
"""
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run as native
sys.path.insert(0, str(ROOT/'xaab'))
import runner

if __name__ == '__main__':
    root = ROOT/'xaab'
    baseline = root/'run-001'
    frozen = (baseline/'inputs.jsonl').read_bytes()
    manifest, cases = runner.load_cases(root)
    policy = runner.read_policy(ROOT/'trial/judge_adapter.py')
    expected_inputs = [{'id':c['id'], 'payload':runner.payload(c,policy)} for c in cases]
    assert expected_inputs == [json.loads(line) for line in frozen.splitlines()]
    assert len(cases) == 180
    live_before = native.get('http://127.0.0.1:18872/v1/models')
    (root/'comparison-live-before.json').write_text(json.dumps(live_before,indent=2)+'\n')
    for filename, name in [('vllm-d3-edge-Q8_0.gguf','edge-001'),
                           ('qev-0.8b-text-Q8_0.gguf','qev-001')]:
        dest = root/name
        dest.mkdir(exist_ok=False)
        model = native.MODELS/filename
        metadata = native.metadata(model)
        assert any(k.endswith('.decision.type') for k in metadata['metadata'])
        metadata['sha256'] = hashlib.file_digest(model.open('rb'),'sha256').hexdigest()
        (dest/'model-file.json').write_text(json.dumps(metadata,indent=2)+'\n')
        with socket.socket() as sock:
            sock.settimeout(2)
            assert sock.connect_ex(('127.0.0.1',18874)) != 0, 'test port occupied'
        args = ['-m',str(model).replace('/mnt/d/','D:/'),'--host','127.0.0.1','--port','18874',
                '-c','8192','-np','1','-ngl','all','--device','CUDA0','--split-mode','none',
                '-fa','on','-b','8192','-ub','8192','--fit','off','--no-webui','--no-agent',
                '--offline','--alias','xaab-comparison']
        executable = '/mnt/d/textgen/2026/llama-b12236-bin-win-cuda-13.5-x64/llama-server.exe'
        (dest/'launch.json').write_text(json.dumps({'binary':native.BIN,'args':args},indent=2)+'\n')
        server = None
        pid = None
        print(name, 'starting separate server on 18874; live 18872 unchanged',flush=True)
        query = "Get-CimInstance Win32_Process -Filter \"name = 'llama-server.exe'\" | Where-Object { $_.CommandLine -like '*--port 18874*xaab-comparison*' }"
        with (dest/'server.stdout.log').open('x') as stdout, (dest/'server.stderr.log').open('x') as stderr:
            try:
                server = subprocess.Popen([executable,*args],stdout=stdout,stderr=stderr)
                time.sleep(2)
                # Windows PowerShell 5.1 lacks ConvertTo-Json -AsArray.
                found = json.loads(native.ps(f'ConvertTo-Json -Compress -InputObject @({query} | Select-Object ProcessId,ExecutablePath,CommandLine)'))
                assert len(found) == 1, found
                owned = found[0]
                assert owned['ExecutablePath'].lower() == native.BIN.lower(), owned
                pid = int(owned['ProcessId'])
                (dest/'owned-process.json').write_text(json.dumps(owned,indent=2)+'\n')
                deadline = time.monotonic()+180
                while True:
                    try:
                        if native.get('http://127.0.0.1:18874/health',timeout=3).get('status') == 'ok':
                            break
                    except Exception:
                        pass
                    if server.poll() is not None:
                        raise RuntimeError('test server exited; inspect server.stderr.log')
                    if time.monotonic() > deadline:
                        raise TimeoutError('test server readiness timeout')
                    time.sleep(1)
                for endpoint in ['props','v1/models']:
                    (dest/(endpoint.replace('/','-')+'.json')).write_text(json.dumps(native.get('http://127.0.0.1:18874/'+endpoint),indent=2)+'\n')
                warm = native.get('http://127.0.0.1:18874/v1/systemone',expected_inputs[0]['payload'])
                runner.probability(warm)
                (dest/'warmup.json').write_text(json.dumps(warm,indent=2)+'\n')
                command = ['bash',str(root/'launch.sh'),str(dest),'--port','18874']
                print(name,'ready; scoring 180 frozen inputs inside sandbox',flush=True)
                with (dest/'runner.log').open('x') as log:
                    job = subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                    for line in job.stdout:
                        log.write(line); log.flush()
                        if line.startswith('[') and (int(line.split('/')[0][1:]) % 30 == 0):
                            print(name,line.rstrip(),flush=True)
                    assert job.wait() == 0, 'classifier failed; inspect runner.log'
                assert (dest/'inputs.jsonl').read_bytes() == frozen, 'input drift'
                summary = json.loads((dest/'summary.json').read_text())
                assert summary['source'] == manifest
                assert summary['policy_sha256'] == hashlib.sha256(policy.encode()).hexdigest()
                assert len(summary['rows']) == 180 and all('p' in r for r in summary['rows'])
                assert summary['metrics_050']['errors'] == 0
                print(name,json.dumps(summary['metrics_050']),flush=True)
            finally:
                if server is not None:
                    if pid is None:
                        raw = native.ps(f'ConvertTo-Json -Compress -InputObject @({query} | Select-Object ProcessId,ExecutablePath,CommandLine)')
                        found = json.loads(raw)
                        if len(found) == 1 and found[0]['ExecutablePath'].lower() == native.BIN.lower():
                            pid = int(found[0]['ProcessId'])
                    if pid is not None:
                        cleanup = native.ps(f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p -and $p.ExecutablePath -eq '{native.BIN}' -and $p.CommandLine -like '*--port 18874*xaab-comparison*') {{ Stop-Process -Id {pid} -ErrorAction Stop; 'stopped exact owned test server' }} else {{ throw 'Owned-process identity changed; refusing stop' }}")
                        (dest/'cleanup.txt').write_text(cleanup+'\n')
                        print(name,cleanup,flush=True)
                    server.wait(timeout=20)
                    for _ in range(30):
                        with socket.socket() as sock:
                            sock.settimeout(.5)
                            free = sock.connect_ex(('127.0.0.1',18874)) != 0
                        if free: break
                        time.sleep(.5)
                    assert free, 'test port not released'
    live_after = native.get('http://127.0.0.1:18872/v1/models')
    assert live_after['data'][0]['id'] == live_before['data'][0]['id']
    assert live_after['data'][0]['meta'] == live_before['data'][0]['meta']
    with socket.create_connection(('127.0.0.1',18873),timeout=2):
        pass
    (root/'comparison-live-after.json').write_text(json.dumps(live_after,indent=2)+'\n')
    print('Both comparisons complete; live Lite server unchanged; temporary test port released.',flush=True)
