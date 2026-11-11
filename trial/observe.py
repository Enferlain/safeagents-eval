"""Private local observation review. Never changes enforcement or executes inputs."""
from collections import Counter
import hashlib
import json
import time
import math
from pathlib import Path
import re
import uuid
import argparse
import sys
import urllib.request

try:
    from decision_models import MODEL_SPECS, get_model, model_identity
except ModuleNotFoundError as exc:
    if exc.name != 'decision_models':
        raise
    # Dashboard loads this trusted file by path, without adding trial/ to sys.path.
    import importlib.util
    _spec = importlib.util.spec_from_file_location('_observation_decision_models', Path(__file__).with_name('decision_models.py'))
    _models = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_models)
    MODEL_SPECS, get_model, model_identity = _models.MODEL_SPECS, _models.get_model, _models.model_identity


def local_request(url, payload=None):
    if url not in (ENDPOINT + '/props', ENDPOINT + '/v1/systemone'):
        raise ValueError('only explicit local replay paths permitted')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('redirect refused')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=120) as response:
        return json.load(response)


def _install_safety(path):
    # CLI replay only: irreversible audit hook must never be installed by imports.
    # Preload dependencies before the hook prevents outside-store bytecode writes.
    sys.dont_write_bytecode = True
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    import run  # local provenance reader; no main block execution
    import ssl
    from xaab.runner import install_safety
    install_safety(Path(path), 18874)


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse can echo arbitrary user input; keep all failure output type-only.
        raise ValueError('invalid CLI options')


def main(argv=None, *, store_factory=None):
    try:
        parser = PrivateParser(description='Private local observation review; log-only, no enforcement changes.')
        parser.add_argument('--store', default=str(Path(__file__).resolve().parent / 'private-observation'))
        commands = parser.add_subparsers(dest='command', required=True)
        commands.add_parser('status')
        replay_args = commands.add_parser('replay')
        replay_args.add_argument('--model', required=True, choices=tuple(MODEL_SPECS))
        replay_args.add_argument('--endpoint', default=ENDPOINT, choices=[ENDPOINT])
        replay_args.add_argument('--limit', type=int)
        replay_args.add_argument('--include-probes', action='store_true')
        report_args = commands.add_parser('report')
        report_args.add_argument('--gap', type=float, default=.3)
        report_args.add_argument('--journal', action='append')
        label_args = commands.add_parser('label')
        label_args.add_argument('id')
        label_args.add_argument('value', choices=LABELS)
        label_args.add_argument('--note')
        show_args = commands.add_parser('show')
        show_args.add_argument('id')
        show_args.add_argument('--include-content', action='store_true')
        args = parser.parse_args(argv)
        if store_factory is None:
            from observation import Store
            store_factory = Store
        store = store_factory(args.store)
        if args.command == 'status':
            result = status(store)
        elif args.command == 'label':
            result = label(store, args.id, args.value, args.note)
        elif args.command == 'report':
            journals = args.journal if args.journal is not None else [Path(__file__).resolve().parent / name for name in ('journal.jsonl', 'local-journal.jsonl')]
            result = report(store, args.gap, journals)
        elif args.command == 'show':
            if not args.include_content:
                raise ValueError('raw content requires explicit local opt-in')
            checked_id(args.id)
            result = next(r for r in store.read('calls.jsonl') if r['id'] == args.id)
            print('WARNING: raw local record may contain secrets; do not paste into chat or cloud tools.',file=sys.stderr)
        else:
            _install_safety(store.path)
            result = replay(store, args.endpoint, args.limit, args.include_probes, model_key=args.model)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print('error: ' + type(exc).__name__)
        return 1

LABELS = ('authorized', 'unwanted', 'insufficient-context')


def checked_id(value):
    if re.fullmatch(r'journal:[0-9a-f]{12}:[0-9]+', value):
        return value
    if str(uuid.UUID(value)) != value:
        raise ValueError('invalid ID')
    return value


def label(store, capture_id, value, note=None):
    checked_id(capture_id)
    if value not in LABELS:
        raise ValueError('invalid label')
    store.append('labels.jsonl', {'id': capture_id, 'label': value, 'note': note, 'time': time.time()})
    return {'mode': 'log-only', 'id': capture_id, 'label': value}


