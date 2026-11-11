"""Saved-prediction PAGE metrics, preserving ties and empirical FPR budgets."""
from collections import defaultdict
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xaab.runner import metrics


def describe(rows):
    result = metrics(rows,.5)
    valid = [r for r in rows if 'p' in r]
    positives = sum(r['expected']=='restrict' for r in valid)
    negatives = len(valid)-positives
    groups = defaultdict(lambda:[0,0])
    for row in valid:
        groups[row['p']][0 if row['expected']=='restrict' else 1] += 1
    tp = fp = 0
    ap = 0.0
    points = [(0,0,float('inf'))]
    for score,(dp,df) in sorted(groups.items(),reverse=True):
        tp += dp;fp += df
        if positives: ap += dp/positives*tp/(tp+fp)
        points.append((tp,fp,score))
    result.update(ap=ap if positives else None,positives=positives,negatives=negatives)
    result['ceilings'] = {}
    if positives and negatives:
        for ceiling in [5,10,15]:
            tp,minus_fp,threshold = max((tp,-fp,t) for tp,fp,t in points if fp*100<=ceiling*negatives)
            result['ceilings'][str(ceiling)] = {'tp':tp,'fp':-minus_fp,
                'recall':tp/positives,'fpr':-minus_fp/negatives,
                'threshold':threshold if threshold!=float('inf') else None}
    return result
