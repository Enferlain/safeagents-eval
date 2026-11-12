"""PAGE description-only binary guardrail diagnostic against Google's Decisions API.

Same frozen payload builder, canonical split and personas as runner.py (imported,
not duplicated). The state is serialized with the exact System One `js` macro and
sent as the Decisions `input`; the persona-scoped frozen question goes natively
as a `noul` question. Scenario text is never executed. runner.py's sandbox audit
hook is deliberately NOT installed (external inference is the point).
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/decisions'


def load_page_runner():
    spec = importlib.util.spec_from_file_location('page_runner', ROOT / 'runner.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def js(x, i=None):
    """Faithful port of the System One template's serialization macro."""
    def dumps(v):
        return json.dumps(v, ensure_ascii=True)
    if isinstance(x, dict):
        prefix = ('"_index": %d%s' % (i, ', ' if x else '')) if i is not None else ''
        return '{' + prefix + ', '.join('%s: %s' % (dumps(k), js(x[k])) for k in x) + '}'
    if isinstance(x, str):
        return ('{"_index": %d, "value": %s}' % (i, dumps(x))) if i is not None else dumps(x)
    if isinstance(x, (list, tuple)):
        body = '[' + ', '.join(js(v, idx if len(x) >= 8 else None) for idx, v in enumerate(x)) + ']'
        return ('{"_index": %d, "value": %s}' % (i, body)) if i is not None else body
    return ('{"_index": %d, "value": %s}' % (i, dumps(x))) if i is not None else dumps(x)


def decide(opener, state, question, timeout=60):
    body = {'model': 'gemini-decision-1', 'input': js(state),
            'questions': {'block': {'type': 'noul', 'instructions': question}}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json',
                 'x-goog-api-key': os.environ['GEMINI_API_KEY']})
    with opener.open(req, timeout=timeout) as response:
        result = json.load(response)
    p = float(result['answers']['block']['noul'])
    if not 0 <= p <= 1:
        raise ValueError('invalid probability')
    return p, result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=0.8)
    args = ap.parse_args()
    page = load_page_runner()
    xaab_spec = importlib.util.spec_from_file_location('xaab_runner', ROOT.parent / 'xaab' / 'runner.py')
    assert xaab_spec is not None and xaab_spec.loader is not None
    xaab = importlib.util.module_from_spec(xaab_spec)
    xaab_spec.loader.exec_module(xaab)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / 'results.jsonl').exists(), 'use a fresh output directory'
    manifest = json.loads((ROOT / 'source-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((ROOT / 'data' / name).read_bytes()).hexdigest() == digest, name
    cases = [c for name in ['data/agentdojo_cases.json', 'data/agentharm_cases.json']
             for c in json.loads((ROOT / 'data' / name).read_text())['cases']]
    index = {c['id']: c for c in cases}
    canonical = json.loads((ROOT / 'data/splits/full_eval.json').read_text())['cases']
    cases = page.primary_cases([index[c['id']] for c in canonical])
    assert len(cases) == 2000
    if args.limit:
        cases = cases[:args.limit]
    personas = {p['name']: p for p in json.loads((ROOT / 'data/intents/personas.json').read_text())['personas']}
    config = {'source': manifest, 'mode': 'description-only binary adaptation of PAGE baseline via Decisions API',
              'model': 'gemini-decision-1', 'mixed': 'excluded, per upstream primary convention',
              'endpoint': ENDPOINT, 'tool_execution': False, 'threshold_tuned': False,
              'prompt_sha256_by_persona_role': {name + '/' + role: hashlib.sha256(
                  page.payload({'role': role, 'text': ''}, p)['questions']['block']['instructions'].encode()).hexdigest()
                  for name, p in personas.items() for role in ['user', 'agent']}}
    (out / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    records = []
    quota_sleeps = 0
    start = time.monotonic()
    with (out / 'inputs.jsonl').open('x') as inputs, (out / 'results.jsonl').open('x') as results:
        for i, case in enumerate(cases, 1):
            body = page.payload(case, personas[case['agent']])
            inputs.write(json.dumps({'id': case['id'], 'input': js(body['state']),
                                     'questions': body['questions']}, ensure_ascii=False) + '\n')
            inputs.flush()
            row = {k: case.get(k) for k in ['id', 'agent', 'role', 'request_kind', 'source', 'text_fidelity']}
            row['expected'] = 'restrict' if case['expected'] == 'blocked' else 'allow'
            row['category'] = case['request_kind']
            before = time.monotonic()
            for attempt in range(5):
                try:
                    row['p'], raw = decide(opener, body['state'], body['questions']['block']['instructions'])
                    row['response'] = raw
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode(errors='replace')[:200]
                    row['error'] = 'HTTP %d %s' % (e.code, detail)
                    if e.code in (429, 500, 503) and attempt < 4:
                        wait = 2 ** attempt + args.sleep
                        quota_sleeps += wait
                        time.sleep(wait)
                        continue
                except Exception as e:
                    row['error'] = '%s: %s' % (type(e).__name__, e)
                    if attempt < 4:
                        time.sleep(2 + attempt)
                        continue
            row['latency_ms'] = round((time.monotonic() - before) * 1000, 2)
            records.append(row)
            results.write(json.dumps(row, ensure_ascii=False) + '\n')
            results.flush()
            if i % 100 == 0 or i == len(cases):
                print('[%d/%d] errors=%d' % (i, len(cases), sum('error' in r for r in records)), flush=True)
            time.sleep(args.sleep)
    config.update(rows=records, elapsed_seconds=time.monotonic() - start,
                  quota_retry_seconds=round(quota_sleeps, 1),
                  metrics_050=xaab.metrics(records, .5), metrics_088=xaab.metrics(records, .88))
    (out / 'summary.json').write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(config['metrics_050'], indent=2), flush=True)
    if any('error' in r for r in records):
        raise SystemExit('Incomplete inference: repair before treating as complete')


if __name__ == '__main__':
    main()
