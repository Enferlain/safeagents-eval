"""Offline launcher contracts; only inert stores and mocked Windows processes."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_observe import FixtureStore, capture


class ReplayModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'], prefix='replay-model-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.store = FixtureStore(Path(self.tmp.name) / 'store')

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('replay_model'),
                             'model-neutral fixed local launcher must exist')
        import replay_model
        return replay_model

    def test_health_transport_is_fixed_loopback_without_proxies_or_redirects(self):
        import urllib.request
        from unittest.mock import MagicMock
        module = self.module()
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"status":"ok"}'
        with patch.object(urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(module.health_request(), {'status': 'ok'})
        self.assertEqual(opener.open.call_args.args, ('http://127.0.0.1:18874/health',))
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 3})
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in build.call_args.args))
        redirect = next(h for h in build.call_args.args if isinstance(h, urllib.request.HTTPRedirectHandler))
        with self.assertRaises(ValueError):
            redirect.redirect_request(None, None, 302, '', {}, 'https://example.invalid')

    def test_owned_process_pins_wrapper_argv_without_powershell(self):
        module = self.module()
        self.assertFalse(hasattr(module, '_powershell'), 'PowerShell round-trips removed from the replay lifecycle')
        server = MagicMock()
        server.pid = 4321
        argv = ['/init', module.EXECUTABLE, module.EXECUTABLE, '-m', 'D:/x.gguf', '--alias', 'a']
        with patch.object(module, '_wrapper_argv', return_value=argv):
            owned = module._owned_process(server, ['-m', 'D:/x.gguf', '--alias', 'a'])
        self.assertEqual(owned['wsl_pid'], 4321)

    def test_occupied_port_refuses_launch_without_cleanup(self):
        module = self.module()
        self.store.append('calls.jsonl', capture())
        with patch.object(module, '_port_free', return_value=False), \
             patch.object(module.subprocess, 'Popen') as start, \
             patch.object(module.os, 'kill') as kill, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(module.main(['--store', str(self.store.path), '--model', 'lite'], store_factory=FixtureStore), 1)
        start.assert_not_called()
        kill.assert_not_called()
        self.assertEqual(output.getvalue().strip(), 'error: RuntimeError')

    def test_replay_failure_still_cleans_up_the_owned_server(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        module = self.module()
        self.store.append('calls.jsonl', capture())
        for failure in ('replay-exit', 'replay-exception', 'readiness-timeout', 'server-exit', 'readiness-error'):
            owned = {}
            server = MagicMock()
            server.pid = 4321
            server.poll.return_value = 1 if failure == 'server-exit' else None
            def start(command, owned=owned, **kwargs):
                owned['launch'] = list(command[1:])
                return server
            def wrapper_argv(pid, owned=owned):
                return ['/init', module.EXECUTABLE, module.EXECUTABLE, *owned['launch']]
            with patch.object(module, '_port_free', return_value=True), \
                 patch.object(module.subprocess, 'Popen', side_effect=start), \
                 patch.object(module, '_wrapper_argv', side_effect=wrapper_argv), \
                 patch.object(module.os, 'kill') as kill, \
                 patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=6),
                              side_effect=ConnectionError('INERT_PRIVATE_FAILURE') if failure == 'replay-exception' else None) as run, \
                 patch.object(module, 'health_request', return_value={'status': 'loading' if failure == 'readiness-timeout' else 'ok'},
                              side_effect=[ConnectionError('INERT_PRIVATE_FAILURE'), {'status': 'ok'}] if failure == 'readiness-error' else None), \
                 patch.object(module.time, 'monotonic', side_effect=[0, 1, 181] if failure == 'readiness-error' else [0, 181]), \
                 patch.object(module.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as output:
                code = module.main(['--store', str(self.store.path), '--model', 'lite'], store_factory=FixtureStore)
            self.assertEqual(code, 6 if failure in ('replay-exit', 'readiness-error') else 1)
            server.wait.assert_called_once_with(timeout=20)
            kill.assert_called_once()
            self.assertEqual(kill.call_args.args[0], 4321)
            if failure in ('readiness-timeout', 'server-exit'):
                run.assert_not_called()
            self.assertNotIn('INERT_PRIVATE', output.getvalue())
            if failure == 'readiness-error':
                errors = list(self.store.path.glob('replay-runs/*/readiness-error.txt'))
                self.assertEqual([path.read_text() for path in errors], ['ConnectionError\n'])

    def test_unverified_ownership_is_never_stopped(self):
        module = self.module()
        self.store.append('calls.jsonl', capture())
        for argv in ([], ['/init'], ['/init', module.EXECUTABLE, 'INERT_PRIVATE_UNRELATED', '-m', 'x'],
                      ['/init', module.EXECUTABLE, module.EXECUTABLE, 'wrong', 'args'],
                      ['/bin/something-else', module.EXECUTABLE, module.EXECUTABLE, '-m', 'x']):
            with patch.object(module, '_port_free', return_value=True), \
                 patch.object(module.subprocess, 'Popen', return_value=MagicMock()) as start, \
                 patch.object(module, '_wrapper_argv', return_value=argv), \
                 patch.object(module.os, 'kill') as kill, \
                 patch.object(module.subprocess, 'run') as replay, \
                 patch.object(module.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.main(['--store', str(self.store.path), '--model', 'qev'], store_factory=FixtureStore), 1)
            replay.assert_not_called()
            kill.assert_not_called()
            self.assertNotIn('INERT_PRIVATE', output.getvalue())

    def test_modules_import_without_startup_network_store_or_audit_effects(self):
        import subprocess
        module = self.module()
        script = '''import importlib, os, pathlib, socket, subprocess, sys
sys.path.insert(0, sys.argv[1])
sys.dont_write_bytecode = True
before = (list(sys.path), os.getcwd(), sys.dont_write_bytecode)
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen', 'sys.addaudithook'}:
        raise AssertionError('import side effect: ' + event)
    if event == 'open':
        path, mode, flags = args
        if 'private-observation' in str(path) or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
            raise AssertionError('import touched private store or wrote a file')
sys.addaudithook(audit)
for name in ('decision_models', 'observe', 'replay_model', 'replay_qev'):
    importlib.import_module(name)
assert before == (list(sys.path), os.getcwd(), sys.dont_write_bytecode)
print('imports inert')
'''
        result = subprocess.run([sys.executable, '-c', script,
                                 str(module.ROOT / 'trial')], capture_output=True, text=True, check=False,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'imports inert')

    def test_cli_rejects_implicit_selection_and_caller_capabilities(self):
        from unittest.mock import Mock
        module = self.module()
        invalid = [[], ['--model', 'INERT_PRIVATE_MODEL'], ['--m', 'lite'],
                   ['--model', 'qev', '--limit', '-1'],
                   ['--model', 'lite', '--endpoint', 'http://example.invalid'],
                   ['--model', 'qev', '--executable', 'INERT_PRIVATE_EXECUTABLE'],
                   ['--model', 'qev', '--model-path', 'INERT_PRIVATE_MODEL_PATH']]
        for args in invalid:
            factory = Mock(return_value=self.store)
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.main(args, store_factory=factory), 1)
                factory.assert_not_called()
                self.assertNotIn('INERT_PRIVATE', output.getvalue())

    def test_legacy_entrypoint_delegates_with_explicit_qev_selection(self):
        import replay_qev
        module = self.module()
        self.assertTrue(hasattr(replay_qev, 'main'), 'legacy entrypoint must be a callable compatibility wrapper')
        with patch.object(module, 'main', return_value=7) as replay:
            self.assertEqual(replay_qev.main(['--store', str(self.store.path), '--limit', '4', '--include-probes']), 7)
        forwarded = replay.call_args.args[0]
        self.assertEqual(forwarded[forwarded.index('--model') + 1], 'qev')
        self.assertEqual(forwarded[forwarded.index('--limit') + 1], '4')
        with patch.object(module, 'main') as replay, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(replay_qev.main(['--model', 'lite']), 1)
            replay.assert_not_called()

    def test_selected_weights_launch_replay_and_cleanup_only_owned_process(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        module = self.module()
        self.assertTrue(hasattr(module, 'run_model'), 'owned temporary model lifecycle must exist')
        self.store.append('calls.jsonl', capture())
        for key, filename in [('lite', 'vllm-d3-lite-Q8_0.gguf'), ('qev', 'qev-0.8b-text-Q8_0.gguf')]:
            owned = {}
            server = MagicMock()
            server.pid = 5309
            server.poll.return_value = None
            def start(command, owned=owned, **kwargs):
                owned['command'] = command
                owned['launch'] = list(command[1:])
                return server
            def wrapper_argv(pid, owned=owned):
                return ['/init', module.EXECUTABLE, module.EXECUTABLE, *owned['launch']]
            sock = MagicMock()
            sock.__enter__.return_value.connect_ex.return_value = 1
            with patch.object(module.subprocess, 'Popen', side_effect=start), \
                 patch.object(module, '_wrapper_argv', side_effect=wrapper_argv), \
                 patch.object(module.os, 'kill') as kill, \
                 patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
                 patch.object(module.socket, 'socket', return_value=sock), \
                 patch.object(module, 'health_request', return_value={'status': 'ok'}), \
                 patch.object(module.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                code = module.main(['--store', str(self.store.path), '--model', key, '--limit', '3',
                                    '--include-probes'], store_factory=FixtureStore)
            self.assertEqual(code, 0)
            command = owned['command']
            self.assertEqual(command[0], '/mnt/d/textgen/2026/llama-b12236-bin-win-cuda-13.5-x64/llama-server.exe')
            self.assertEqual(command[command.index('-m') + 1], 'D:/textgen/models/decision/' + filename)
            self.assertEqual(command[command.index('--host') + 1], '127.0.0.1')
            self.assertEqual(command[command.index('--port') + 1], '18874')
            for flag in ('--offline', '--no-agent', '--no-webui'):
                self.assertIn(flag, command)
            alias = command[command.index('--alias') + 1]
            self.assertTrue(alias.startswith('observation-replay-' + key + '-'))
            replay_command = run.call_args.args[0]
            self.assertEqual(replay_command[replay_command.index('--model') + 1], key)
            self.assertEqual(replay_command[replay_command.index('--endpoint') + 1], 'http://127.0.0.1:18874')
            self.assertIn('--include-probes', replay_command)
            self.assertEqual(replay_command[replay_command.index('--limit') + 1], '3')
            self.assertEqual(run.call_args.kwargs['cwd'], module.ROOT)
            kill.assert_called_once()
            self.assertEqual(kill.call_args.args[0], 5309)
            server.wait.assert_called_once_with(timeout=20)
        for path in self.store.path.glob('replay-runs/*/*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_direct_launcher_also_skips_empty_capture_store(self):
        module = self.module()
        with patch.object(module, '_port_free', return_value=False) as probe, \
             patch.object(module.subprocess, 'Popen') as start, contextlib.redirect_stdout(io.StringIO()):
            result = None
            try:
                result = module.run_model(self.store, 'lite')
            except RuntimeError:
                pass
        self.assertEqual(result, 0, 'no entrypoint may start or probe without eligible captures')
        probe.assert_not_called()
        start.assert_not_called()

    def test_no_eligible_capture_does_not_start_or_probe_a_model(self):
        module = self.module()
        self.store.append('calls.jsonl', capture(origin='verification'))
        self.store.append('calls.jsonl', capture(2, origin='other'))
        self.store.append('calls.jsonl', dict(capture(3), payload=None))
        for key in ('lite', 'qev'):
            with patch.object(module.subprocess, 'Popen', side_effect=AssertionError('no startup')), \
                 patch.object(module.subprocess, 'run', side_effect=AssertionError('no subprocess')), \
                 patch.object(module.socket, 'socket', side_effect=AssertionError('no network')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                code = module.main(['--store', str(self.store.path), '--model', key], store_factory=FixtureStore)
            self.assertEqual(code, 0)
            self.assertIn('not started', output.getvalue())
            self.assertNotIn('INERT_PRIVATE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
