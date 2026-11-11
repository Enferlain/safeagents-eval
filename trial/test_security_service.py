"""Synthetic fixtures only; never read the owner's private observation records."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
PYTHON = '/nix/store/ak6rvbv8nzzxdsac1rhkp58vmjy6f86j-hermes-agent-env/bin/python3.14'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.home = Path(self.temp.name) / 'hermes'
        self.store = self.repo / 'trial/private-observation'
        self.store.mkdir(parents=True, mode=0o700)
        self.home.mkdir()
        self.mod = load('security_test_service', ROOT / 'security-plugin/service.py')
        self.service = self.mod.SecurityService(repo_root=self.repo, hermes_home=self.home)

    def write(self, name, rows):
        path = self.store / name
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        path.chmod(0o600)
        return path

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        api = load('security_test_api', ROOT / 'security-plugin/dashboard/plugin_api.py')
        api.service = self.service
        app = FastAPI()
        app.include_router(api.router)
        return TestClient(app)

    def capture(self, origin='traffic'):
        return {'id': '3a31cd31-98fc-4324-94d2-e573796ee37e', 'time': 100.,
                'origin': origin, 'payload': {'secret': 'TOPSECRET'}, 'score': .9,
                'threshold': .88, 'would_block': True, 'error': None,
                'model': {'secret': 'TOPSECRET'}, 'input_sha256': 'a', 'policy_sha256': 'b'}

    def test_events_are_metadata_only_and_unassociated(self):
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        self.write('qev.jsonl', [dict(capture_id=capture['id'], input_sha256='a', policy_sha256='b',
                                      score=.2, error=None, time=101.)])
        self.write('labels.jsonl', [dict(id=capture['id'], label='authorized', note='TOPSECRET')])
        client = self.client()
        result = client.get('/events').json()
        self.assertEqual(result['events'], [])
        self.assertEqual(result['unassociated_count'], 1)
        response = client.get('/events?all_sessions=true')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('TOPSECRET', response.text)
        event = response.json()['events'][0]
        self.assertIsNone(event['session_id'])
        self.assertIsNone(event['tool'])
        self.assertEqual(event['execution'], 'not_intercepted')
        self.assertEqual([(r['role'], r['score']) for r in event['scores']], [('live', .9), ('replay', .2)])
        self.assertTrue(all(r['model']['id'] is None for r in event['scores']))
        self.assertTrue(event['would_flag'])
        self.assertEqual(event['human_label'], 'authorized')
        self.assertIn('unassociated', event['detail'].lower())
        self.assertNotIn('TOPSECRET', client.get('/events/' + capture['id']).text)
        with patch.object(self.service, '_health', return_value=[]):
            status = client.get('/status').json()
        self.assertEqual(status['counts'], dict(traffic_captures=1, verification_captures=0, replay_attempts=1, label_events=1, reviewed_events=1))
        self.assertEqual(status['observation']['state'], 'unknown')

    def test_payloadless_producer_error_does_not_poison_good_captures(self):
        observation = load('security_test_observation', ROOT / 'observation.py')
        store = observation.Store(self.store)
        good = observation.new_capture(
            {'state': {'secret': 'TOPSECRET'}, 'questions': {'block': 'fixture policy'}},
            .9, .88, {'fixture': True})
        failure = observation.new_capture(None, None, .88, {'fixture': True}, error='ValueError')
        for row in (good, failure):
            store.append('calls.jsonl', row)
        self.assertIsNone(failure['input_sha256'])
        self.assertIsNone(failure['policy_sha256'])
        self.assertEqual(self.service.inspect(good['id'], include_content=True), good)
        result = self.service.events(all_sessions=True)
        self.assertTrue(result['healthy'])
        events = {event['id']: event for event in result['events']}
        self.assertEqual(set(events), {good['id'], failure['id']})
        self.assertTrue(events[good['id']]['inspectable'])
        self.assertEqual(events[failure['id']]['status'], 'error')
        self.assertFalse(events[failure['id']]['inspectable'])
        self.assertNotIn('TOPSECRET', json.dumps(result))
        with self.assertRaises(self.mod.SecurityError) as caught:
            self.service.inspect(failure['id'], include_content=True)
        self.assertEqual(caught.exception.status, 400)
        replay = self.service.replay_status()
        self.assertTrue(replay['available'])
        self.assertEqual(replay['state'], 'idle')
        with patch.object(self.service, '_health', return_value=[]):
            status = self.service.status()
        self.assertTrue(status['subsystems']['activity']['healthy'])
        self.assertEqual(status['counts']['traffic_captures'], 2)
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store / 'calls.jsonl').stat().st_mode & 0o777, 0o600)

    def test_null_capture_hashes_require_explicit_payloadless_error(self):
        observation = load('security_test_observation', ROOT / 'observation.py')
        failure = observation.new_capture(None, None, .88, {}, error='ValueError')
        malformed = [dict(failure, payload={}), dict(failure, error=None),
                     dict(failure, error=''), dict(failure, error='TOPSECRET exception text'),
                     dict(failure, error=True), dict(failure, score=.5),
                     dict(failure, input_sha256='a'), dict(failure, policy_sha256='b')]
        for key in ('payload', 'input_sha256', 'policy_sha256'):
            missing = dict(failure)
            del missing[key]
            malformed.append(missing)
        for row in malformed:
            with self.subTest(row=row):
                self.write('calls.jsonl', [self.capture(), row])
                with self.assertRaises(self.mod.SecurityError) as caught:
                    self.service.inspect(self.capture()['id'], include_content=True)
                self.assertEqual(caught.exception.status, 503)
                self.assertFalse(self.service.replay_status()['available'])

    def test_explicit_inspection_and_existing_id_local_label(self):
        import shutil
        for name in ('observation.py', 'observe.py', 'decision_models.py'):
            shutil.copyfile(ROOT / name, self.repo / 'trial' / name)
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        client = self.client()
        cid = capture['id']
        self.assertEqual(client.post('/inspect', json={'id': cid, 'include_content': False}).status_code, 400)
        result = client.post('/inspect', json={'id': cid, 'include_content': True})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), capture)
        self.assertEqual(result.headers['cache-control'], 'no-store')
        with patch('socket.socket.connect', side_effect=AssertionError('labels must be local only')):
            result = client.post('/label', json={'id': cid, 'value': 'unwanted'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.get('/events/' + cid).json()['human_label'], 'unwanted')
        self.assertEqual((self.store / 'labels.jsonl').stat().st_mode & 0o777, 0o600)
        missing = '4a31cd31-98fc-4324-94d2-e573796ee37e'
        self.assertEqual(client.post('/label', json={'id': missing, 'value': 'authorized'}).status_code, 404)
        for bad in ('../calls.jsonl', cid.upper(), cid.replace('-', ''), 'TOPSECRET'):
            response = client.post('/inspect', json={'id': bad, 'include_content': True})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('TOPSECRET', response.text)
        self.assertEqual(client.post('/label', json={'id': cid, 'value': 'deny'}).status_code, 400)
        self.assertEqual(client.post('/label', json={'id': cid, 'value': 'authorized', 'note': 'TOPSECRET'}).status_code, 400)

    def test_replay_fixed_process_singleton_and_private_log(self):
        import concurrent.futures
        import threading
        import time
        self.write('calls.jsonl', [self.capture()])
        # Fixed trusted fixture process proves real Popen lifecycle, not model startup.
        executable = self.repo / '.venv-trial/bin/python'
        executable.parent.mkdir(parents=True)
        executable.symlink_to(PYTHON)
        script = self.repo / 'trial/replay_model.py'
        script.write_text('import time; print("TOPSECRET", flush=True); time.sleep(.4)\n')
        client = self.client()
        self.assertEqual(client.get('/replay').json()['state'], 'idle')
        gate = threading.Barrier(2)
        def start():
            gate.wait()
            return client.post('/replay', json={'model': 'qev'}).status_code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: start(), range(2)))
        self.assertEqual(sorted(results), [202, 409])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get('/replay')
            self.assertNotIn('TOPSECRET', state.text)
            if state.json()['state'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(state.json()['state'], 'completed')
        self.assertEqual(state.json()['exit_code'], 0)
        self.assertIsNotNone(state.json()['finished_at'])
        logs = list(self.store.glob('replay-*.log'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(client.post('/replay', json={'command': 'TOPSECRET'}).status_code, 400)
        script.write_text('raise SystemExit(7)\n')
        self.assertEqual(client.post('/replay', json={'model': 'lite'}).status_code, 202)
        time.sleep(.1)
        self.assertEqual(client.get('/replay').json()['state'], 'failed')

    def test_replay_status_recovers_child_lock_without_inventing_outcome(self):
        import sys
        self.write('calls.jsonl', [self.capture()])
        executable = self.repo / '.venv-trial/bin/python'
        executable.parent.mkdir(parents=True)
        executable.symlink_to(sys.executable)
        (self.repo / 'trial/replay_model.py').write_text('import time; time.sleep(30)\n')
        def stop(process):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        self.service.start_replay('lite')
        child = self.service._process
        self.addCleanup(stop, child)
        owned = self.service.replay_status()
        self.assertEqual(owned['model'], 'lite')
        self.assertIsNotNone(owned['started_at'])
        # Simulate loss of the old backend: only the child's inherited FD remains.
        self.service._job_lock_file.close()
        self.service._job_lock_file = None
        recovered = self.mod.SecurityService(repo_root=self.repo, hermes_home=self.home)
        current = recovered.replay_status()
        self.assertEqual(current['state'], 'running')
        self.assertIsNone(current['model'])
        self.assertIsNone(current['started_at'])
        self.assertIn('unknown', current['detail'].lower())
        with self.assertRaises(self.mod.SecurityError) as caught:
            recovered.start_replay('qev')
        self.assertEqual(caught.exception.status, 409)
        stop(child)
        current = recovered.replay_status()
        self.assertEqual(current['state'], 'idle')
        self.assertIsNone(current['exit_code'])
        self.assertIsNone(current['finished_at'])
        self.assertIn('unknown', current['detail'].lower())
        self.assertEqual(recovered.start_replay('qev')['state'], 'running')
        self.addCleanup(stop, recovered._process)
        self.addCleanup(recovered._job_lock_file.close)

    def test_native_route_evidence_health_and_staleness(self):
        import time
        route = dict(schema=1, session_id='session-a', turn_id='turn-a', api_request_id='req-a',
                     time=time.time(), provider='safeagents-glm', route='safeagents-glm',
                     api_mode='chat_completions', origin='native_hook', host_pid=123)
        self.write('security-routes.jsonl', [route])
        healthy = [dict(id=i, label=i, healthy=True, detail='Available.')
                   for i in ('proxy_glm', 'proxy_local', 'decision', 'capture')]
        with patch.object(self.service, '_health', return_value=healthy):
            result = self.service.status('session-a')
        self.assertEqual(result['observation']['state'], 'observing')
        self.assertIn('last request', result['observation']['detail'].lower())
        self.assertIn(str(route['time']), result['observation']['detail'])
        healthy[0]['healthy'] = False
        with patch.object(self.service, '_health', return_value=healthy):
            self.assertEqual(self.service.status('session-a')['observation']['state'], 'degraded')
        route['route'] = None
        self.write('security-routes.jsonl', [route])
        with patch.object(self.service, '_health', return_value=healthy):
            self.assertEqual(self.service.status('session-a')['observation']['state'], 'not_covered')
        route.update(route='safeagents-glm', time=time.time()-1000)
        self.write('security-routes.jsonl', [route])
        with patch.object(self.service, '_health', return_value=healthy):
            self.assertEqual(self.service.status('session-a')['observation']['state'], 'unknown')
        route.update(time=time.time(), api_mode='responses')
        self.write('security-routes.jsonl', [route])
        with patch.object(self.service, '_health', return_value=healthy):
            self.assertEqual(self.service.status('session-a')['observation']['state'], 'not_covered')

    def test_last_recorded_sqlite_route_never_proves_live_coverage(self):
        with sqlite3.connect(self.home / 'state.db') as db:
            db.execute('CREATE TABLE sessions (id TEXT,billing_provider TEXT,billing_base_url TEXT,model_config TEXT,runtime_revision TEXT)')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       ('session-a', 'safeagents-glm', 'http://127.0.0.1:8788/api/coding/paas/v4', 'TOPSECRET', 'TOPSECRET'))
        db.close()
        with patch.object(self.service, '_health', return_value=[]):
            result = self.service.status('session-a')
        self.assertEqual(result['observation']['state'], 'unknown')
        self.assertEqual(result['observation']['provider'], 'safeagents-glm')
        self.assertIn('last-recorded', result['observation']['detail'])
        self.assertNotIn('TOPSECRET', json.dumps(result))

    def test_health_uses_fixed_local_endpoints_no_proxies_or_redirects(self):
        import httpx
        urls = []
        def handle(request):
            urls.append(str(request.url))
            if request.url.port == 18873:
                return httpx.Response(200, json={'status': 'ok', 'capture_enabled': True, 'capture_errors': 0})
            return httpx.Response(302, headers={'location': 'https://evil.example/TOPSECRET'})
        original = httpx.Client
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://evil.example', 'HTTPS_PROXY': 'http://evil.example'}), \
             patch('socket.create_connection', side_effect=OSError('TOPSECRET')), \
             patch.object(self.mod.httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw)) as factory:
            result = self.service._health()
        self.assertEqual(set(urls), {'http://127.0.0.1:18872/health', 'http://127.0.0.1:18873/health'})
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertFalse(factory.call_args.kwargs['follow_redirects'])
        self.assertEqual({r['id']: r['healthy'] for r in result},
                         dict(proxy_glm=False, proxy_local=False, decision=False, capture=True, recorder=True))
        self.assertNotIn('TOPSECRET', json.dumps(result))

    def test_native_events_merge_and_filter_without_raw_review(self):
        row = dict(schema=1, id=self.capture()['id'], session_id='session-a', turn_id='turn-a',
                   tool_call_id='call-a', time=99., source='hermes', status='proposed',
                   execution='not_confirmed', tool='terminal', origin='traffic', detail='TOPSECRET',
                   reviewable=False, arguments='TOPSECRET')
        self.write('security-events.jsonl', [row, dict(row, status='completed', execution='tool_returned', time=101.)])
        client = self.client()
        result = client.get('/events?session_id=session-a').json()
        self.assertEqual(len(result['events']), 1)
        event = result['events'][0]
        self.assertEqual(event['status'], 'completed')
        self.assertEqual(event['source'], 'hermes')
        self.assertFalse(event['reviewable'])
        self.assertNotIn('TOPSECRET', json.dumps(event))
        self.assertEqual(client.get('/events?session_id=session-b').json()['events'], [])
        self.assertEqual(client.post('/label', json={'id': row['id'], 'value': 'authorized'}).status_code, 400)
        self.assertEqual(client.post('/inspect', json={'id': row['id'], 'include_content': True}).status_code, 400)

    def test_journal_labels_bind_to_content_and_match_cli(self):
        import hashlib
        import shutil
        for name in ('observation.py', 'observe.py', 'decision_models.py'):
            shutil.copyfile(ROOT / name, self.repo / 'trial' / name)
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        journal = self.repo / 'trial/journal.jsonl'
        row = {'id': 1, 'time': 101., 'judge': None, 'verdict': 'flagged', 'arguments': 'FIRST_PRIVATE'}
        journal.write_text(json.dumps(row) + '\n')
        legacy = 'journal:' + hashlib.sha256(str(journal).encode()).hexdigest()[:12] + ':1'
        self.write('labels.jsonl', [{'id': legacy, 'label': 'unwanted'}])
        client = self.client()
        def event():
            return next(e for e in client.get('/events?all_sessions=true').json()['events']
                        if e['id'].startswith('journal:'))
        first = event()
        self.assertNotEqual(first['id'], legacy)
        self.assertIsNone(first['human_label'])
        self.assertEqual(client.post('/label', json={'id': first['id'], 'value': 'authorized'}).status_code, 200)
        cli = load('journal_identity_cli', ROOT / 'observe.py')
        self.assertEqual(cli.journal_rows([journal], [capture])[1][0]['id'], first['id'])
        # Whitespace/key order and later appends do not invalidate the original review.
        journal.write_text(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n'
                           + json.dumps(dict(row, id=2, time=102.)) + '\n')
        self.assertEqual(client.get('/events/' + first['id']).json()['human_label'], 'authorized')
        # Reuse the integer AND timestamp, changing only hidden record content.
        replacement = dict(row, arguments='SECOND_PRIVATE')
        journal.write_text(json.dumps(replacement) + '\n')
        current = event()
        self.assertNotEqual(current['id'], first['id'])
        self.assertIsNone(current['human_label'])
        self.assertEqual(client.get('/events/' + first['id']).status_code, 404)
        self.assertEqual(client.post('/label', json={'id': legacy, 'value': 'authorized'}).status_code, 404)
        self.assertEqual(cli.journal_rows([journal], [capture])[1][0]['id'], current['id'])
        with patch.object(self.service, '_health', return_value=[]):
            counts = client.get('/status').json()['counts']
        self.assertEqual(counts['label_events'], 2)
        self.assertEqual(counts['reviewed_events'], 0)
        self.assertNotIn('PRIVATE', json.dumps(current))

    def test_journal_metadata_probe_filtering_and_limits(self):
        self.write('calls.jsonl', [self.capture('verification')])
        journal = self.repo / 'trial/local-journal.jsonl'
        journal.write_text(json.dumps(dict(id=7, time=101., verdict='flagged', judge=None,
                                          arguments='TOPSECRET', tool='TOPSECRET')) + '\n')
        client = self.client()
        result = client.get('/events?all_sessions=true').json()
        self.assertEqual(len(result['events']), 1)
        event = result['events'][0]
        self.assertRegex(event['id'], r'^journal:[0-9a-f]{12}:7:[0-9a-f]{64}$')
        self.assertTrue(event['would_flag'])
        self.assertEqual(event['scores'], [])
        self.assertIsNone(event['session_id'])
        self.assertNotIn('TOPSECRET', json.dumps(result))
        self.assertEqual(len(client.get('/events?all_sessions=true&include_probes=true').json()['events']), 2)
        self.assertEqual(len(client.get('/events?all_sessions=true&include_probes=true&limit=1').json()['events']), 1)
        self.assertEqual(client.get('/events?limit=0').status_code, 422)
        self.assertEqual(client.get('/events?limit=501').status_code, 422)

    def test_private_failures_symlinks_and_nondefault_profiles_fail_closed(self):
        client = self.client()
        path = self.write('calls.jsonl', [self.capture()])
        path.chmod(0o644)
        response = client.get('/events?all_sessions=true')
        self.assertEqual(response.status_code, 503)
        with patch.object(self.service, '_health', return_value=[]):
            result = client.get('/status').json()
        self.assertEqual(result['observation']['state'], 'unknown')
        self.assertFalse(result['subsystems']['activity']['healthy'])
        self.assertIsNone(result['counts']['traffic_captures'])
        path.chmod(0o600)
        path.write_text('{TOPSECRET invalid JSON\n')
        self.assertEqual(client.get('/events?all_sessions=true').status_code, 503)
        self.assertNotIn('TOPSECRET', client.get('/events?all_sessions=true').text)
        path.unlink()
        external = Path(self.temp.name) / 'external'
        external.write_text('TOPSECRET')
        external.chmod(0o600)
        path.symlink_to(external)
        self.assertEqual(client.get('/events?all_sessions=true').status_code, 503)
        path.unlink()
        self.write('calls.jsonl', [self.capture()])
        (self.store / 'labels.jsonl').symlink_to(external)
        self.assertEqual(client.post('/label', json={'id': self.capture()['id'], 'value': 'authorized'}).status_code, 503)
        (self.store / 'labels.jsonl').unlink()
        moved = self.store.with_name('moved')
        self.store.rename(moved)
        self.store.symlink_to(moved, target_is_directory=True)
        self.assertEqual(client.get('/events?all_sessions=true').status_code, 503)
        self.store.unlink()
        moved.rename(self.store)
        other = self.mod.SecurityService(repo_root=self.repo, hermes_home=self.home / 'profiles/work')
        with self.assertRaises(self.mod.SecurityError):
            other.events(all_sessions=True)
        with patch.object(other, '_health', side_effect=AssertionError('profile must not probe default stack')):
            self.assertEqual(other.status()['observation']['state'], 'not_covered')
        # A nonstandard HERMES_HOME without an explicit test/integration override is also not default.
        with patch.dict(os.environ, {'HERMES_HOME': str(self.home / 'other-home')}):
            implicit = self.mod.SecurityService()
        self.assertFalse(implicit.profile_allowed)

    def test_subsystem_corruption_does_not_hide_live_route(self):
        import time
        self.write('calls.jsonl', [self.capture()])
        self.write('security-routes.jsonl', [dict(session_id='s', origin='native_hook', time=time.time(),
                                                route='safeagents-glm', api_mode='chat')])
        health = [dict(id=i, healthy=True) for i in ('proxy_glm', 'decision', 'capture')]
        for name, subsystem in [('qev.jsonl', 'replay'), ('labels.jsonl', 'review'), ('calls.jsonl', 'activity')]:
            with self.subTest(name=name):
                path = self.write(name, [dict(self.capture(), capture_id=[], label=[])])
                path.write_text('{TOPSECRET invalid\n')
                with patch.object(self.service, '_health', return_value=health):
                    result = self.client().get('/status?session_id=s')
                self.assertEqual(result.status_code, 200)
                data = result.json()
                self.assertEqual(data['observation']['state'], 'observing')
                self.assertFalse(data['subsystems'][subsystem]['healthy'])
                self.assertNotIn('TOPSECRET', result.text)
                response = self.client().get('/events?all_sessions=true')
                self.assertEqual(response.status_code, 503 if subsystem == 'activity' else 200)
                path.unlink()
                self.write('calls.jsonl', [self.capture()])

    def test_wrong_types_are_sanitized_and_qev_replay_unavailable(self):
        self.write('calls.jsonl', [self.capture()])
        client = self.client()
        for name, row, subsystem in [
            ('qev.jsonl', dict(capture_id=[], input_sha256={}, policy_sha256='b'), 'replay'),
            ('labels.jsonl', dict(id=[], label={}), 'review'),
            ('calls.jsonl', dict(self.capture(), input_sha256=[]), 'activity'),
            ('security-events.jsonl', dict(id=self.capture()['id'], time=1, status=[]), 'activity')]:
            with self.subTest(name=name):
                path = self.write(name, [row])
                with patch.object(self.service, '_health', return_value=[]):
                    result = client.get('/status')
                self.assertEqual(result.status_code, 200)
                self.assertFalse(result.json()['subsystems'][subsystem]['healthy'])
                if subsystem == 'replay':
                    self.assertFalse(client.get('/replay').json()['available'])
                    self.assertEqual(client.post('/replay', json={'model': 'qev'}).status_code, 503)
                path.unlink()
                self.write('calls.jsonl', [self.capture()])

    def test_inspect_http_bound_review_counts_and_capabilities(self):
        capture = self.capture()
        capture['payload'] = {'nested': [(chr(0) + '😀') * 100000]}
        self.write('calls.jsonl', [capture])
        self.write('labels.jsonl', [dict(id=capture['id'], label=value)
                                   for value in ('authorized', 'unwanted')])
        client = self.client()
        with patch.object(self.service, '_health', return_value=[]):
            counts = client.get('/status').json()['counts']
        self.assertEqual(counts['label_events'], 2)
        self.assertEqual(counts['reviewed_events'], 1)
        response = client.post('/inspect', json={'id': capture['id'], 'include_content': True})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.content), 262144)
        self.assertTrue(response.json()['truncated'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        event = client.get('/events/' + capture['id']).json()
        self.assertTrue(event['inspectable'])
        self.assertTrue(event['reviewable'])
        journal = self.repo / 'trial/journal.jsonl'
        journal.write_text(json.dumps(dict(id=1, time=2, judge=None, verdict='flagged')) + '\n')
        event = next(e for e in client.get('/events?all_sessions=true').json()['events'] if e['id'].startswith('journal:'))
        self.assertTrue(event['reviewable'])
        self.assertFalse(event['inspectable'])

    def test_native_execution_and_shared_identifiers(self):
        client = self.client()
        for status, execution, expected in [('blocked', 'tool_returned', 'blocked'),
                                             ('cancelled', 'tool_returned', 'cancelled'),
                                             ('error', 'tool_returned', 'error'),
                                             ('completed', 'tool_returned', 'tool_returned'),
                                             ('proposed', 'proposed', 'proposed')]:
            row = dict(id=self.capture()['id'], time=1, status=status, execution=execution,
                       session_id='session/valid', tool_call_id='x' * 200, api_request_id='api/req',
                       turn_id=' invalid ', tool='terminal')
            self.write('security-events.jsonl', [row])
            result = client.get('/events?session_id=session/valid')
            self.assertEqual(result.status_code, 200)
            event = result.json()['events'][0]
            self.assertEqual(event['execution'], expected)
            self.assertEqual(event['api_request_id'], 'api/req')
            self.assertEqual(event['tool_call_id'], 'x' * 200)
            self.assertIsNone(event['turn_id'])
            self.assertFalse(event['inspectable'])

    def test_no_store_on_success_and_all_error_paths(self):
        client = self.client()
        for response in (client.get('/events'), client.get('/events?limit=0'),
                         client.post('/inspect', json={'id': [], 'include_content': True}),
                         client.post('/label', json={'id': 'bad', 'value': 'deny'})):
            self.assertEqual(response.headers.get('cache-control'), 'no-store')

    def test_inspection_ignores_secondary_corruption_and_bounds_metadata(self):
        capture = self.capture()
        capture['model'] = (chr(0) + '😀') * 100000
        self.write('calls.jsonl', [capture])
        self.write('labels.jsonl', [dict(id=[], label='unwanted')])
        (self.repo / 'trial/journal.jsonl').write_text('{TOPSECRET bad\n')
        response = self.client().post('/inspect', json={'id': capture['id'], 'include_content': True})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.content), 262144)
        self.assertTrue(response.json()['truncated'])

    def test_journal_labels_remain_local_and_missing_counts_unknown(self):
        import shutil
        for name in ('observation.py', 'observe.py', 'decision_models.py'):
            shutil.copyfile(ROOT / name, self.repo / 'trial' / name)
        (self.repo / 'trial/journal.jsonl').write_text(json.dumps(dict(id=1, time=2, judge=None, verdict='flagged')) + '\n')
        client = self.client()
        event = client.get('/events?all_sessions=true').json()['events'][0]
        self.assertEqual(client.post('/label', json={'id': event['id'], 'value': 'authorized'}).status_code, 200)
        with patch.object(self.service, '_health', return_value=[]):
            counts = client.get('/status').json()['counts']
        self.assertEqual(counts['reviewed_events'], 1)
        self.assertIsNone(counts['traffic_captures'])
        self.assertIsNone(counts['replay_attempts'])

    def test_manifest_declares_only_plugin_api(self):
        manifest = json.loads((ROOT / 'security-plugin/dashboard/manifest.json').read_text())
        self.assertEqual(manifest['name'], 'local-agent-security')
        self.assertEqual(manifest['api'], 'plugin_api.py')

    def test_corrupt_journal_status_is_degraded_and_qev_hashes_must_match(self):
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        self.write('qev.jsonl', [dict(capture_id=capture['id'], input_sha256='wrong', policy_sha256='b',
                                      score=.1, error=None)])
        event = self.client().get('/events/' + capture['id']).json()
        self.assertEqual(len(event['scores']), 1)
        self.assertEqual(event['scores'][0]['role'], 'live')
        (self.repo / 'trial/journal.jsonl').write_text('{TOPSECRET invalid\n')
        with patch.object(self.service, '_health', return_value=[]):
            result = self.service.status()
        self.assertEqual(result['observation']['state'], 'unknown')
        self.assertFalse(result['subsystems']['activity']['healthy'])
        self.assertEqual(result['counts']['traffic_captures'], 1)
        self.assertTrue(result['components'][0]['healthy'])
        self.assertNotIn('TOPSECRET', json.dumps(result))

    def test_status_missing_is_degraded_not_healthy_zero(self):
        self.store.rmdir()
        with patch.object(self.service, '_health', return_value=[]):
            result = self.service.status()
        self.assertEqual(result['observation']['state'], 'unknown')
        self.assertFalse(result['subsystems']['activity']['healthy'])
        self.assertIsNone(result['counts']['traffic_captures'])
        self.assertEqual(result['enforcement']['state'], 'not_enabled')
        self.assertEqual(result['pending'], [])
        self.assertFalse(result['components'][0]['healthy'])


if __name__ == '__main__':
    unittest.main()
