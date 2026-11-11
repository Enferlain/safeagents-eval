"""Inert fixtures; no live transcripts, tool args or model services."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).parent / 'security-plugin' / 'telemetry.py'

class TelemetryTests(unittest.TestCase):
    def module(self):
        self.assertTrue(PATH.exists(), 'security telemetry module must exist')
        spec = importlib.util.spec_from_file_location('security_telemetry_fixture', PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_shared_identifier_preserves_valid_values_without_truncation(self):
        identity_path = PATH.with_name('identity.py')
        self.assertTrue(identity_path.exists(), 'shared identity module must exist')
        spec = importlib.util.spec_from_file_location('security_identity_fixture', identity_path)
        identity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(identity)
        for value in ('session/turn:call-1_2.3', 'a' * 199 + '/'):
            with self.subTest(value=value):
                self.assertIs(identity.identifier(value), value)
                self.assertIs(self.module().identifier(value), value)
        for value in (None, 1, True, [], {}, '', 'a' * 201, 'has space', 'a\n', 'a\x00', 'é'):
            with self.subTest(value=value):
                self.assertIsNone(identity.identifier(value))
                self.assertIsNone(self.module().identifier(value))

    def test_request_records_only_route_metadata_not_private_context(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            collector = module.Telemetry(Path(tmp) / 'store')
            collector.pre_api_request(session_id='session-one', turn_id='turn-one', api_request_id='request-one',
                provider='custom', base_url='http://127.0.0.1:8788/api/coding/paas/v4', api_mode='chat_completions',
                user_message='INERT_PRIVATE_TASK', request={'token':'INERT_PRIVATE_TOKEN'},
                conversation_history=[{'content':'INERT_PRIVATE_HISTORY'}])
            path = Path(tmp) / 'store' / 'security-routes.jsonl'
            text = path.read_text()
            self.assertNotIn('INERT_PRIVATE', text)
            row = json.loads(text)
            self.assertEqual(row['route'], 'safeagents-glm')
            self.assertEqual(row['session_id'], 'session-one')
            self.assertEqual(row['turn_id'], 'turn-one')
            self.assertEqual(row['api_request_id'], 'request-one')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_observers_never_return_policy_directives_and_group_one_tool_call(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            collector = module.Telemetry(Path(tmp) / 'store')
            callbacks = {}
            class Context:
                def register_hook(self, name, callback): callbacks[name] = callback
            module.register_observers(Context(), collector)
            args = dict(session_id='session-one', turn_id='turn-one', tool_call_id='call-one',
                        tool_name='write_file', args={'content':'INERT_PRIVATE_ARGUMENT'}, result='INERT_PRIVATE_OUTPUT')
            self.assertIsNone(callbacks['pre_tool_call'](**args))
            self.assertIsNone(callbacks['post_tool_call'](**args, status='success'))
            text = (Path(tmp) / 'store' / 'security-events.jsonl').read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(rows[0]['id'], rows[1]['id'])
            self.assertEqual(rows[1]['execution'], 'tool_returned')
            self.assertNotIn('INERT_PRIVATE', text)
            self.assertFalse(rows[1]['reviewable'])

    def test_tool_identity_preserves_request_ids_and_separates_requests(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            collector = module.Telemetry(Path(tmp) / 'store')
            fields = dict(session_id='session/one', turn_id='turn/one', tool_call_id='call/one',
                          tool_name='terminal', args={'secret': 'INERT_PRIVATE_ARGS'},
                          result='INERT_PRIVATE_RESULT', arbitrary='INERT_PRIVATE_KWARG')
            for request in ('api/one', 'api/two', None, None):
                self.assertIsNone(collector.pre_tool_call(**fields, api_request_id=request))
                self.assertIsNone(collector.post_tool_call(**fields, api_request_id=request, status='ok'))
            text = (Path(tmp) / 'store' / 'security-events.jsonl').read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            for index, request in enumerate(('api/one', 'api/two', None, None)):
                pre, post = rows[index * 2:index * 2 + 2]
                self.assertEqual(pre['id'], post['id'])
                for row in (pre, post):
                    self.assertEqual(row['api_request_id'], request)
                    for key in ('session_id', 'turn_id', 'tool_call_id'):
                        self.assertEqual(row[key], fields[key])
            self.assertNotEqual(rows[0]['id'], rows[2]['id'])
            self.assertNotEqual(rows[0]['id'], rows[4]['id'])
            self.assertEqual(rows[4]['id'], rows[6]['id'])
            self.assertNotIn('INERT_PRIVATE', text)

    def test_native_outcomes_do_not_overclaim_execution_or_effects(self):
        module = self.module()
        cases = [
            ('pre', 'success', 'proposed', 'proposed', 'Tool proposed; no contextual enforcement'),
            ('post', 'ok', 'completed', 'tool_returned', 'Tool returned successfully; side effects not independently verified'),
            ('post', 'success', 'completed', 'tool_returned', 'Tool returned successfully; side effects not independently verified'),
            ('post', 'completed', 'completed', 'tool_returned', 'Tool returned successfully; side effects not independently verified'),
            ('post', 'blocked', 'blocked', 'blocked', 'Tool blocked before execution; not executed'),
            ('post', 'cancelled', 'cancelled', 'cancelled', 'Tool cancelled; execution and side effects unknown'),
            ('post', 'error', 'error', 'error', 'Tool reported an error; side effects unknown'),
            ('post', None, 'unknown', 'unknown', 'Tool outcome unknown; execution and side effects unknown'),
            ('post', 'INERT_PRIVATE_STATUS', 'unknown', 'unknown', 'Tool outcome unknown; execution and side effects unknown'),
            ('post', {}, 'unknown', 'unknown', 'Tool outcome unknown; execution and side effects unknown'),
        ]
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            collector = module.Telemetry(Path(tmp) / 'store')
            for stage, status, expected_status, execution, detail in cases:
                with self.subTest(stage=stage, status=status):
                    status_kwargs = {} if status is None else {'status': status}
                    getattr(collector, stage + '_tool_call')(
                        session_id='session', tool_call_id='call', **status_kwargs)
                    row = json.loads((Path(tmp) / 'store' / 'security-events.jsonl').read_text().splitlines()[-1])
                    self.assertEqual(row['status'], expected_status)
                    self.assertEqual(row['execution'], execution)
                    self.assertEqual(row['detail'], detail)
                    self.assertFalse(row['reviewable'])
                    self.assertIsNone(row['api_request_id'])

    def test_observer_failure_is_visible_but_not_a_policy_decision(self):
        module = self.module()
        callbacks = {}
        class Context:
            def register_hook(self, name, callback): callbacks[name] = callback
        class Broken:
            def unavailable(self, **kwargs): raise OSError('INERT_PRIVATE_EXCEPTION')
            pre_api_request = pre_tool_call = post_tool_call = unavailable
        module.register_observers(Context(), Broken())
        for name in ('pre_api_request', 'pre_tool_call', 'post_tool_call'):
            with self.subTest(hook=name), self.assertLogs('local-agent-security', level='WARNING') as log:
                self.assertIsNone(callbacks[name]())
            self.assertIn('execution not intercepted', str(log.output))
            self.assertNotIn('INERT_PRIVATE', str(log.output))

    def test_route_does_not_accept_remote_lookalikes_or_responses(self):
        module = self.module()
        for url, mode in [('http://127.0.0.1:8788/api/coding/paas/v4','responses'),
                          ('http://127.0.0.1:8788.evil.invalid/api/coding/paas/v4','chat'),
                          ('http://secret@127.0.0.1:8788/api/coding/paas/v4','chat'),
                          ('http://127.0.0.1:8788/api/coding/paas/v4?token=secret','chat'),
                          ('http://example.invalid:8789/v1','chat')]:
            self.assertIsNone(module.observed_route(url, mode))
        self.assertEqual(module.observed_route('http://localhost:8789/v1','chat'), 'safeagents-local')

    def test_package_registers_observers_only_for_default_profile(self):
        from unittest.mock import patch
        import sys
        from types import SimpleNamespace
        init_path = PATH.parent / '__init__.py'
        self.assertTrue(init_path.exists(), 'plugin registration must exist')
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            home = Path(tmp)
            spec = importlib.util.spec_from_file_location('security_plugin_fixture', init_path,
                                                         submodule_search_locations=[str(PATH.parent)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            self.assertIs(sys.modules[spec.name + '.telemetry'].identifier,
                          sys.modules[spec.name + '.identity'].identifier)
            hooks = {}
            class Context:
                def register_hook(self, name, callback): hooks[name] = callback
            constants = SimpleNamespace(get_hermes_home=lambda:home / '.hermes')
            with patch.dict(sys.modules, {'hermes_constants':constants}), patch.object(Path, 'home', return_value=home):
                module.register(Context())
                self.assertEqual(set(hooks), {'pre_api_request','pre_tool_call','post_tool_call'})
                hooks['pre_api_request'](session_id='fixture-session', provider='openai-codex')
                self.assertTrue((home/'Projects/safeagents-eval/trial/private-observation/security-routes.jsonl').exists())
                hooks.clear()
                constants.get_hermes_home = lambda:home/'.hermes/profiles/other'
                module.register(Context())
                self.assertFalse(hooks)

if __name__ == '__main__': unittest.main()
