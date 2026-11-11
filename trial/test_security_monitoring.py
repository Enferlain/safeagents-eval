"""Monitoring integrations use isolated owner-only fixtures, never private records."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parent


def load_service():
    spec = importlib.util.spec_from_file_location('monitoring_service_test', ROOT / 'security-plugin/service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.store = self.repo / 'trial/private-observation'
        self.store.mkdir(parents=True, mode=0o700)
        self.home = Path(self.temp.name) / 'hermes'
        self.home.mkdir()
        self.mod = load_service()
        self.service = self.mod.SecurityService(self.repo, self.home)

    def write(self, name, rows, journal=False):
        path = (self.repo / 'trial' if journal else self.store) / name
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        path.chmod(0o600)
        return path

    def test_recorder_errors_do_not_make_judge_or_route_unavailable(self):
        identity = self.mod.model_catalog().get_model('lite')
        props = {'model_path': identity['model_path']}
        # Use the actual catalogue projection accepted by the service.
        model = self.mod.model_catalog().model_identity(props)
        self.assertEqual(model['id'], 'lite')
        self.write('security-routes.jsonl', [dict(session_id='s', origin='native_hook',
                   route='safeagents-glm', api_mode='chat', time=time.time())])
        def respond(request):
            if request.url.port == 18872:
                return httpx.Response(200, json=props if request.url.path == '/props' else {'status': 'ok'})
            return httpx.Response(200, json={'status': 'ok', 'model': props, 'threshold': .88,
                                            'capture_enabled': True, 'capture_errors': 1})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        with patch.object(self.mod.httpx, 'Client', return_value=client), patch.object(self.mod.socket, 'create_connection', side_effect=OSError):
            health = self.service._health()
        by_id = {item['id']: item for item in health}
        self.assertTrue(by_id['capture']['healthy'])
        self.assertFalse(by_id['recorder']['healthy'])
        self.assertEqual(by_id['recorder']['write_errors'], 1)
        self.assertEqual(self.service._judge_status(health)['state'], 'available')
        by_id['proxy_glm']['healthy'] = True
        self.assertEqual(self.service._route('s', health)['state'], 'observing')


    def test_journal_exposes_sanitized_verdict_and_consultation(self):
        self.write('journal.jsonl', [
            dict(id=1, time=100., verdict='flagged', judge=None,
                 signals=[{'id': 'side_channel.model_call', 'severity': 'high', 'title': 'TOPSECRET'}], arguments='TOPSECRET'),
            dict(id=2, time=101., verdict='suspicious', signals=[]),
        ], journal=True)
        events = sorted(self.service.events(all_sessions=True)['events'], key=lambda e: e['time'])
        self.assertEqual(events[0]['verdict'], 'flagged')
        self.assertEqual(events[0]['judge_consultation'], 'not_consulted')
        self.assertEqual(events[1]['judge_consultation'], 'unknown')
        self.assertEqual(events[0]['signals'], [{'id': 'side_channel.model_call', 'severity': 'high'}])
        self.assertNotIn('TOPSECRET', json.dumps(events))


    def capture(self):
        return {'id': '3a31cd31-98fc-4324-94d2-e573796ee37e', 'time': 100.,
                'origin': 'traffic', 'payload': {'secret': 'TOPSECRET'}, 'score': .9,
                'threshold': .88, 'error': None, 'model': {},
                'input_sha256': 'a', 'policy_sha256': 'b'}

    def test_linked_journal_capture_is_one_finding_with_preserved_review(self):
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        self.write('labels.jsonl', [dict(id=capture['id'], label='authorized')])
        self.write('journal.jsonl', [dict(id=3, time=101., verdict='flagged',
                   judge='block: fixture observation_id=' + capture['id'])], journal=True)
        events = self.service.events(all_sessions=True)['events']
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertTrue(event['id'].startswith('journal:'))
        self.assertEqual(event['capture_id'], capture['id'])
        self.assertEqual(event['human_label'], 'authorized')
        self.assertEqual(event['identity_status'], 'matched')
        self.assertTrue(event['inspectable'])
        self.assertEqual(event['scores'][0]['score'], .9)
        self.assertEqual(self.service.event(capture['id'])['id'], event['id'])
        self.assertNotIn('TOPSECRET', json.dumps(event))


    def test_conflicting_alias_labels_remain_explicit_until_reviewed_together(self):
        import shutil
        for name in ('observation.py', 'observe.py', 'decision_models.py'):
            shutil.copyfile(ROOT / name, self.repo / 'trial' / name)
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        self.write('journal.jsonl', [dict(id=3, time=101., verdict='flagged',
                   judge='block: fixture observation_id=' + capture['id'])], journal=True)
        event = self.service.events(all_sessions=True)['events'][0]
        self.write('labels.jsonl', [dict(id=capture['id'], label='authorized'),
                                   dict(id=event['id'], label='unwanted')])
        event = self.service.event(event['id'])
        self.assertTrue(event['review_conflict'])
        self.assertIsNone(event['human_label'])
        self.assertEqual(event['identity_status'], 'matched')
        self.assertEqual(set(event['review_labels'].values()), {'authorized', 'unwanted'})
        self.service.label(event['id'], 'insufficient-context')
        reviewed = self.service.event(event['id'])
        self.assertFalse(reviewed['review_conflict'])
        self.assertEqual(reviewed['human_label'], 'insufficient-context')
        self.assertEqual(self.service.event(capture['id'])['human_label'], 'insufficient-context')


    def test_status_exposes_attempts_and_unreviewed_findings_with_independent_review_health(self):
        self.write('journal.jsonl', [dict(id=1, time=100., verdict='flagged', judge=None)], journal=True)
        self.write('security-routes.jsonl', [dict(session_id='s', origin='native_hook', time=100.),
                                           dict(session_id='s', origin='native_hook', time=101.),
                                           dict(session_id='other', origin='native_hook', time=102.)])
        with patch.object(self.service, '_health', return_value=[]):
            status = self.service.status('s')
        self.assertEqual(status['route_activity'], {'attempts': 2, 'last_attempt': 101.})
        self.assertEqual(status['attention']['needs_review'], 1)
        self.assertEqual(status['attention']['scope'], 'stored_history')
        self.assertEqual(status['monitoring']['state'], 'unverified')
        self.assertIsNone(status['monitoring']['guard']['reviewed'])
        self.write('labels.jsonl', []).write_text('{invalid private labels\n')
        with patch.object(self.service, '_health', return_value=[]):
            status = self.service.status('s')
        self.assertIsNone(status['attention']['needs_review'])
        self.assertFalse(status['attention']['healthy'])
        self.assertEqual(len(status['attention']['events']), 1)


    def test_capture_error_does_not_invalidate_mixed_attention_schema(self):
        import subprocess
        capture = dict(self.capture(), error='TimeoutError', payload=None, score=None,
                       input_sha256=None, policy_sha256=None)
        self.write('calls.jsonl', [capture])
        self.write('journal.jsonl', [dict(id=1, time=101., verdict='flagged', judge=None)], journal=True)
        with patch.object(self.service, '_health', return_value=[]):
            attention = self.service.status()['attention']
        self.assertTrue(attention['healthy'])
        self.assertEqual(attention['needs_review'], 2)
        self.assertEqual(len(attention['events']), 2)
        # Exercise the actual frontend validator against the real backend output,
        # not a handwritten event fixture that can omit producer differences.
        plugin = (ROOT / 'security-plugin/desktop/plugin.js').read_text()
        validator = plugin[plugin.index('function attentionView(value)'):plugin.index('function resourceValid(')]
        checked = subprocess.run(['node', '-e', validator + '\n'
                                  + 'let data=""; process.stdin.on("data", c => data += c); '
                                  + 'process.stdin.on("end", () => process.exit(attentionView(JSON.parse(data)) ? 0 : 1));'],
                                 input=json.dumps(attention), text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, 'ordinary capture errors must not erase unrelated findings')
        error = next(event for event in attention['events'] if event['id'] == capture['id'])
        self.assertEqual(error['signals'], [])
        self.assertIsNone(error['verdict'])
        self.assertEqual(error['judge_consultation'], 'unknown')
        self.assertIsNone(error.get('capture_id'), 'self-referencing capture_id would poison merge links')
        self.assertFalse(error['inspectable'])
        self.assertNotIn('TOPSECRET', json.dumps(attention))


    def test_warn_without_a_judge_is_not_a_blocking_finding(self):
        self.write('journal.jsonl', [dict(id=1, time=100., verdict='suspicious', judge=None)], journal=True)
        event = self.service.events(all_sessions=True)['events'][0]
        self.assertFalse(event['would_flag'])
        self.assertEqual(event['judge_consultation'], 'not_consulted')
        self.assertNotIn('deterministic', event['detail'].lower())


    def test_attention_joins_only_unique_current_run_journal_records(self):
        self.write('journal.jsonl', [dict(id=3, time=101., verdict='flagged', judge=None)], journal=True)
        self.write('local-journal.jsonl', [dict(id=3, time=101., verdict='flagged', judge=None)], journal=True)
        monitoring = {'state': 'verified', 'scope': 'current_managed_run', 'run': {'started_at': 90.},
                      'guard': {'reviewed': 1}, 'judge': {},
                      'journal_path': str(self.repo / 'trial/journal.jsonl'),
                      'records': [{'call_id': 3, 'verdict': 'flagged', 'judge_outcome': None, 'time': 101.}]}
        with patch.object(self.service, '_health', return_value=[]), patch.object(self.service, '_monitoring', return_value=monitoring):
            status = self.service.status()
        self.assertEqual(status['attention']['scope'], 'current_managed_run')
        self.assertEqual(status['attention']['needs_review'], 1)
        self.assertEqual(len(status['attention']['events']), 1)
        self.assertNotIn('journal_path', status['monitoring'])
        self.assertNotIn('records', status['monitoring'])


    def test_ambiguous_capture_links_are_conflicts_not_two_matches(self):
        capture = self.capture()
        self.write('calls.jsonl', [capture])
        self.write('journal.jsonl', [dict(id=index, time=101., verdict='flagged',
                   judge='block: fixture observation_id=' + capture['id']) for index in (1, 2)], journal=True)
        events = self.service.events(all_sessions=True)['events']
        journals = [row for row in events if row['id'].startswith('journal:')]
        self.assertTrue(all(row['identity_status'] == 'conflict' for row in journals))
        self.assertTrue(all(not row['reviewable'] and not row['inspectable'] for row in journals))
        self.assertEqual(len([row for row in events if row['id'] == capture['id']]), 1)


    def test_duplicate_capture_identity_cannot_silently_choose_scoring_evidence(self):
        capture = self.capture()
        self.write('calls.jsonl', [capture, dict(capture, score=.1)])
        self.write('journal.jsonl', [dict(id=1, time=101., verdict='flagged',
                   judge='block: fixture observation_id=' + capture['id'])], journal=True)
        journal = next(row for row in self.service.events(all_sessions=True)['events'] if row['id'].startswith('journal:'))
        self.assertEqual(journal['identity_status'], 'conflict')
        self.assertFalse(journal['inspectable'])
        self.assertFalse(journal['reviewable'])
        self.assertEqual(journal['scores'], [])
        capture_event = self.service.event(capture['id'])
        self.assertEqual(capture_event['identity_status'], 'conflict')
        self.assertFalse(capture_event['inspectable'])
        with self.assertRaises(self.mod.SecurityError) as caught:
            self.service.inspect(capture['id'], include_content=True)
        self.assertEqual(caught.exception.status, 409)


if __name__ == '__main__':
    unittest.main()