def journal_rows(paths, calls):
    counts = Counter()
    rows = []
    first = min((r['time'] for r in calls), default=None)
    ids = {r['id'] for r in calls}
    for path in dict.fromkeys(Path(p).resolve() for p in paths):
        if not path.exists():
            counts['journal_missing'] += 1
            continue
        path_hash = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            stamp = entry.get('time')
            if not isinstance(stamp, (float, int)) or isinstance(stamp, bool) or not math.isfinite(stamp):
                counts['journal_invalid_time'] += 1
                continue
            if first is None or stamp < first:
                counts['journal_historical_omitted'] += 1
                continue
            judge = entry.get('judge')
            found = re.search(r'observation_id=([0-9a-f-]{36})(?![0-9a-f-])', judge) if isinstance(judge, str) else None
            linked_id = found.group(1) if found else '-'
            if 'judge' not in entry:
                kind = 'journal_legacy'
            elif isinstance(judge, str) and (judge.lower().startswith('error:') or 'judge-adapter error' in judge.lower()):
                kind = 'journal_failures'
            elif judge is None and entry.get('verdict') in ('flagged', 'blocked', 'suspicious'):
                kind = 'deterministic_flags'
            elif judge is None:
                kind = 'journal_legacy'
            else:
                kind = ('journal_matched' if linked_id in ids else 'journal_unmatched') if found else 'journal_legacy'
            counts[kind] += 1
            number = entry.get('id')
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                counts['journal_invalid_id'] += 1
                continue
            rows.append({'id': f'journal:{path_hash}:{number}', 'time': stamp, 'kind': kind, 'capture_id': linked_id})
    return counts, rows, first


def verified_replays(store):
    """Legacy filenames and declared keys are not evidence of model identity."""
    rows = []
    for row in store.read('qev.jsonl') + store.read('replays.jsonl'):
        actual = model_identity(row.get('model'))['id']
        if actual is not None and row.get('model_key', actual) == actual:
            rows.append(row)
    return rows


def report(store, gap=.3, journals=()):
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap) or gap < 0:
        raise ValueError('invalid review gap')
    calls = store.read('calls.jsonl')
    labels = {r['id']: r['label'] for r in store.read('labels.jsonl') if r.get('label') in LABELS}
    latest = {}
    for row in verified_replays(store):
        key = (row['capture_id'], row['input_sha256'], row['policy_sha256'],
               canonical_hash(model_identity(row.get('model'))))
        latest[key] = row
    counts, entries, first = journal_rows(journals, calls)
    summary = status(store)
    for key in ('deterministic_flags', 'journal_historical_omitted', 'journal_matched', 'journal_failures', 'journal_legacy', 'journal_unmatched', 'journal_missing', 'journal_invalid_id', 'journal_invalid_time'):
        summary[key] = counts[key]
    lines = ['# Private local observation review', '',
             '**Mode: log-only. No enforcement change. Labels are review annotations, not enforcement.**', '',
             f'Review gap {gap:.3f} is a triage heuristic, not a scientific or calibrated metric.',
             'Replay uses the SAME captured provisional cutoff for comparison; it is not calibrated for the selected model.',
             'Unverified replay provenance is excluded from comparisons; source identity is Unknown model when unverified.',
             'Only identifiers, timestamps, scores, statuses and labels are rendered. No tasks, arguments, reasons or notes.', '',
             '## Summary', '']
    lines += [f'- {key}: {value}' for key, value in summary.items()]
    table = []
    for capture in calls:
        cid = checked_id(capture['id'])
        matching = [r for key, r in latest.items()
                    if key[:3] == (cid, capture['input_sha256'], capture['policy_sha256'])]
        source_model = model_identity(capture.get('model'))
        for replay_row in matching or [None]:
            replay_model = model_identity(replay_row.get('model')) if replay_row else None
            source_score = capture.get('score')
            replay_score = replay_row.get('score') if replay_row else None
            delta = replay_score - source_score if source_score is not None and replay_score is not None else None
            source_flag = source_score >= capture['threshold'] if source_score is not None else None
            replay_flag = replay_score >= capture['threshold'] if replay_score is not None else None
            error = bool(capture.get('error') or (replay_row and replay_row.get('error')))
            disagreement = source_flag is not None and replay_flag is not None and source_flag != replay_flag
            large_gap = delta is not None and abs(delta) >= gap
            state = 'error' if error else ('disagreement' if disagreement else ('gap' if large_gap else ('flag' if source_flag or replay_flag else ('pending' if not replay_row else 'scored'))))
            priority = 0 if error else 1 if disagreement else 2 if large_gap else 3 if source_flag or replay_flag else 4
            def fmt(value):
                return '-' if value is None else f'{value:.3f}'
            def flag(value):
                return '-' if value is None else 'yes' if value else 'no'
            source_hash = source_model['model_file_sha256'] or '-'
            replay_hash = (replay_model['model_file_sha256'] or '-') if replay_model else '-'
            source_template = source_model['native_template_sha256'] or '-'
            replay_template = (replay_model['native_template_sha256'] or '-') if replay_model else '-'
            origin = 'verification' if capture.get('origin') == 'verification' else 'traffic' if capture.get('origin', 'traffic') == 'traffic' else 'other'
            table.append((origin, priority, capture['time'], f"| {cid} | {capture['time']:.3f} | {state} | {source_model['label']} | {replay_model['label'] if replay_model else '-'} | {fmt(source_score)} | {fmt(replay_score)} | {fmt(delta)} | {flag(source_flag)} | {flag(replay_flag)} | {capture['threshold']:.3f} | {source_hash} | {replay_hash} | {source_template} | {replay_template} | {labels.get(cid, '-')} |"))
    for origin in ('traffic', 'verification', 'other'):
        lines += ['', f'## {origin} captures', '',
                  '| ID | Time (Unix) | Status | Source model | Replay model | Source score | Replay score | Delta Replay-Source | Source wouldflag | Replay SAME captured provisional cutoff wouldflag | Cutoff | Source weights SHA-256 | Replay weights SHA-256 | Source template SHA-256 | Replay template SHA-256 | Human label |',
                  '|---|---:|---|---|---|---:|---:|---:|---|---|---:|---|---|---|---|---|']
        lines += [row[3] for row in sorted(table, key=lambda r: (r[1], r[2], r[3])) if row[0] == origin]
    lines += ['', '## Deterministic bypass flags (not model-scored)', '',
              f'Scope: journal timestamps >= first capture time {first if first is not None else "none (no capture window)"}.',
              f'Historical/out-of-window rows omitted: {counts["journal_historical_omitted"]}.',
              'Only explicit judge=null flagged rows count as deterministic. Failures, legacy and unmatched rows remain separate.', '',
              '| Stable label-able ID | Time (Unix) | Classification | Linked capture ID | Human label |', '|---|---:|---|---|---|']
    for entry in sorted(entries, key=lambda r: (r['kind'] != 'deterministic_flags', r['time'], r['id'])):
        lines.append(f"| {entry['id']} | {entry['time']:.3f} | {entry['kind']} | {entry['capture_id']} | {labels.get(entry['id'], '-')} |")
    store.write_text('review.md', '\n'.join(lines) + '\n')
    return summary

