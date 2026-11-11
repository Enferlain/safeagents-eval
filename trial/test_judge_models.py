"""Live-judge naming uses actual model provenance, never a fixed Lite label."""
import json
import threading
import unittest
import urllib.request
from unittest.mock import patch

import judge_adapter as adapter


class JudgeModelTests(unittest.TestCase):
    def setUp(self):
        self.server = adapter.ThreadingHTTPServer(('127.0.0.1', 0), adapter.Handler)
        self.server.model_provenance = {'model_path': 'D:/textgen/models/decision/qev-0.8b-text-Q8_0.gguf',
            'model_file_sha256': 'a' * 64, 'native_template_sha256': 'b' * 64, 'private_extra': 'TOPSECRET'}
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def test_qev_verdict_and_health_do_not_claim_to_be_lite(self):
        prompt = ('<task>\nRead a fixture.\n</task>\n\n'
                  '<tool_call name="read_file">\n{}\n</tool_call>\n\n'
                  '<scanner_findings>\n- fixture (medium): Inert\n</scanner_findings>')
        request = urllib.request.Request(self.url + '/chat/completions',
            data=json.dumps({'messages': [{'role': 'user', 'content': prompt}]}).encode(),
            headers={'Content-Type': 'application/json'})
        with patch.object(adapter, 'score', return_value=.3), urllib.request.urlopen(request, timeout=3) as response:
            body = json.load(response)
        self.assertEqual(body['model'], 'qev-systemone')
        reason = json.loads(body['choices'][0]['message']['content'])['reason']
        self.assertIn('QEV P(block)=', reason)
        self.assertNotIn('lite', reason.lower())
        with urllib.request.urlopen(self.url + '/health', timeout=3) as response:
            health = json.load(response)
        self.assertEqual(health['model']['id'], 'qev')
        self.assertEqual(health['model']['model_file_sha256'], 'a' * 64)
        self.assertTrue(health['threshold_provisional'])
        self.assertNotIn('TOPSECRET', json.dumps(health))

    def test_scoring_refuses_a_backend_swapped_since_adapter_start(self):
        import inspect
        import io
        self.assertIn('expected_model', inspect.signature(adapter.score).parameters)
        urls = []
        class Opener:
            def open(self, request, timeout):
                url = request if isinstance(request, str) else request.full_url
                urls.append(url)
                return io.BytesIO(json.dumps({'model_path':
                    'D:/textgen/models/decision/vllm-d3-lite-Q8_0.gguf'}).encode())
        with patch.object(adapter.urllib.request, 'build_opener', return_value=Opener()), self.assertRaises(ValueError):
            adapter.score({'state': {}, 'questions': {}}, expected_model=self.server.model_provenance)
        self.assertEqual(urls, [adapter.LLAMA_URL.rstrip('/') + '/props'])

    def test_live_startup_requires_a_choice_and_checks_actual_provenance(self):
        import contextlib
        import io
        self.assertTrue(callable(getattr(adapter, 'parse_options', None)))
        self.assertTrue(callable(getattr(adapter, 'selected_provenance', None)))
        for args in ([], ['--model', 'other']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                adapter.parse_options(args)
        for model in ('lite', 'qev'):
            options = adapter.parse_options(['18873', '--model', model])
            self.assertEqual(options.model, model)
            self.assertEqual(options.port, 18873)
        with patch.object(adapter, 'model_info', return_value=self.server.model_provenance):
            self.assertEqual(adapter.selected_provenance('qev'), self.server.model_provenance)
            with self.assertRaises(ValueError):
                adapter.selected_provenance('lite')
        with patch.object(adapter, 'model_info') as discover:
            with self.assertRaises(ValueError):
                adapter.selected_provenance('not-a-model')
            discover.assert_not_called()


if __name__ == '__main__':
    unittest.main()
