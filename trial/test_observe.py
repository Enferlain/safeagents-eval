"""Inert local fixtures only; no services, journals, or private records."""
import contextlib
import hashlib
import importlib
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ENDPOINT_BAD = 'http://example.invalid:18874'


def load_observe():
    try:
        return importlib.import_module('observe')
    except ModuleNotFoundError as exc:
        if exc.name != 'observe':
            raise
        return None


class FixtureStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def append(self, name, row):
        with (self.path / name).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        (self.path / name).chmod(0o600)

    def read(self, name):
        path = self.path / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def write_text(self, name, text):
        (self.path / name).write_text(text, encoding='utf-8')
        (self.path / name).chmod(0o600)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def capture(number=1, score=.2, origin='traffic'):
    payload = {'state': {'task': 'INERT_PRIVATE_TASK', 'arguments': {'text': 'INERT_PRIVATE_ARGUMENT'}},
               'questions': {'block': {'type': 'noul', 'instructions': 'inert policy'}}}
    return {'id': f'00000000-0000-4000-8000-{number:012d}', 'time': 100. + number,
            'payload': payload, 'input_sha256': digest(payload), 'policy_sha256': digest(payload['questions']),
            'score': score, 'threshold': .88, 'would_block': score >= .88 if score is not None else None,
            'model': provenance('lite'), 'error': None, 'origin': origin}


