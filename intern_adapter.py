# SPDX-License-Identifier: Apache-2.0
# Copyright 2026-2027 Shanghai AI Laboratory
# Copyright 2026 Alibaba Cloud.
# Local compiler/readout adaptation and modifications: 2026-11-11.
# Upstream notices and pinned revisions: licenses/intern/, references/sources.json.
"""Intern-Decision text-only one-question noul compiler and candidate scorer.

Encoding follows pinned internlm inference.py; no generation APIs are used.
"""
import json
import math
import os
import pathlib
import time
import hashlib
import sys
from functools import lru_cache


@lru_cache(maxsize=2)
def render_template(template):
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    def fail(message):
        raise ValueError(message)
    env.globals['raise_exception'] = fail
    return env.from_string(template)


def encode_request(request, template, tokenize, marker_id, max_length=8192):
    text = render_template(template).render(messages=compile_request(request),
        add_generation_prompt=False, enable_thinking=False, add_vision_id=True)
    tokens = tokenize(text)
    if len(tokens) > max_length:
        raise ValueError(f'Example has {len(tokens)} tokens, above {max_length}; truncation is forbidden')
    positions = [i for i, token in enumerate(tokens) if token == marker_id]
    if len(positions) != 1 or positions[0] == 0:
        raise ValueError('Decision marker count or position mismatch')
    return {'text': text, 'tokens': tokens, 'readout_position': positions[0]-1}

from collections.abc import Mapping

DECISION_TOKEN = '<decision>'
TEMPERATURES = {'intern-decision-0.8b-q8_0': 2.747760550703,
                'intern-decision-2b-q8_0': 2.100509348278}
SYSTEM_PROMPT = 'You are a careful decision assistant. Use the state and decision schema in the user message to make the requested decisions. For every field, choose exactly one answer symbol (e.g. A, B, C, ...) from its listed options and return one valid JSON object mapping each field name to its chosen symbol. Use the field names and symbols exactly as given. Do not include explanations, Markdown, or extra text.'


def forward_once(context, batch, encoded, candidate_ids, temperature):
    """Evaluate the entire masked skeleton once; request one row of output logits."""
    tokens, position = encoded['tokens'], encoded['readout_position']
    context.kv_cache_clear()  # Includes recurrent state; never reuse a previous row's prefix.
    batch.set_batch(tokens, n_past=0, logits_all=False)
    batch.batch.logits[len(tokens)-1] = False
    batch.batch.logits[position] = True
    context.decode(batch)
    # get_logits synchronizes the decode and returns packed selected rows. Exactly one
    # row was requested; select A/B only, not a vocabulary-wide softmax.
    logits = context.get_logits()
    selected = [float(logits[i]) for i in candidate_ids]
    return candidate_probability(selected, [0, 1], temperature), selected


def candidate_probability(logits, candidate_ids, temperature):
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError('Temperature must be finite and positive')
    if len(candidate_ids) != 2 or len(set(candidate_ids)) != 2:
        raise ValueError('Expected distinct A=no and B=yes candidate tokens')
    selected = [float(logits[i]) / temperature for i in candidate_ids]
    if not all(math.isfinite(x) for x in selected):
        raise ValueError('Nonfinite candidate logits')
    maximum = max(selected)
    weights = [math.exp(x - maximum) for x in selected]
    return weights[1] / sum(weights)


def compile_request(request):
    if not isinstance(request, dict) or 'state' not in request or request.get('images'):
        raise ValueError('Expected text-only state and one noul question')
    questions = request.get('questions')
    if not isinstance(questions, dict) or len(questions) != 1:
        raise ValueError('Expected exactly one noul question')
    field, question = next(iter(questions.items()))
    if not isinstance(field, str) or not field or question.get('type') != 'noul':
        raise ValueError('Expected named noul question')
    criteria = question.get('criteria')
    descriptions = criteria if isinstance(criteria, Mapping) else {}
    yes = next((str(descriptions[k]) for k in descriptions if str(k).lower() in {'yes','true','1'}), 'The answer is yes (affirmative, or align with the claim).')
    no = next((str(descriptions[k]) for k in descriptions if str(k).lower() in {'no','false','0'}), 'The answer is no (negative, or disagree with the claim).')
    schema = [f"{field}: {question.get('instructions', '')}", f'    A = no: {no}', f'    B = yes: {yes}']
    state = json.dumps(request['state'], ensure_ascii=False, indent=2, sort_keys=False)
    user = f'Return one answer for every field using the supplied answer symbols.\n\n## State\n{state}\n## Decision schema\n' + '\n'.join(schema)
    if DECISION_TOKEN in user:
        raise ValueError('Reserved decision marker appears in input evidence')
    return [{'role':'system','content':SYSTEM_PROMPT}, {'role':'user','content':user},
            {'role':'assistant','content':json.dumps({field:DECISION_TOKEN}, ensure_ascii=False, indent=4)}]


