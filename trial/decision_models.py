"""Fixed trusted decision-model choices. Importing performs no I/O."""
import re

MODEL_SPECS = {
    'lite': {'id': 'lite', 'label': 'D3 Lite', 'filename': 'vllm-d3-lite-Q8_0.gguf',
             'model_path': 'D:/textgen/models/decision/vllm-d3-lite-Q8_0.gguf'},
    'qev': {'id': 'qev', 'label': 'QEV', 'filename': 'qev-0.8b-text-Q8_0.gguf',
            'model_path': 'D:/textgen/models/decision/qev-0.8b-text-Q8_0.gguf'},
}


def get_model(key):
    if not isinstance(key, str) or key not in MODEL_SPECS:
        raise ValueError('unknown decision model')
    return dict(MODEL_SPECS[key])


def model_identity(provenance):
    """Return only allowlisted metadata; aliases and basenames prove nothing."""
    provenance = provenance if isinstance(provenance, dict) else {}
    identity = {'id': None, 'label': 'Unknown model',
                'model_file_sha256': None, 'native_template_sha256': None}
    path = provenance.get('model_path')
    if isinstance(path, str):
        normalized = path.replace('\\', '/').casefold()
        for key, spec in MODEL_SPECS.items():
            if normalized == spec['model_path'].casefold():
                identity.update(id=key, label=spec['label'])
                break
    for key in ('model_file_sha256', 'native_template_sha256'):
        value = provenance.get(key)
        if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value):
            identity[key] = value
    return identity