def provenance(key='qev', **extra):
    filenames = {'qev': 'qev-0.8b-text-Q8_0.gguf', 'lite': 'vllm-d3-lite-Q8_0.gguf'}
    return dict({'model_path': 'D:/textgen/models/decision/' + filenames[key],
                 'model_file_sha256': ('a' if key == 'lite' else 'b') * 64,
                 'native_template_sha256': 'c' * 64}, **extra)


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'], prefix='observe-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.store = FixtureStore(Path(self.tmp.name) / 'store')

    def test_show_requires_explicit_local_content_opt_in(self):
        module=self.module()
        row=capture()
        self.store.append('calls.jsonl',row)
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=module.main(['--store',str(self.store.path),'show',row['id']],store_factory=FixtureStore)
        self.assertEqual(code,1)
        self.assertNotIn('INERT_PRIVATE_TASK',output.getvalue())
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(io.StringIO()):
            code=module.main(['--store',str(self.store.path),'show',row['id'],'--include-content'],store_factory=FixtureStore)
        self.assertEqual(code,0)
        self.assertIn('INERT_PRIVATE_TASK',output.getvalue())

    def module(self):
        module = load_observe()
        self.assertIsNotNone(module, 'local observation CLI must exist')
        return module

    def test_explicit_selection_records_two_models_separately(self):
        import inspect
        module = self.module()
        self.assertIn('model_key', inspect.signature(module.replay).parameters,
                      'replay must require an explicit trusted model selection')
        row = capture()
        self.store.append('calls.jsonl', row)
        before = (self.store.path / 'calls.jsonl').read_bytes()
        self.store.append('qev.jsonl', {'legacy': 'INERT_PRIVATE_UNKNOWN'})
        legacy_before = (self.store.path / 'qev.jsonl').read_bytes()
        for key, filename in [('lite', 'vllm-d3-lite-Q8_0.gguf'), ('qev', 'qev-0.8b-text-Q8_0.gguf')]:
            model = {'model_path': 'D:/textgen/models/decision/' + filename,
                     'model_file_sha256': ('a' if key == 'lite' else 'b') * 64,
                     'native_template_sha256': 'c' * 64,
                     'model_id': 'INERT_PRIVATE_ALIAS', 'server_build': {'build': 1}}
            seen = []
            def request(url, payload=None, model=model, seen=seen):
                if url.endswith('/props'):
                    return {'model_path': model['model_path'].replace('/', '\\')}
                seen.append(payload)
                return {'answers': {'block': {'noul': .95}}}
            self.assertEqual(module.replay(self.store, model_key=key, request=request,
                             model_provider=lambda url, model=model: model)['scored'], 1)
            self.assertEqual(seen, [row['payload']])
            self.assertEqual(module.replay(self.store, model_key=key, request=request,
                             model_provider=lambda url, model=model: dict(model, model_id='different-alias'))['scored'], 0)
        saved = self.store.read('replays.jsonl')
        self.assertEqual([r['model_key'] for r in saved], ['lite', 'qev'])
        self.assertTrue(all(r['same_cutoff_provisional'] for r in saved))
        self.assertTrue(all(r['threshold_source'] == 'captured-provisional' for r in saved))
        self.assertEqual(saved[-1]['model'], model)
        self.assertEqual((self.store.path / 'calls.jsonl').read_bytes(), before)
        self.assertEqual((self.store.path / 'qev.jsonl').read_bytes(), legacy_before)
        with self.assertRaises(TypeError):
            module.replay(self.store, request=lambda *a: self.fail('must not contact backend'))

    def test_replay_preserves_payload_skips_probes_is_idempotent(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'replay'), 'offline replay must exist')
        row = capture()
        self.store.append('calls.jsonl', row)
        self.store.append('calls.jsonl', capture(2, origin='verification'))
        requests = []
        def request(url, payload=None):
            requests.append((url, payload))
            if url.endswith('/props'):
                return {'model_path': 'D:/textgen/models/decision/qev-0.8b-text-Q8_0.gguf'}
            return {'answers': {'block': {'noul': .95}}}
        info = lambda url: provenance(identity='qev-fixture')
        result = module.replay(self.store, request=request, model_provider=info, model_key='qev')
        self.assertEqual(result['scored'], 1)
        self.assertEqual(requests[1], ('http://127.0.0.1:18874/v1/systemone', row['payload']))
        saved = self.store.read('replays.jsonl')[0]
        self.assertEqual(saved['capture_id'], row['id'])
        self.assertEqual(saved['input_sha256'], row['input_sha256'])
        self.assertEqual(saved['policy_sha256'], row['policy_sha256'])
        self.assertEqual(saved['threshold'], .88)
        self.assertTrue(saved['same_cutoff_provisional'])
        self.assertTrue(saved['would_block'])
        self.assertEqual(saved['model']['identity'], 'qev-fixture')
        self.assertNotIn('INERT_PRIVATE', json.dumps(saved))
        self.assertEqual(module.replay(self.store, request=request, model_provider=info, model_key='qev')['scored'], 0)
        self.assertEqual(len(self.store.read('replays.jsonl')), 1)
        self.assertEqual(module.replay(self.store, include_probes=True, request=request, model_provider=info, model_key='qev')['scored'], 1)

    def test_replay_rejects_remote_wrong_model_tampering_and_invalid_scores(self):
        module = self.module()
        row = capture()
        self.store.append('calls.jsonl', row)
        def request(url, payload=None):
            return {'model_path': 'D:/textgen/models/decision/qev-0.8b-text-Q8_0.gguf'} if url.endswith('/props') else {'answers': {'block': {'noul': .5}}}
        info = lambda url: provenance(identity='fixture')
        for endpoint in ['http://localhost:18874', 'https://127.0.0.1:18874', ENDPOINT_BAD, 'http://127.0.0.1:18874/']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                module.replay(self.store, endpoint=endpoint, request=request, model_provider=info, model_key='qev')
        with self.assertRaises(ValueError):
            module.replay(self.store, model_key='qev', request=lambda *a: {'model_path': '/models/not-qev.gguf'}, model_provider=info)
        row['payload']['state']['task'] = 'tampered inert data'
        bad = FixtureStore(Path(self.tmp.name) / 'tampered')
        bad.append('calls.jsonl', row)
        with self.assertRaises(ValueError):
            module.replay(bad, request=request, model_provider=info, model_key='qev')
        for score in [True, None, '0.5', -1, 1.1, float('nan'), float('inf')]:
            def invalid(url, payload=None):
                return request(url) if url.endswith('/props') else {'answers': {'block': {'noul': score}}}
            with self.subTest(score=score), self.assertRaises(ValueError):
                module.replay(self.store, model_key='qev', request=invalid, model_provider=info)
        self.assertEqual(sum(r['score'] is not None for r in self.store.read('replays.jsonl')), 0)

    def test_replay_network_failure_preserves_progress_and_type_only_error(self):
        module = self.module()
        self.store.append('calls.jsonl', capture())
        self.store.append('calls.jsonl', capture(2))
        def request(url, payload=None):
            if url.endswith('/props'):
                return {'model_path': 'D:/textgen/models/decision/qev-0.8b-text-Q8_0.gguf'}
            if len(self.store.read('replays.jsonl')) == 1:
                raise ConnectionError('INERT_PRIVATE_HTTP_BODY')
            return {'answers': {'block': {'noul': .7}}}
        with self.assertRaises(ConnectionError):
            module.replay(self.store, model_key='qev', request=request, model_provider=lambda url: provenance(identity='fixture'))
        saved = self.store.read('replays.jsonl')
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]['score'], .7)
        self.assertEqual(saved[1]['error'], 'ConnectionError')
        self.assertIsNone(saved[1]['score'])
        self.assertNotIn('INERT_PRIVATE', json.dumps(saved))

    def test_dedupe_requires_successful_matching_policy_and_verified_model(self):
        module = self.module()
        row = capture()
        base = {'capture_id': row['id'], 'input_sha256': row['input_sha256'],
                'policy_sha256': row['policy_sha256'], 'score': .4, 'error': None, 'model': provenance()}
        cases = [('verified-legacy', 'qev.jsonl', base, 0),
                 ('unknown-legacy', 'qev.jsonl', dict(base, model={'model_id': 'qev'}), 1),
                 ('key-mismatch', 'replays.jsonl', dict(base, model_key='lite'), 1),
                 ('missing-score', 'qev.jsonl', dict(base, score=None), 1),
                 ('bad-score', 'qev.jsonl', dict(base, score=True), 1),
                 ('prior-error', 'replays.jsonl', dict(base, error='ConnectionError', score=None), 1),
                 ('different-policy', 'qev.jsonl', dict(base, policy_sha256='d' * 64), 1),
                 ('different-input', 'qev.jsonl', dict(base, input_sha256='d' * 64), 1),
                 ('different-weights', 'qev.jsonl', dict(base, model=provenance(model_file_sha256='d' * 64)), 1),
                 ('different-template', 'qev.jsonl', dict(base, model=provenance(native_template_sha256='d' * 64)), 1),
                 ('unhashed', 'qev.jsonl', dict(base, model=provenance(model_file_sha256=None)), 1)]
        for name, filename, prior, expected in cases:
            with self.subTest(case=name):
                store = FixtureStore(Path(self.tmp.name) / name)
                store.append('calls.jsonl', row)
                store.append(filename, prior)
                seen = []
                def request(url, payload=None, seen=seen):
                    if url.endswith('/props'):
                        return {'model_path': provenance()['model_path']}
                    seen.append(payload)
                    return {'answers': {'block': {'noul': .7}}}
                actual = module.replay(store, model_key='qev', request=request,
                                       model_provider=lambda url: provenance())
                self.assertEqual(actual['scored'], expected)
                self.assertEqual(len(seen), expected)

    def test_report_uses_actual_source_and_replay_identities(self):
        module = self.module()
        one = capture()
        one['model'] = provenance('qev', model_id='INERT_PRIVATE_SOURCE_ALIAS')
        two = capture(2)
        two['model'] = {'model_id': 'lite', 'model_path': 'INERT_PRIVATE_MODEL_PATH'}
        for row in (one, two):
            self.store.append('calls.jsonl', row)
        for key, score in [('lite', .95), ('qev', .4)]:
            self.store.append('replays.jsonl', {
                'capture_id': one['id'], 'input_sha256': one['input_sha256'],
                'policy_sha256': one['policy_sha256'], 'score': score, 'error': None,
                'model_key': key, 'model': provenance(key, model_id='INERT_PRIVATE_REPLAY_ALIAS')})
        self.store.append('qev.jsonl', {
            'capture_id': two['id'], 'input_sha256': two['input_sha256'],
            'policy_sha256': two['policy_sha256'], 'score': .99, 'error': None,
            'model': {'model_id': 'qev', 'private': 'INERT_PRIVATE_UNKNOWN'}})
        summary = module.report(self.store)
        self.assertEqual(summary.get('replay_attempts'), 3, 'status must count all model-neutral replay attempts')
        self.assertNotIn('qev_attempts', summary)
        text = (self.store.path / 'review.md').read_text()
        self.assertIn('| Source model |', text)
        self.assertIn('| Replay model |', text)
        self.assertIn('Delta Replay-Source', text)
        self.assertIn('| QEV | D3 Lite |', text)
        self.assertIn('| QEV | QEV |', text)
        self.assertIn('| Unknown model | - |', text)
        self.assertIn('captured provisional cutoff', text)
        self.assertNotIn('0.990', text)
        self.assertNotIn('INERT_PRIVATE', text)
        self.assertEqual(text.count(one['id']), 2)
        self.assertIn('Source weights SHA-256', text)
        self.assertIn('Replay weights SHA-256', text)
        self.assertIn(provenance('lite')['model_file_sha256'], text)

    def test_report_labels_priority_journal_scope_and_redaction(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'report'), 'private report must exist')
        one, two, probe = capture(), capture(2, .91), capture(3, origin='verification')
        for row in [one, two, probe]:
            self.store.append('calls.jsonl', row)
        for row, p in [(one, .95), (two, .9)]:
            self.store.append('qev.jsonl', {'capture_id': row['id'], 'input_sha256': row['input_sha256'],
                              'policy_sha256': row['policy_sha256'], 'score': p, 'error': None, 'model': provenance()})
        module.label(self.store, one['id'], 'unwanted', note='INERT_PRIVATE_LABEL_NOTE')
        module.label(self.store, one['id'], 'authorized')
        journal = Path(self.tmp.name) / 'inert-journal.jsonl'
        base = {'time': 104., 'verdict': 'flagged', 'tool': 'INERT_PRIVATE_TOOL', 'arguments': 'INERT_PRIVATE_ARGS',
                'reason': 'INERT_PRIVATE_REASON', 'signals': [], 'model': 'fixture', 'task': 'INERT_PRIVATE_TASK'}
        entries = [dict(base, id=1, judge=None, time=1.), dict(base, id=2, judge=None),
                   dict(base, id=3, judge='block: observation_id=' + one['id']),
                   dict(base, id=4, judge='error: INERT_PRIVATE_ERROR'), dict(base, id=5, judge='block: legacy'),
                   dict(base, id=6, judge='block: observation_id=00000000-0000-4000-8000-000000000099')]
        journal.write_text(''.join(json.dumps(row) + '\n' for row in entries))
        stable_id = ('journal:' + hashlib.sha256(str(journal.resolve()).encode()).hexdigest()[:12]
                     + ':2:' + module.canonical_hash(entries[1]))
        module.label(self.store, stable_id, 'insufficient-context', note='INERT_PRIVATE_NOTE')
        before = journal.read_bytes()
        result = module.report(self.store, journals=[journal], gap=.3)
        text = (self.store.path / 'review.md').read_text()
        self.assertEqual(result['deterministic_flags'], 1)
        self.assertEqual(result['journal_historical_omitted'], 1)
        self.assertEqual(result['journal_matched'], 1)
        self.assertEqual(result['journal_failures'], 1)
        self.assertEqual(result['journal_legacy'], 1)
        self.assertEqual(result['journal_unmatched'], 1)
        self.assertEqual(before, journal.read_bytes())
        self.assertIn('| journal_matched | ' + one['id'] + ' |', text)
        self.assertIn(stable_id, text)
        self.assertIn('insufficient-context', text)
        self.assertIn('authorized', text)
        self.assertNotIn('unwanted', text)
        self.assertNotIn('INERT_PRIVATE', text)
        self.assertLess(text.index(one['id']), text.index(two['id']))
        for phrase in ['log-only', 'not calibrated', 'heuristic', 'verification', '101', '0.300', 'provisional']:
            self.assertIn(phrase, text)
        self.assertEqual((self.store.path / 'review.md').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            module.label(self.store, one['id'], 'block')

    def test_replay_cli_requires_model_and_forwards_selection(self):
        module = self.module()
        for args in (['replay'], ['replay', '--model', 'INERT_PRIVATE_MODEL']):
            with patch.object(module, '_install_safety') as safety, patch.object(module, 'replay') as replay:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    code = module.main(['--store', str(self.store.path)] + args, store_factory=FixtureStore)
                self.assertEqual(code, 1)
                self.assertNotIn('INERT_PRIVATE', output.getvalue())
                safety.assert_not_called()
                replay.assert_not_called()
        for key in ('lite', 'qev'):
            with patch.object(module, '_install_safety'), patch.object(module, 'replay', return_value={'scored': 0}) as replay:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = module.main(['--store', str(self.store.path), 'replay', '--model', key,
                                        '--limit', '2', '--include-probes'], store_factory=FixtureStore)
                self.assertEqual(code, 0)
                self.assertEqual(replay.call_args.kwargs['model_key'], key)

    def test_cli_commands_and_errors_never_print_payloads_or_notes(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'main'), 'command-line entrypoint must exist')
        self.store.append('calls.jsonl', capture())
        def run(args):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = module.main(['--store', str(self.store.path)] + args, store_factory=FixtureStore)
            self.assertNotIn('INERT_PRIVATE', output.getvalue())
            return code, output.getvalue()
        self.assertEqual(run(['status'])[0], 0)
        self.assertEqual(run(['label', capture()['id'], 'authorized', '--note', 'INERT_PRIVATE_NOTE'])[0], 0)
        missing = Path(self.tmp.name) / 'missing-inert-journal.jsonl'
        self.assertEqual(run(['report', '--journal', str(missing)])[0], 0)
        with patch.object(module, '_install_safety') as safety, patch.object(module, 'replay', side_effect=ConnectionError('INERT_PRIVATE_HTTP_BODY')):
            code, text = run(['replay', '--model', 'qev', '--limit', '1'])
            self.assertEqual(code, 1)
            self.assertEqual(text.strip(), 'error: ConnectionError')
            safety.assert_called_once_with(self.store.path)
        (self.store.path / 'calls.jsonl').write_text('{INERT_PRIVATE_BAD_JSON')
        code, text = run(['status'])
        self.assertEqual(code, 1)
        self.assertEqual(text.strip(), 'error: JSONDecodeError')

    def test_http_transport_disables_proxies_redirects_auth_and_other_urls(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'local_request'), 'restricted local transport must exist')
        with self.assertRaises(ValueError):
            module.local_request(ENDPOINT_BAD + '/props')
        import urllib.request
        opener = unittest.mock.MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"model_path":"qev-0.8b-text-Q8_0.gguf"}'
        with patch.object(urllib.request, 'build_opener', return_value=opener) as build:
            result = module.local_request('http://127.0.0.1:18874/props')
            self.assertEqual(result['model_path'], 'qev-0.8b-text-Q8_0.gguf')
            handlers = build.call_args.args
            self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in handlers))
            redirect = next(h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
            with self.assertRaises(ValueError):
                redirect.redirect_request(None, None, 302, '', {}, 'https://example.invalid')
            request = opener.open.call_args.args[0]
            self.assertFalse(any(key.lower() == 'authorization' for key in request.headers))

    def test_invalid_limits_are_rejected_before_any_backend_contact(self):
        module = self.module()
        for limit in (True, -1, .5, float('nan'), float('inf')):
            with self.subTest(limit=limit), patch.object(module, 'local_request') as request:
                with self.assertRaises(ValueError):
                    module.replay(self.store, model_key='lite', limit=limit, request=request,
                                  model_provider=lambda url: provenance('lite'))
                request.assert_not_called()

    def test_selected_model_requires_both_actual_props_and_provenance(self):
        from unittest.mock import Mock
        module = self.module()
        self.store.append('calls.jsonl', capture())
        for key, other in [('lite', 'qev'), ('qev', 'lite')]:
            for props, model in [(provenance(other), provenance(key)),
                                 (provenance(key), provenance(other)),
                                 ({'model_path': '/models/' + provenance(key)['model_path'].split('/')[-1]}, provenance(key)),
                                 (provenance(key), {'model_id': key}),
                                 (None, provenance(key))]:
                request = Mock(return_value=props)
                provider = Mock(return_value=model)
                with self.subTest(key=key, props=props, model=model), self.assertRaises(ValueError):
                    module.replay(self.store, model_key=key, request=request, model_provider=provider)
                self.assertEqual(request.call_args_list, [unittest.mock.call(module.ENDPOINT + '/props')])
                self.assertEqual(self.store.read('replays.jsonl'), [])
        for key in (None, [], 'INERT_PRIVATE_MODEL'):
            request = Mock()
            with self.assertRaises(ValueError):
                module.replay(self.store, model_key=key, request=request)
            request.assert_not_called()

    def test_replay_model_identity_must_match_validated_props(self):
        module = self.module()
        self.store.append('calls.jsonl', capture())
        request = lambda url, payload=None: {'model_path': 'D:/textgen/models/decision/qev-0.8b-text-Q8_0.gguf'} if url.endswith('/props') else {'answers': {'block': {'noul': .5}}}
        with self.assertRaises(ValueError):
            module.replay(self.store, model_key='qev', request=request, model_provider=lambda url: provenance('lite'))
        self.assertEqual(self.store.read('replays.jsonl'), [])

    def test_audit_guard_isolated_child_and_real_store_cli(self):
        module = self.module()
        import subprocess
        root = Path(__file__).resolve().parents[1]
        interpreter = sys.executable
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        cli_store = Path(self.tmp.name) / 'real-store'
        command = [str(interpreter), str(root / 'trial/observe.py'), '--store', str(cli_store)]
        status_result = subprocess.run(command + ['status'], capture_output=True, text=True, env=env)
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        self.assertEqual(json.loads(status_result.stdout)['traffic_captures'], 0)
        result = subprocess.run(command + ['label', capture()['id'], 'authorized', '--note', 'INERT_PRIVATE_NOTE'], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('INERT_PRIVATE', result.stdout)
        result = subprocess.run(command + ['report', '--journal', str(Path(self.tmp.name) / 'missing')], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('INERT_PRIVATE', (cli_store / 'review.md').read_text())
        self.assertEqual((cli_store / 'labels.jsonl').stat().st_mode & 0o777, 0o600)
        script = '''import sys, pathlib, socket, subprocess
sys.path.insert(0, sys.argv[1])
import observe
observe._install_safety(pathlib.Path(sys.argv[2]))
checks = [lambda: subprocess.run(['never-execute-this']),
          lambda: socket.socket().connect(('127.0.0.1', 9)),
          lambda: pathlib.Path(sys.argv[3]).write_text('inert')]
for check in checks:
    try: check()
    except RuntimeError: pass
    else: raise AssertionError('audit guard did not deny capability')
print('three capabilities denied')
'''
        result = subprocess.run([str(interpreter), '-c', script, str(root / 'trial'), str(cli_store), str(Path(self.tmp.name) / 'outside-store')], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'three capabilities denied')
        self.assertFalse((Path(self.tmp.name) / 'outside-store').exists())

    def test_status_distinguishes_real_captures_from_probes_without_payloads(self):
        module = self.module()
        self.store.append('calls.jsonl', capture())
        self.store.append('calls.jsonl', capture(2, origin='verification'))
        result = module.status(self.store)
        self.assertEqual(result['mode'], 'log-only')
        self.assertEqual(result['traffic_captures'], 1)
        self.assertEqual(result['verification_captures'], 1)
        self.assertNotIn('INERT_PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
