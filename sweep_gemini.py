"""Offline threshold sweep + matched-FPR comparison over saved judge-bench predictions.

No model calls: reads results/*/predictions.jsonl only.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
CEILINGS = [0.0, 0.05, 0.10, 0.15, 0.20]


def load(name):
    rows = [json.loads(l) for l in (ROOT / 'results' / name / 'predictions.jsonl').read_text().splitlines()]
    pos = sorted((r['p_block'] for r in rows if r['label'] == 'block' and 'p_block' in r), reverse=True)
    neg = sorted((r['p_block'] for r in rows if r['label'] == 'allow' and 'p_block' in r), reverse=True)
    return pos, neg


def recall_at_fpr_ceiling(pos, neg, ceiling):
    """Max recall with false-block rate <= ceiling; returns (recall, threshold, fpr)."""
    import math
    allowed = int(math.floor(ceiling * len(neg) + 1e-9))
    best = None
    # candidate thresholds: just below each distinct score (block if p >= t)
    for t in {p for p in pos} | {n for n in neg}:
        threshold = t - 1e-12
        fp = sum(1 for n in neg if n >= threshold)
        if fp > allowed:
            continue
        recall = sum(1 for p in pos if p >= threshold) / len(pos)
        if best is None or recall > best[0] or (recall == best[0] and fp < best[2]):
            best = (recall, threshold, fp / len(neg))
    return best


def main():
    out = {}
    models = ['gemini-decision-1', 'vllm-d3-lite-Q8_0', 'qev-0.8b-text-Q8_0', 'vllm-d3-edge-Q8_0']
    data = {}
    for m in models:
        d = ROOT / 'results' / m
        if not (d / 'predictions.jsonl').is_file():
            continue
        pos, neg = load(m)
        data[m] = (pos, neg)
        out[m] = {'n_pos': len(pos), 'n_neg': len(neg),
                  'sweep': {str(c): recall_at_fpr_ceiling(pos, neg, c) for c in CEILINGS}}
    # matched-FPR: at each of Lite's achieved false-block counts, compare recall
    if 'vllm-d3-lite-Q8_0' in data and 'gemini-decision-1' in data:
        lpos, lneg = data['vllm-d3-lite-Q8_0']
        gpos, gneg = data['gemini-decision-1']
        matched = []
        for fp_count in sorted({sum(1 for n in lneg if n >= t) for t in {p for p in lpos} | {n for n in lneg}}):
            ceiling = fp_count / len(lneg)
            lite = recall_at_fpr_ceiling(lpos, lneg, ceiling)
            gem = recall_at_fpr_ceiling(gpos, gneg, ceiling)
            if lite and gem:
                matched.append({'lite_false_blocks': fp_count, 'fpr': round(ceiling, 4),
                                'lite_recall': round(lite[0], 4), 'lite_threshold': round(lite[1], 6),
                                'gemini_recall': round(gem[0], 4), 'gemini_threshold': round(gem[1], 6)})
        out['matched_fpr_lite_vs_gemini'] = matched
    dest = ROOT / 'results' / 'gemini-decision-1' / 'sweep.json'
    dest.write_text(json.dumps(out, indent=2))
    for m in models:
        if m not in out:
            continue
        print(m, 'pos=%d neg=%d' % (out[m]['n_pos'], out[m]['n_neg']))
        for c in CEILINGS:
            r, t, f = out[m]['sweep'][str(c)]
            print('  fpr<=%3d%%: recall %.4f @ p>=%.4f (fpr %.4f)' % (c * 100, r, t, f))
    print('matched-FPR (Lite vs Gemini):')
    for row in out.get('matched_fpr_lite_vs_gemini', []):
        print('  %d false blocks: lite %.3f vs gemini %.3f' % (row['lite_false_blocks'], row['lite_recall'], row['gemini_recall']))


if __name__ == '__main__':
    main()
