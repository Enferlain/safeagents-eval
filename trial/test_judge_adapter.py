"""Adapter HTTP contract tests. Proposed commands are never executed."""
import asyncio
import json
import socket
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import judge_adapter as adapter
from safeagents.guard import Config, analyze
from safeagents.judge import Judge


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = adapter.ThreadingHTTPServer(('127.0.0.1', 0), adapter.Handler)
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, body):
        req = urllib.request.Request(self.url + '/chat/completions',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_capture_preserves_payload_precision_and_correlates_response(self):
        import tempfile
        from pathlib import Path
        from observation import Store
        prompt = ('<task>\nRead an inert fixture.\n</task>\n\n'
                  '<tool_call name="read_file">\n{"path":"fixture.txt"}\n</tool_call>\n\n'
                  '<scanner_findings>\n- fixture (medium): Inert example\n</scanner_findings>')
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'private')
            with patch.object(self.server,'capture_store',store,create=True),patch.object(adapter,'score',return_value=.123456789123456):
                status,body=self.post({'model':'observation-verification','messages':[{'role':'user','content':prompt}]})
                self.assertEqual(status,200)
                event=store.read('calls.jsonl')[0]
                self.assertEqual(event['payload'],adapter.parse_judge_prompt(prompt))
                self.assertEqual(event['score'],.123456789123456)
                self.assertEqual(event['origin'],'verification')
                reason=json.loads(body['choices'][0]['message']['content'])['reason']
                self.assertIn('observation_id='+event['id'],reason)
                with patch.object(adapter,'score',side_effect=TimeoutError('do-not-log-this-private-text')):
                    status,_=self.post({'messages':[{'role':'user','content':prompt}]})
                self.assertEqual(status,502)
                failed=store.read('calls.jsonl')[-1]
                self.assertEqual(failed['error'],'TimeoutError')
                self.assertIsNone(failed['score'])
                self.assertNotIn('do-not-log-this-private-text',json.dumps(failed))

    def test_capture_failure_does_not_change_judge_verdict(self):
        from unittest.mock import Mock
        store=Mock()
        store.append.side_effect=OSError('private-value-must-not-be-logged')
        prompt=('<task>\nInert fixture.\n</task>\n\n<tool_call name="read_file">\n{}\n</tool_call>\n\n'
                '<scanner_findings>\n- fixture (medium): Inert\n</scanner_findings>')
        with patch.object(self.server,'capture_store',store,create=True),patch.object(adapter,'score',return_value=.2),patch.object(adapter.Handler,'log_message') as log:
            status,body=self.post({'messages':[{'role':'user','content':prompt}]})
            self.assertEqual(status,200)
            verdict=json.loads(body['choices'][0]['message']['content'])
            self.assertEqual(verdict['verdict'],'allow')
            self.assertTrue(store.append.called)
            self.assertNotIn('private-value-must-not-be-logged',str(log.call_args_list))

    def test_live_policy_targets_authorized_assistant_work(self):
        payload = adapter.parse_judge_prompt(
            '<task>\nReview the requested changes.\n</task>\n\n'
            '<tool_call name="terminal">\n{}\n</tool_call>\n\n'
            '<scanner_findings>\n- test (medium): Example finding\n</scanner_findings>'
        )
        instructions = payload['questions']['block']['instructions']
        self.assertIn('personal assistant', instructions)
        self.assertIn('unintended destination', instructions)
        self.assertIn('conversation history', instructions)
        self.assertIn('not inherently violations', instructions)
        self.assertNotIn('hidden tests, graders', instructions)

    def test_malformed_prompt_is_http_error_not_allow(self):
        status, body = self.post({'messages': [{'role': 'user', 'content': 'ping'}]})
        self.assertGreaterEqual(status, 400)
        self.assertIn('error', body)
        self.assertNotIn('choices', body)
        self.assertNotIn('verdict', body)
    def test_backend_failure_reaches_safeagents_policy(self):
        request = {
            'messages': [{'role': 'user', 'content': 'Fix the parser tests.'}],
            'tools': [{'type': 'function', 'function': {'name': 'terminal',
                'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}}}}],
        }
        call = {'function': {'name': 'terminal', 'arguments': json.dumps({'command': 'cat /proc/1/environ'})}}

        async def check():
            judge = Judge(self.url, 'test')
            try:
                from safeagents.guard import Guard
                for fail_closed, block in [(False, True), (True, True), (True, False)]:
                    with self.subTest(fail_closed=fail_closed, block=block):
                        config = Config(judge=judge, judge_fail_closed=fail_closed, block=block)
                        review = await analyze(request, call, config)
                        self.assertTrue(review.judge.startswith('error:'), review.judge)
                        self.assertIn('502', review.judge)
                        self.assertEqual(review.reason is not None, fail_closed)
                        allowed = await Guard(request, config).allow('terminal', call['function']['arguments'])
                        self.assertEqual(allowed, not (fail_closed and block))
            finally:
                await judge.client.aclose()

        # A bound but non-listening socket guarantees a local connection refusal.
        # Live model/proxy processes are not stopped or reconfigured for this test.
        with socket.socket() as unavailable:
            unavailable.bind(('127.0.0.1', 0))
            url = f'http://127.0.0.1:{unavailable.getsockname()[1]}'
            with patch.object(adapter, 'LLAMA_URL', url):
                asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
