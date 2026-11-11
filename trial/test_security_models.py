"""Model-selection contracts exercised only with inert, private fixtures."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.home = Path(self.temp.name) / 'hermes'
        self.store = self.repo / 'trial/private-observation'
        self.store.mkdir(parents=True, mode=0o700)
        self.home.mkdir()
        self.module = load('security_model_contract_service', ROOT / 'security-plugin/service.py')
        self.service = self.module.SecurityService(self.repo, self.home)

    def write(self, name, rows):
        path = self.store / name
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        path.chmod(0o600)
        return path

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        api = load('security_model_contract_api', ROOT / 'security-plugin/dashboard/plugin_api.py')
        api.service = self.service
        app = FastAPI()
        app.include_router(api.router)
        return TestClient(app)

    def test_replay_requires_an_explicit_allowlisted_model(self):
        client = self.client()
        with patch.object(self.module.subprocess, 'Popen') as launch:
            for payload in (None, {}, {'model': 'unknown'}, {'model': None}, {'model': []},
                            {'model': '../qev'}, {'model': 'qev', 'endpoint': 'https://example.invalid'}):
                with self.subTest(payload=payload):
                    response = client.post('/replay', json=payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.headers.get('cache-control'), 'no-store')
            launch.assert_not_called()

    def test_both_model_choices_reach_the_fixed_runner_and_job_identity(self):
        import sys
        import time
        executable = self.repo / '.venv-trial/bin/python'
        executable.parent.mkdir(parents=True)
        executable.symlink_to(sys.executable)
        (self.repo / 'trial/replay_model.py').write_text(
            'import argparse\np=argparse.ArgumentParser()\np.add_argument("--model",required=True)\n'
            'a=p.parse_args()\nprint("selected="+a.model,flush=True)\n', encoding='utf-8')
        client = self.client()
        for model in ('lite', 'qev'):
            with self.subTest(model=model):
                response = client.post('/replay', json={'model': model})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()['model'], model)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    state = client.get('/replay').json()
                    if state['state'] != 'running':
                        break
                    time.sleep(.01)
                self.assertEqual(state['state'], 'completed')
                self.assertEqual(state['model'], model)
        logs = [p.read_text() for p in self.store.glob('replay-*.log')]
        self.assertEqual(set(logs), {'selected=lite\n', 'selected=qev\n'})
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.store.glob('replay-*.log')))
        options = client.get('/replay').json()['models']
        self.assertEqual(options, [{'id': 'lite', 'label': 'D3 Lite'}, {'id': 'qev', 'label': 'QEV'}])

    def provenance(self, key, digest='a'):
        filename = {'lite': 'vllm-d3-lite-Q8_0.gguf', 'qev': 'qev-0.8b-text-Q8_0.gguf'}[key]
        return {'model_path': 'D:/textgen/models/decision/' + filename,
                'model_file_sha256': digest * 64, 'native_template_sha256': 'b' * 64,
                'private_extra': 'TOPSECRET'}

    def capture(self, model='qev'):
        return {'id': '3a31cd31-98fc-4324-94d2-e573796ee37e', 'time': 100.,
                'origin': 'traffic', 'payload': {'secret': 'TOPSECRET'}, 'score': .8,
                'threshold': .88, 'would_block': False, 'error': None,
                'model': self.provenance(model), 'input_sha256': 'input', 'policy_sha256': 'policy'}

    def test_source_qev_and_replayed_lite_keep_their_actual_identities(self):
        capture = self.capture('qev')
        self.write('calls.jsonl', [capture])
        self.write('replays.jsonl', [{'capture_id': capture['id'], 'input_sha256': 'input',
                    'policy_sha256': 'policy', 'time': 101., 'model_key': 'lite', 'model': self.provenance('lite'),
                    'score': .9, 'threshold': .88, 'error': None}])
        event = self.client().get('/events?all_sessions=true').json()['events'][0]
        self.assertIn('scores', event)
        self.assertNotIn('lite_score', event)
        self.assertNotIn('qev_score', event)
        self.assertEqual([(r['role'], r['model']['id'], r['score']) for r in event['scores']],
                         [('live', 'qev', .8), ('replay', 'lite', .9)])
        self.assertTrue(all(r['provisional'] for r in event['scores']))
        self.assertFalse(event['would_flag'])
        self.assertNotIn('TOPSECRET', json.dumps(event))
        with patch.object(self.service, '_health', return_value=[]):
            status = self.service.status()
        self.assertIn('replay', status['subsystems'])
        self.assertNotIn('qev', status['subsystems'])
        self.assertEqual(status['counts']['replay_attempts'], 1)
        self.assertTrue(status['subsystems']['replay']['healthy'])
        self.assertIn('records', status['subsystems']['replay']['detail'].lower())

    def test_successful_retry_replaces_failed_replay_despite_runtime_changes(self):
        for model in ('lite', 'qev'):
            with self.subTest(model=model):
                capture = self.capture()
                self.write('calls.jsonl', [capture])
                failed = {'capture_id': capture['id'], 'input_sha256': 'input',
                          'policy_sha256': 'policy', 'time': 101., 'model_key': model,
                          'model': dict(self.provenance(model), model_id='first-launch',
                                        base_url='http://127.0.0.1:18874'),
                          'score': None, 'threshold': .88, 'error': 'ConnectionError'}
                successful = dict(failed, time=102., score=.9, error=None,
                                  model=dict(self.provenance(model), model_id='retry-launch',
                                             base_url='http://127.0.0.1:18875'))
                self.write('replays.jsonl', [failed, successful])
                response = self.client().get('/events?all_sessions=true')
                self.assertEqual(response.status_code, 200)
                event = response.json()['events'][0]
                self.assertEqual(event['status'], 'observed')
                self.assertEqual([(r['role'], r['model']['id'], r['score'], r['error'], r['time'])
                                  for r in event['scores']],
                                 [('live', 'qev', .8, False, 100.),
                                  ('replay', model, .9, False, 102.)])

    def test_replays_preserve_distinct_weight_and_template_identities(self):
        for model in ('lite', 'qev'):
            with self.subTest(model=model):
                capture = self.capture()
                self.write('calls.jsonl', [capture])
                provenances = [self.provenance(model), self.provenance(model, digest='c'),
                               dict(self.provenance(model), native_template_sha256='d' * 64)]
                self.write('replays.jsonl', [
                    {'capture_id': capture['id'], 'input_sha256': 'input',
                     'policy_sha256': 'policy', 'time': 101. + index, 'model_key': model,
                     'model': dict(provenance, model_id='same-launch',
                                   base_url='http://127.0.0.1:18874'),
                     'score': None if index == 0 else .9, 'threshold': .88,
                     'error': 'ConnectionError' if index == 0 else None}
                    for index, provenance in enumerate(provenances)])
                response = self.client().get('/events?all_sessions=true')
                self.assertEqual(response.status_code, 200)
                event = response.json()['events'][0]
                replays = [r for r in event['scores'] if r['role'] == 'replay']
                self.assertEqual(len(replays), 3)
                self.assertEqual({(r['model']['id'], r['model']['model_file_sha256'],
                                   r['model']['native_template_sha256'], r['score'], r['error'])
                                  for r in replays},
                                 {(model, 'a' * 64, 'b' * 64, None, True),
                                  (model, 'c' * 64, 'b' * 64, .9, False),
                                  (model, 'a' * 64, 'd' * 64, .9, False)})
                self.assertEqual(event['status'], 'error')

    def test_live_judge_identity_is_checked_against_the_current_backend(self):
        import time

        import httpx
        capture_model = self.provenance('qev')
        current_model = [capture_model['model_path']]
        urls = []
        def handle(request):
            urls.append(str(request.url))
            if request.url.port == 18873:
                return httpx.Response(200, json={'status': 'ok', 'capture_enabled': True,
                    'capture_errors': 0, 'threshold': .88, 'model': capture_model, 'secret': 'TOPSECRET'})
            if request.url.path == '/props':
                return httpx.Response(200, json={'model_path': current_model[0], 'secret': 'TOPSECRET'})
            return httpx.Response(200, json={'status': 'ok'})
        original = httpx.Client
        self.write('security-routes.jsonl', [{'session_id': 'fixture', 'origin': 'native_hook',
                    'time': time.time(), 'route': 'safeagents-glm', 'api_mode': 'chat'}])
        with patch('socket.create_connection'), patch.object(self.module.httpx, 'Client',
                    side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw)), \
             patch.object(self.service, '_monitoring', return_value={
                        'state': 'verified', 'scope': 'current_managed_run', 'run': None, 'policy': None,
                        'guard': {}, 'judge': {}, 'records': [], 'journal_path': None}):
            available = self.service.status('fixture')
            self.assertIn('judge', available)
            self.assertEqual(available['judge']['state'], 'available')
            self.assertEqual(available['judge']['model']['id'], 'qev')
            self.assertEqual(available['judge']['threshold'], .88)
            self.assertEqual(available['observation']['state'], 'observing')
            current_model[0] = self.provenance('lite')['model_path']
            mismatch = self.service.status('fixture')
            self.assertEqual(mismatch['judge']['state'], 'mismatch')
            self.assertEqual(mismatch['observation']['state'], 'degraded')
            current_model[0] = 'D:/private/TOPSECRET.gguf'
            unknown = self.service.status('fixture')
            self.assertEqual(unknown['judge']['state'], 'unknown')
        self.assertEqual(set(urls), {'http://127.0.0.1:18872/health',
                    'http://127.0.0.1:18872/props', 'http://127.0.0.1:18873/health'})
        self.assertNotIn('TOPSECRET', json.dumps([available, mismatch, unknown]))

    def test_trusted_review_module_loads_without_trial_on_sys_path(self):
        import shutil
        import subprocess
        import sys
        for name in ('observe.py', 'decision_models.py'):
            shutil.copyfile(ROOT / name, self.repo / 'trial' / name)
        self.write('calls.jsonl', [self.capture()])
        script = '''import importlib.util, json, sys
from pathlib import Path
before = list(sys.path)
spec = importlib.util.spec_from_file_location('isolated_security', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
service = module.SecurityService(repo_root=sys.argv[2], hermes_home=sys.argv[3])
trusted = service._load_trusted('observe')
assert callable(trusted.label)
capture_id = '3a31cd31-98fc-4324-94d2-e573796ee37e'
result = service.label(capture_id, 'authorized')
assert result == {'mode': 'log-only', 'id': capture_id, 'label': 'authorized'}
written = [json.loads(line) for line in (service.store_path / 'labels.jsonl').read_text().splitlines()]
assert len(written) == 1 and written[0]['id'] == capture_id and written[0]['label'] == 'authorized'
assert before == sys.path
print('trusted import isolated: OK')
'''
        result = subprocess.run([sys.executable, '-I', '-c', script,
                str(ROOT / 'security-plugin/service.py'), str(self.repo), str(self.home)],
                cwd=self.temp.name, capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'trusted import isolated: OK')


if __name__ == '__main__':
    unittest.main()
