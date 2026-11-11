"""Reference conformance tests; use the actual benchmark payload, not invented safety cases."""
import ast
import dataclasses
import importlib
import importlib.util
import json
import pathlib
import types
import unittest
from collections.abc import Mapping
from typing import Any
from run import ROOT, dataset, payload


def reference(model='Intern-Decision-0.8B'):
    source = (ROOT/'references'/model/'inference.py').read_text()
    tree = ast.parse(source)
    names = {'CompiledExample', 'DECISION_TOKEN', 'ANSWER_SYMBOLS', 'SYSTEM_PROMPT',
             '_json', '_options', '_message_content', 'compile_row'}
    nodes = [n for n in tree.body if getattr(n, 'name', None) in names or
             isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    scope = dict(dataclass=dataclasses.dataclass, Mapping=Mapping, Any=Any, json=json)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<pinned upstream compiler>', 'exec'), scope)
    return scope


class CompilerTests(unittest.TestCase):
    def test_same_messages_as_both_upstream_checkpoints(self):
        self.assertIsNotNone(importlib.util.find_spec('intern_adapter'), 'Intern adapter is not implemented')
        adapter = importlib.import_module('intern_adapter')
        for model in ('Intern-Decision-0.8B', 'Intern-Decision-2B'):
            compile_row = reference(model)['compile_row']
            for row in dataset():
                request = payload(row)
                upstream = compile_row(request)
                actual = adapter.compile_request(request)
                self.assertEqual(actual, upstream.messages, (model,row['row_idx']))
                self.assertEqual(upstream.symbols['block'], ('A', 'B'))

class EncodingTests(unittest.TestCase):
    def test_exact_released_template_and_marker_readout(self):
        import intern_adapter as adapter
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        self.assertTrue(hasattr(adapter, 'encode_request'), 'Full-prefix encoding is missing')
        request = payload(dataset()[0])
        messages = reference()['compile_row'](request).messages
        template = (ROOT/'references/Intern-Decision-0.8B/chat_template.jinja').read_text()
        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
        expected = env.from_string(template).render(messages=messages, add_generation_prompt=False,
                                                    enable_thinking=False, add_vision_id=True)
        seen = []
        def tokenize(text):
            seen.append(text)
            # Encoding behavior test only, not an inference fixture.
            return [1, 2, 3, 99, 4, 5]
        encoded = adapter.encode_request(request, template, tokenize, marker_id=99, max_length=8192)
        self.assertEqual(seen, [expected])
        self.assertEqual(encoded['tokens'], [1, 2, 3, 99, 4, 5])
        self.assertEqual(encoded['readout_position'], 2)
        self.assertIn('<think>\n\n</think>\n\n', expected)
        self.assertTrue(expected.endswith('}\u003c|im_end|>\n'))

class CalibrationTests(unittest.TestCase):
    def test_candidate_only_softmax_uses_checkpoint_temperature(self):
        import intern_adapter as adapter
        import math
        self.assertTrue(hasattr(adapter, 'candidate_probability'), 'Candidate scoring is missing')
        # Irrelevant vocabulary logits must not enter the normalization.
        logits = [10000., 2., 4., -10000.]
        for temperature in (2.747760550703, 2.100509348278):
            p = adapter.candidate_probability(logits, [1, 2], temperature)
            self.assertAlmostEqual(p, 1 / (1 + math.exp(-2 / temperature)), places=12)
        for t in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                adapter.candidate_probability(logits, [1, 2], t)

class ForwardTests(unittest.TestCase):
    def test_full_prefix_one_decode_selects_only_pre_marker_logits(self):
        import intern_adapter as adapter
        self.assertTrue(hasattr(adapter, 'forward_once'), 'Full-prefix forward is missing')
        class Batch:
            def set_batch(self, tokens, n_past, logits_all):
                self.tokens = list(tokens)
                self.batch = types.SimpleNamespace(logits=[False] * (len(tokens)-1) + [True])
                self.n_past = n_past
        class Context:
            def __init__(self): self.calls = []
            def kv_cache_clear(self): self.calls.append('clear')
            def decode(self, batch):
                self.calls.append(('decode', batch.tokens[:], batch.batch.logits[:], batch.n_past))
            def get_logits(self): return [0., 2., 4., 10000.]
        context, batch = Context(), Batch()
        encoded = {'tokens':[1,2,3,99,4,5], 'readout_position':2}
        p, selected = adapter.forward_once(context, batch, encoded, [1,2], 2.)
        self.assertEqual(context.calls, ['clear', ('decode', [1,2,3,99,4,5], [False,False,True,False,False,False], 0)])
        self.assertAlmostEqual(p, 1/(1+__import__('math').exp(-1)))
        self.assertEqual(selected, [2.,4.])

class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').name == 'nt', 'CUDA integration runs in the isolated Windows runtime')
    def test_real_checkpoint_repeatability_and_encoding(self):
        import intern_adapter as adapter
        self.assertTrue(hasattr(adapter, 'InternBackend'), 'CUDA backend is missing')
        model = pathlib.Path(r'D:\textgen\models\decision\intern-decision-0.8b-q8_0.gguf')
        backend = adapter.InternBackend(model)
        try:
            request = payload(dataset()[0])
            a = backend.predict(request)
            b = backend.predict(request)
            self.assertTrue(0 <= a['answers']['block']['noul'] <= 1)
            self.assertAlmostEqual(a['answers']['block']['noul'], b['answers']['block']['noul'], places=6)
            self.assertEqual(a['usage']['output_tokens'], 0)
            self.assertEqual(backend.runtime['n_batch'], 8192)
            self.assertEqual(backend.runtime['n_ubatch'], 8192)
            self.assertEqual(a['encoding']['candidate_ids'], [32,33])
        finally:
            backend.close()

class PipelineTests(unittest.TestCase):
    def test_intern_pipeline_entrypoint_exists(self):
        import run
        self.assertTrue(hasattr(run, 'run_intern'), 'Benchmark routing for Intern is missing')
        self.assertIsNotNone(importlib.util.find_spec('intern_runner'), 'Isolated Intern worker is missing')

if __name__ == '__main__':
    unittest.main()