class InternBackend:
    """One causal full-prefix llama_decode, logits only before the masked marker.

    Uses llama-cpp-python's model/context/batch wrappers, not its generation class.
    """
    def __init__(self, model_path):
        self.model_path = pathlib.Path(model_path)
        stem = self.model_path.stem
        self.temperature = TEMPERATURES[stem]  # Unknown checkpoints must fail closed.
        self.gpu_uuid = 'GPU-4cec666b-991c-fe31-e63f-6455bc828119'
        if 'llama_cpp' in sys.modules:
            raise RuntimeError('Construct backend before importing llama_cpp; CUDA UUID must be set first')
        os.environ['CUDA_VISIBLE_DEVICES'] = self.gpu_uuid
        import llama_cpp
        import llama_cpp.llama_cpp as cpp
        from llama_cpp._internals import LlamaModel, LlamaContext, LlamaBatch
        if not cpp.llama_supports_gpu_offload():
            raise RuntimeError('CUDA offload is required for this benchmark')
        cpp.llama_backend_init()
        self.cpp = cpp
        root = pathlib.Path(__file__).resolve().parent
        name = 'Intern-Decision-0.8B' if '0.8b' in stem else 'Intern-Decision-2B'
        self.reference_dir = root/'references'/name
        self.template = (self.reference_dir/'chat_template.jinja').read_text(encoding='utf-8')
        self.model = self.context = self.batch = None
        try:
            params = cpp.llama_model_default_params()
            params.n_gpu_layers = 0x7FFFFFFF
            params.split_mode = cpp.LLAMA_SPLIT_MODE_NONE
            params.main_gpu = 0
            self.model = LlamaModel(path_model=str(self.model_path), params=params, verbose=True)
            context = cpp.llama_context_default_params()
            context.n_ctx = context.n_batch = context.n_ubatch = 8192
            context.n_seq_max = 1
            context.n_threads = context.n_threads_batch = 16
            context.flash_attn_type = cpp.LLAMA_FLASH_ATTN_TYPE_ENABLED
            context.attention_type = cpp.LLAMA_ATTENTION_TYPE_CAUSAL
            self.context = LlamaContext(model=self.model, params=context, verbose=True)
            self.batch = LlamaBatch(n_tokens=8192, embd=0, n_seq_max=1)
            self.marker_id = self.single_token(DECISION_TOKEN, special=True)
            self.candidate_ids = [self.single_token(s, special=False) for s in ('A','B')]
            config = json.loads((self.reference_dir/'tokenizer_config.json').read_text(encoding='utf-8'))
            expected = [int(k) for k,v in config['added_tokens_decoder'].items() if v['content']==DECISION_TOKEN]
            if expected != [self.marker_id] or len(set(self.candidate_ids)) != 2:
                raise ValueError('GGUF decision vocabulary does not match released checkpoint')
            self.runtime = {'backend':'llama-cpp-python-intern-full-prefix',
                'python':sys.version, 'python_executable':sys.executable,
                'llama_cpp_python':llama_cpp.__version__,
                'llama_cpp_commit':'0c1e57098bba43ac29e6e3b677cdceebdd22334f',
                'system_info':cpp.llama_print_system_info().decode(),
                'gpu_uuid':self.gpu_uuid, 'n_gpu_layers':-1, 'split_mode':'none', 'main_gpu':0,
                'n_ctx':cpp.llama_n_ctx(self.context.ctx),
                'n_batch':cpp.llama_n_batch(self.context.ctx),
                'n_ubatch':cpp.llama_n_ubatch(self.context.ctx),
                'flash_attention':True, 'attention':'causal', 'context_reset_per_row':True,
                'temperature':self.temperature,'marker_id':self.marker_id,'candidate_ids':self.candidate_ids,
                'readout':'logits at marker_index - 1; A=no, B=yes', 'generation_tokens':0,
                'timing':'in-process encode + full-prefix forward + readout; forward also recorded separately',
                'source':json.loads((root/'references/sources.json').read_text(encoding='utf-8'))[name]}
            if any(self.runtime[k] != 8192 for k in ('n_ctx','n_batch','n_ubatch')):
                raise RuntimeError('Runtime did not preserve requested context/batch/microbatch 8192')
        except BaseException:
            self.close()
            raise

    def single_token(self, text, special):
        tokens = self.model.tokenize(text.encode('utf-8'), add_bos=False, special=special)
        if len(tokens) != 1:
            raise ValueError(f'{text!r} must encode to one token, got {tokens}')
        return tokens[0]

    def encode(self, request):
        return encode_request(request, self.template,
            lambda text:self.model.tokenize(text.encode('utf-8'), add_bos=False, special=True),
            self.marker_id)

    def predict(self, request):
        start = time.perf_counter()
        encoded = self.encode(request)
        before_forward = time.perf_counter()
        p, logits = forward_once(self.context, self.batch, encoded, self.candidate_ids, self.temperature)
        elapsed_forward = time.perf_counter() - before_forward
        field = next(iter(request['questions']))
        return {'backend':self.runtime['backend'],
            'answers':{field:{'type':'noul','noul':p,'probabilities':{'no':1-p,'yes':p}}},
            'calibration':{'temperature':self.temperature,'method':'candidate-logit-temperature-scaling'},
            'candidate_logits':dict(zip(('A','B'),logits)),
            'encoding':{'readout_position':encoded['readout_position'],
                'input_ids_sha256':hashlib.sha256(json.dumps(encoded['tokens']).encode()).hexdigest(),
                'candidate_ids':self.candidate_ids},
            'usage':{'input_tokens':len(encoded['tokens']), 'output_tokens':0,'decision_count':1},
            'timing':{'forward_seconds':elapsed_forward,'adapter_seconds':time.perf_counter()-start}}

    def close(self):
        for name in ('batch','context','model'):
            item = getattr(self,name,None)
            if item is not None:
                item.close()
                setattr(self,name,None)
