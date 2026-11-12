"""Shadow-score stored judge captures with Google's Decisions API (gemini-decision-1).

Reads the private observation capture store (exact System One payloads the live
D3 Lite judge saw) and sends the identical state and question to the hosted
decision model, appending score-only records keyed by capture UUID. Never
affects enforcement, never executes captured calls, never prints capture
content. Idempotent: already-shadowed captures are skipped. Verification-origin
captures are skipped. Run on demand:

    GEMINI_API_KEY=... .venv-checks/bin/python trial/shadow_gemini.py [--sleep 1.9]
"""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
STORE = ROOT / 'private-observation'
CALLS = STORE / 'calls.jsonl'
SHADOW = STORE / 'shadow-gemini.jsonl'
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/decisions'


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


def decide(state, question, timeout=60):
    body = {'model': 'gemini-decision-1', 'input': js(state),
            'questions': {'block': {'type': 'noul', 'instructions': question}}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json',
                 'x-goog-api-key': os.environ['GEMINI_API_KEY']})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.load(response)
    answer = result.get('answers', {}).get('block', {})
    p = answer.get('noul')
    p = float(p)
    if not 0 <= p <= 1:
        raise ValueError('invalid probability')
    return p, answer.get('confidence'), result.get('usage')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sleep', type=float, default=1.9)
    args = ap.parse_args()
    if 'GEMINI_API_KEY' not in os.environ:
        raise SystemExit('GEMINI_API_KEY not set')
    STORE.mkdir(exist_ok=True)
    done = set()
    if SHADOW.exists():
        done = {json.loads(l)['capture_id'] for l in SHADOW.read_text().splitlines() if l.strip()}
    captures = [json.loads(l) for l in CALLS.read_text().splitlines() if l.strip()]
    pending = [c for c in captures
               if c.get('origin') == 'traffic' and c.get('error') is None
               and c.get('id') not in done and isinstance(c.get('payload'), dict)]
    print('captures=%d shadowed=%d pending=%d (verification/error rows skipped)'
          % (len(captures), len(done), len(pending)))
    failures = 0
    with SHADOW.open('a') as out:
        for i, c in enumerate(pending, 1):
            payload = c['payload']
            question = (payload.get('questions', {}).get('block', {}) or {}).get('instructions')
            state = payload.get('state')
            if question is None or state is None:
                print('[%d/%d] %s: unusable payload shape, skipped' % (i, len(pending), c['id'][:8]), flush=True)
                failures += 1
                continue
            row = {'capture_id': c['id'], 'model': 'gemini-decision-1', 'time': time.time(),
                   'policy_sha256': c.get('policy_sha256'), 'input_sha256': c.get('input_sha256'),
                   'live_score': c.get('score'), 'live_would_block': c.get('would_block'),
                   'live_threshold': c.get('threshold')}
            start = time.perf_counter()
            for attempt in range(5):
                try:
                    p, confidence, usage = decide(state, question)
                    row.update(p_block=p, confidence=confidence, usage=usage,
                               seconds=round(time.perf_counter() - start, 3))
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode(errors='replace')[:200]
                    row['error'] = 'HTTP %d %s' % (e.code, detail)
                    if e.code in (429, 500, 503) and attempt < 4:
                        time.sleep(2 ** attempt + args.sleep)
                        continue
                except Exception as e:
                    row['error'] = '%s: %s' % (type(e).__name__, e)
                    if attempt < 4:
                        time.sleep(2 + attempt)
                        continue
            if 'p_block' not in row:
                failures += 1
            out.write(json.dumps(row) + '\n')
            out.flush()
            shown = ('p_block=%.4f' % row['p_block']) if 'p_block' in row else row['error']
            print('[%d/%d] %s: %s (live lite score: %s)' % (i, len(pending), c['id'][:8], shown, c.get('score')), flush=True)
            time.sleep(args.sleep)
    os.chmod(SHADOW, 0o600) if SHADOW.exists() else None
    if failures:
        raise SystemExit('%d capture(s) failed; re-run to retry them' % failures)
    print('shadow pass complete')


if __name__ == '__main__':
    main()
