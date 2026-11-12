"""Benchmark Google's Decisions API (gemini-decision-1) on the frozen SafeAgents judge bench.

Frozen-input parity with the local decision models: the state is serialized with
the exact `js` macro from the System One chat template (GGUF metadata), and the
question is the identical QUESTION string from run.py, delivered natively as a
`noul` question instead of a rendered "Answer: (" prompt line.
"""
import argparse, json, os, pathlib, time, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/decisions'
QUESTION = (ROOT / 'run.py').read_text().split("QUESTION='", 1)[1].split("'", 1)[0]


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


def frozen_input(row):
    state = {'task': row['task_text'], 'tool': row['tool_name'],
             'arguments': row['tool_args'], 'scanner_findings': row['findings']}
    return js(state)


def call(row, timeout=60):
    body = {'model': 'gemini-decision-1', 'input': frozen_input(row),
            'questions': {'block': {'type': 'noul', 'instructions': QUESTION}}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json',
                 'x-goog-api-key': os.environ['GEMINI_API_KEY']})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def metrics(records):
    import run
    return run.metrics(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int)
    parser.add_argument('--sleep', type=float, default=0.35)
    a = parser.parse_args()
    rows = json.loads((ROOT / 'dataset.json').read_text())
    rows = rows[:a.limit] if a.limit else rows
    dest = ROOT / 'results' / 'gemini-decision-1'
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / 'predictions.jsonl').exists():
        raise FileExistsError('Refusing to overwrite: %s' % dest)
    records = []
    with (dest / 'predictions.jsonl').open('w') as f:
        for i, row in enumerate(rows):
            rec = {'row_idx': row['row_idx'], 'label': row['label'], 'category': row['category'],
                   'task': row.get('task'), 'trial': row.get('trial')}
            start = time.perf_counter()
            for attempt in range(4):
                try:
                    response = call(row)
                    p = float(response['answers']['block']['noul'])
                    assert 0 <= p <= 1
                    rec.update(p_block=p, response=response)
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode(errors='replace')[:300]
                    if e.code in (429, 500, 503) and attempt < 3:
                        time.sleep(2 ** attempt + a.sleep); continue
                    rec['error'] = 'HTTP %d %s' % (e.code, detail)
                except Exception as e:
                    rec['error'] = str(e)[:300]
            rec['seconds'] = time.perf_counter() - start
            records.append(rec); f.write(json.dumps(rec) + '\n'); f.flush()
            if (i + 1) % 25 == 0:
                print(i + 1, '/', len(rows), flush=True)
            time.sleep(a.sleep)
    result = metrics(records)
    (dest / 'metrics.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