ENDPOINT = 'http://127.0.0.1:18874'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('invalid probability')
    return float(value)


def replay(store, endpoint=ENDPOINT, limit=None, include_probes=False, *, model_key, request=None, model_provider=None):
    get_model(model_key)
    if endpoint != ENDPOINT or (limit is not None and
                                (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0)):
        raise ValueError('invalid replay options')
    if request is None:
        request = local_request
    if model_provider is None:
        from observation import model_info
        model_provider = model_info
    props = request(endpoint + '/props')
    if model_identity(props)['id'] != model_key:
        raise ValueError('unexpected backend model')
    model = model_provider(endpoint)
    if model_identity(model)['id'] != model_key:
        raise ValueError('backend provenance mismatch')
    identity = canonical_hash(model_identity(model))
    existing = set()
    for row in verified_replays(store):
        prior_identity = model_identity(row.get('model'))
        if row.get('error') is not None or any(prior_identity[key] is None for key in
                                              ('model_file_sha256', 'native_template_sha256')):
            continue
        try:
            probability(row.get('score'))
        except ValueError:
            continue
        existing.add((row.get('capture_id'), row.get('input_sha256'), row.get('policy_sha256'),
                      canonical_hash(prior_identity)))
    scored = 0
    for capture in store.read('calls.jsonl'):
        if capture.get('origin', 'traffic') != 'traffic' and not include_probes:
            continue
        if capture.get('payload') is None:
            continue
        if limit is not None and scored >= limit:
            break
        payload = capture['payload']
        if canonical_hash(payload) != capture['input_sha256'] or canonical_hash(payload['questions']) != capture['policy_sha256']:
            raise ValueError('capture integrity mismatch')
        key = (capture['id'], capture['input_sha256'], capture['policy_sha256'], identity)
        if key in existing:
            continue
        threshold = probability(capture['threshold'])
        row = {'capture_id': capture['id'], 'time': time.time(),
               'input_sha256': capture['input_sha256'], 'policy_sha256': capture['policy_sha256'],
               'model_key': model_key, 'model': model, 'score': None, 'error': None, 'threshold': threshold,
               'same_cutoff_provisional': True, 'threshold_source': 'captured-provisional', 'would_block': None}
        try:
            result = request(endpoint + '/v1/systemone', payload)
            row['score'] = probability(result['answers']['block']['noul'])
            row['would_block'] = row['score'] >= threshold
        except Exception as exc:
            row['error'] = type(exc).__name__
            store.append('replays.jsonl', row)
            raise
        store.append('replays.jsonl', row)
        existing.add(key)
        scored += 1
    return {'mode': 'log-only', 'scored': scored}


def status(store):
    calls = store.read('calls.jsonl')
    return {'mode': 'log-only', 'traffic_captures': sum(r.get('origin', 'traffic') == 'traffic' for r in calls),
            'verification_captures': sum(r.get('origin') == 'verification' for r in calls),
            'other_captures': sum(r.get('origin', 'traffic') not in ('traffic', 'verification') for r in calls),
            'replay_attempts': len(store.read('qev.jsonl')) + len(store.read('replays.jsonl')),
            'label_events': len(store.read('labels.jsonl'))}


if __name__ == '__main__':
    raise SystemExit(main())
