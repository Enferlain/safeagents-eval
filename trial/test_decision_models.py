"""Trusted model selection and public identity; inert fixtures only."""
import importlib.util
import unittest


class DecisionModelsTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.find_spec('decision_models')
        self.assertIsNotNone(spec, 'shared fixed decision-model registry must exist')
        import decision_models
        return decision_models

    def test_registry_accepts_only_two_explicit_model_keys(self):
        module = self.module()
        expected = {'lite': ('D3 Lite', 'vllm-d3-lite-Q8_0.gguf'),
                    'qev': ('QEV', 'qev-0.8b-text-Q8_0.gguf')}
        self.assertEqual(set(module.MODEL_SPECS), set(expected))
        for key, (label, filename) in expected.items():
            self.assertEqual(module.get_model(key), {
                'id': key, 'label': label, 'filename': filename,
                'model_path': 'D:/textgen/models/decision/' + filename})
        for key in (None, True, 1, [], {}, '', 'QEV', '../qev', 'INERT_PRIVATE_KEY'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.get_model(key)

    def test_identity_uses_exact_known_path_and_sanitizes_every_field(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'model_identity'), 'public identity sanitizer must exist')
        unknown = {'id': None, 'label': 'Unknown model', 'model_file_sha256': None,
                   'native_template_sha256': None}
        for provenance in (None, [], 'qev', {}, {'model_id': 'qev', 'alias': 'lite'},
                           {'model_path': 'qev-0.8b-text-Q8_0.gguf'},
                           {'model_path': '/models/qev-0.8b-text-Q8_0.gguf'},
                           {'model_path': 'D:/elsewhere/qev-0.8b-text-Q8_0.gguf'},
                           {'model_path': 1}):
            with self.subTest(provenance=provenance):
                self.assertEqual(module.model_identity(provenance), unknown)
        for key in ('lite', 'qev'):
            model = module.get_model(key)
            for path in (model['model_path'], model['model_path'].replace('/', '\\')):
                provenance = {'model_path': path, 'model_id': 'INERT_PRIVATE_ALIAS',
                              'model_file_sha256': 'a' * 64, 'native_template_sha256': 'b' * 64,
                              'decision_metadata': {'private': 'INERT_PRIVATE_METADATA'}}
                self.assertEqual(module.model_identity(provenance), {
                    'id': key, 'label': model['label'], 'model_file_sha256': 'a' * 64,
                    'native_template_sha256': 'b' * 64})
        for bad_hash in ('A' * 64, 'a' * 63, 'g' * 64, True, [], 'INERT_PRIVATE_HASH'):
            self.assertEqual(module.model_identity({'model_path': module.get_model('lite')['model_path'],
                             'model_file_sha256': bad_hash, 'native_template_sha256': bad_hash}),
                             dict(unknown, id='lite', label='D3 Lite'))


if __name__ == '__main__':
    unittest.main()
