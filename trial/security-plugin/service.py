"""Local read/review backend. Imports do not install hooks or start workers."""
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx

try:
    from .identity import identifier
except ImportError:
    import importlib.util
    _identity_spec = importlib.util.spec_from_file_location('_security_identity', Path(__file__).with_name('identity.py'))
    _identity = importlib.util.module_from_spec(_identity_spec)
    _identity_spec.loader.exec_module(_identity)
    identifier = _identity.identifier


def bounded_inspection(row, limit=262144):
    """Bound the complete wire JSON, including escaping and metadata."""
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
    if size(row) <= limit:
        return row
    result = {'id': row['id'], 'time': row['time'], 'truncated': True,
              'detail': 'Raw capture exceeds response limit; bounded JSON text preview only.', 'preview': ''}
    # Iterencode avoids a second giant nested object in the response. Limit text before final wire-size search.
    chunks, length = [], 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(row):
        chunks.append(chunk[:limit - length])
        length += len(chunks[-1])
        if length >= limit:
            break
    text = ''.join(chunks)
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        result['preview'] = text[:mid]
        if size(result) <= limit:
            low = mid
        else:
            high = mid - 1
    result['preview'] = text[:low]
    return result


class SecurityError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


def checked_id(value):
    if not isinstance(value, str):
        raise SecurityError(400, 'Invalid event identifier.')
    if re.fullmatch(r'journal:[0-9a-f]{12}:[0-9]+(?::[0-9a-f]{64})?', value):
        return value
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise SecurityError(400, 'Invalid event identifier.')


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def score(value):
    value = number(value)
    return value if value is not None and 0 <= value <= 1 else None


@lru_cache(maxsize=1)
def model_catalog():
    """One trusted catalogue; deployment copies the same file beside this service."""
    import importlib.util
    local = Path(__file__).with_name('decision_models.py')
    path = local if local.is_file() else Path(__file__).resolve().parents[1] / 'decision_models.py'
    SecurityService._safe_path(path)
    spec = importlib.util.spec_from_file_location('_security_decision_models', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecurityService:
    def __init__(self, repo_root=None, hermes_home=None):
        self.repo_root = Path(repo_root or Path.home() / 'Projects/safeagents-eval').absolute()
        self.hermes_home = Path(hermes_home or os.environ.get('HERMES_HOME') or Path.home() / '.hermes').absolute()
        self.store_path = self.repo_root / 'trial/private-observation'
        self.profile_allowed = (self.hermes_home.parent.name != 'profiles'
                                and os.environ.get('HERMES_PROFILE', 'default') == 'default'
                                and (hermes_home is not None or self.hermes_home == Path.home() / '.hermes'))
        self._replay_lock = threading.Lock()
        self._process = None
        self._job_lock_file = None
        self._job = {'state': 'idle', 'started_at': None, 'finished_at': None,
                     'exit_code': None, 'model': None, 'detail': 'No replay started by this service.'}

    @staticmethod
    def _safe_path(path):
        for part in (path, *path.parents):
            if part.is_symlink():
                raise OSError('unsafe path')

    def _check_store(self):
        if not self.profile_allowed:
            raise OSError('profile unavailable')
        self._safe_path(self.store_path)
        info = self.store_path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError('unsafe store')

    def _read(self, name, *, journal=False):
        self._check_store()
        path = (self.repo_root / 'trial' if journal else self.store_path) / name
        self._safe_path(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        with os.fdopen(fd, encoding='utf-8') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (not journal and info.st_mode & 0o077):
                raise OSError('unsafe record')
            rows = [json.loads(line) for line in handle if line.strip()]
            if not all(isinstance(row, dict) for row in rows):
                raise ValueError('invalid record')
            return rows

    @staticmethod
    def _validate(name, rows):
        for row in rows:
            if name == 'calls':
                if checked_id(row.get('id')).startswith('journal:') or number(row.get('time')) is None:
                    raise ValueError('invalid capture')
                # Parsing can fail before the producer has a payload to hash.
                # Accept only its explicit, scoreless error variant as metadata.
                payloadless_error = (
                    all(k in row and row[k] is None for k in
                        ('payload', 'score', 'input_sha256', 'policy_sha256'))
                    and isinstance(row.get('error'), str)
                    and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', row['error']) is not None)
                if not payloadless_error and any(not isinstance(row.get(k), str) for k in ('input_sha256', 'policy_sha256')):
                    raise ValueError('invalid hashes')
            elif name == 'replays':
                checked_id(row.get('capture_id'))
                if any(not isinstance(row.get(k), str) for k in ('input_sha256', 'policy_sha256')):
                    raise ValueError('invalid hashes')
                if row.get('score') is not None and score(row['score']) is None:
                    raise ValueError('invalid score')
                if 'model_key' in row:
                    chosen = model_catalog().get_model(row['model_key'])
                    actual = model_catalog().model_identity(row.get('model'))
                    if actual['id'] is not None and actual['id'] != chosen['id']:
                        raise ValueError('replay model identity mismatch')
            elif name == 'labels':
                checked_id(row.get('id'))
                if row.get('label') not in ('authorized', 'unwanted', 'insufficient-context'):
                    raise ValueError('invalid label')
        return rows

    def _snapshot(self):
        records, subsystems = {}, {}
        for name, subsystem in (('calls', 'activity'), ('labels', 'review'), ('replays', 'replay')):
            try:
                rows = (self._read('qev.jsonl') + self._read('replays.jsonl')
                        if name == 'replays' else self._read(name + '.jsonl'))
                records[name] = self._validate(name, rows)
                subsystems[subsystem] = {'healthy': True, 'detail': 'Local records available; not model-runtime health.'}
            except (OSError, ValueError, UnicodeError, SecurityError, TypeError):
                records[name] = None
                subsystems[subsystem] = {'healthy': False, 'detail': subsystem.title() + ' records unavailable or invalid.'}
        return records, subsystems

    def _records(self):
        records, subsystems = self._snapshot()
        if not all(s['healthy'] for s in subsystems.values()):
            raise SecurityError(503, {'subsystems': subsystems, 'healthy': False})
        return records

    def _health(self):
        components = []
        for cid, label, port in (('proxy_glm', 'GLM observation proxy', 8788),
                                 ('proxy_local', 'Local observation proxy', 8789)):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.5):
                    healthy = True
            except OSError:
                healthy = False
            components.append({'id': cid, 'label': label, 'healthy': healthy,
                               'detail': 'TCP reachable; not proof of session coverage.' if healthy else 'TCP unavailable.'})
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=1.) as client:
            for cid, label, port in (('decision', 'Local decision model', 18872),
                                     ('capture', 'Observation adapter', 18873)):
                model = model_catalog().model_identity(None)
                threshold = None
                try:
                    response = client.get(f'http://127.0.0.1:{port}/health')
                    healthy = response.status_code == 200
                    if cid == 'capture' and healthy:
                        data = response.json()
                        components.append({'id': 'recorder', 'label': 'Capture recording',
                                           'healthy': data.get('capture_enabled') is True
                                           and type(data.get('capture_errors')) is int
                                           and data['capture_errors'] == 0,
                                           'write_errors': data.get('capture_errors') if type(data.get('capture_errors')) is int else None,
                                           'detail': 'Capture recording state; historical write errors do not imply inference failure.'})
                        # Endpoint readiness and model identity belong to the judge;
                        # capture persistence is a separate audit subsystem.
                        healthy = data.get('status') == 'ok'
                        model = model_catalog().model_identity(data.get('model'))
                        threshold = score(data.get('threshold'))
                    elif cid == 'decision' and healthy:
                        props = client.get('http://127.0.0.1:18872/props')
                        if props.status_code == 200:
                            model = model_catalog().model_identity(props.json())
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    healthy = False
                components.append({'id': cid, 'label': label, 'healthy': healthy,
                                   'model': model, 'threshold': threshold,
                                   'detail': 'Local health available.' if healthy else 'Local health or capture unavailable.'})
        decision = next(c for c in components if c['id'] == 'decision')
        capture = next(c for c in components if c['id'] == 'capture')
        if decision['healthy'] and capture['healthy']:
            ids = [c['model']['id'] for c in (decision, capture)]
            if None in ids or ids[0] != ids[1]:
                capture['healthy'] = False
                capture['identity_state'] = 'unknown' if None in ids else 'mismatch'
                capture['detail'] = 'Live judge model identity is unverified or differs from the current backend.'
        return components

    @staticmethod
    def _judge_status(components):
        by_id = {c['id']: c for c in components}
        capture, decision = by_id.get('capture', {}), by_id.get('decision', {})
        model = model_catalog().model_identity(None)
        state = capture.get('identity_state')
        if state not in ('unknown', 'mismatch'):
            if capture.get('healthy') is not True or decision.get('healthy') is not True:
                state = 'unavailable'
            else:
                captured, actual = capture.get('model', {}), decision.get('model', {})
                if not captured.get('id') or not actual.get('id'):
                    state = 'unknown'
                elif captured['id'] != actual['id']:
                    state = 'mismatch'
                else:
                    state, model = 'available', captured
        return {'state': state, 'model': model, 'threshold': score(capture.get('threshold')),
                'provisional': True,
                'detail': {'available': 'Adapter provenance matches the current backend model; cutoff is provisional.',
                           'unknown': 'Live judge identity is not established; no model is assumed.',
                           'mismatch': 'Adapter provenance differs from the current backend; restart the adapter deliberately.',
                           'unavailable': 'Live decision model or observation adapter is unavailable.'}[state]}

    def _route(self, session_id, components):
        unknown = {'state': 'unknown', 'provider': None, 'detail': 'Current session route is not proven.'}
        if not self.profile_allowed:
            return {'state': 'not_covered', 'provider': None, 'detail': 'Default profile only; this profile is not covered.'}
        session_id = identifier(session_id)
        if session_id is None:
            return unknown
        try:
            rows = [r for r in self._read('security-routes.jsonl') if r.get('session_id') == session_id
                    and r.get('origin') == 'native_hook' and number(r.get('time')) is not None]
        except (OSError, ValueError, UnicodeError):
            return {'state': 'degraded', 'provider': None, 'detail': 'Native route evidence unavailable or invalid.'}
        if rows:
            row = max(rows, key=lambda r: r['time'])
            route = row.get('route')
            provider = route if route in ('safeagents-glm', 'safeagents-local') else None
            detail = f"Last request observed at {row['time']} (Unix); not a guarantee about subsequent requests."
            if not 0 <= time.time() - row['time'] <= 300:
                return {'state': 'unknown', 'provider': provider, 'detail': 'Stale native evidence. ' + detail}
            if provider is None or row.get('api_mode') not in ('chat_completions', 'chat'):
                return {'state': 'not_covered', 'provider': provider, 'detail': 'Last request used an unsupported observation route or API mode. ' + detail}
            health = {r['id']: r['healthy'] for r in components}
            required = ('proxy_glm' if route == 'safeagents-glm' else 'proxy_local', 'decision', 'capture')
            return {'state': 'observing' if all(health.get(k) is True for k in required) else 'degraded',
                    'provider': provider, 'detail': detail}
        # Read only route columns, never model_config, transcripts or credential configuration.
        try:
            db_path = self.hermes_home / 'state.db'
            self._safe_path(db_path)
            if not db_path.is_file():
                return unknown
            with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=.5)) as db:
                row = db.execute('SELECT billing_provider,billing_base_url FROM sessions WHERE id=?', (session_id,)).fetchone()
            if row:
                parsed = urlsplit(row[1] or '')
                provider = None
                if parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and not parsed.username and not parsed.password:
                    provider = {8788: 'safeagents-glm', 8789: 'safeagents-local'}.get(parsed.port)
                return {'state': 'unknown', 'provider': provider,
                        'detail': 'Database last-recorded route only; no live request or coverage proof.'}
        except (OSError, ValueError, sqlite3.Error):
            return {'state': 'unknown', 'provider': None, 'detail': 'Last-recorded route unavailable.'}
        return unknown

    def _route_activity(self, session_id):
        unknown = {'attempts': None, 'last_attempt': None}
        if not self.profile_allowed or identifier(session_id) is None:
            return unknown
        try:
            if not (self.store_path / 'security-routes.jsonl').exists():
                return unknown
            rows = [row for row in self._read('security-routes.jsonl')
                    if row.get('session_id') == session_id and row.get('origin') == 'native_hook']
            if any(number(row.get('time')) is None for row in rows):
                return unknown
            return {'attempts': len(rows), 'last_attempt': max((row['time'] for row in rows), default=None)}
        except (OSError, ValueError, UnicodeError, TypeError):
            return unknown

    def _monitoring(self, route):
        unknown = {'state': 'unverified', 'scope': 'current_managed_run',
                   'detail': 'Managed proxy run is unverified.', 'run': None, 'policy': None,
                   'guard': {key: None for key in ('reviewed', 'passed', 'warn', 'flagged', 'blocked')},
                   'judge': {key: None for key in ('consulted', 'allow', 'block', 'errors',
                                                  'last_consultation', 'last_success', 'last_failure')},
                   'records': [], 'journal_path': None}
        if not self.profile_allowed or route not in ('safeagents-glm', 'safeagents-local'):
            return unknown
        try:
            if not hasattr(self, '_managed_monitor'):
                import importlib.util
                path = Path(__file__).with_name('managed_monitor.py')
                self._safe_path(path)
                spec = importlib.util.spec_from_file_location('_security_managed_monitor', path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._managed_monitor = module.ManagedMonitor(run_dir=self.hermes_home.parent / '.safeagents/run')
            return self._managed_monitor.snapshot(route)
        except (OSError, ValueError, ImportError, TypeError, AttributeError):
            return unknown

    @staticmethod
    def _attention(events, subsystems, monitoring):
        healthy = events is not None and subsystems['review']['healthy'] is True
        pool = [event for event in (events or []) if event['source'] == 'safeagents'
                and event['origin'] != 'verification']
        # Only SafeAgents call judgments need human review. Technical capture
        # failures (judge/inference/parsing/connectivity) are health, not
        # findings; the adapter's write errors are tracked separately.
        rows = [event for event in pool if event.get('verdict') in ('suspicious', 'flagged', 'blocked')]
        failed_judge_attempts = sum(event.get('verdict') is None and event['status'] == 'error'
                                    for event in pool)
        scope = 'stored_history'
        journal_path = monitoring.get('journal_path')
        if monitoring.get('state') in ('verified', 'degraded') and journal_path:
            scope = 'current_managed_run'
            path_hash = hashlib.sha256(str(journal_path).encode()).hexdigest()[:12]
            records = [record for record in monitoring.get('records', []) if record.get('verdict') != 'passed']
            selected = []
            for record in records:
                candidates = [event for event in rows
                              if event.get('journal_id', '').startswith('journal:' + path_hash + ':')
                              and event.get('call_id') == record.get('call_id')
                              and event.get('verdict') == record.get('verdict')
                              and event.get('judge_outcome') == record.get('judge_outcome')
                              and number(record.get('time')) is not None
                              and abs(event['time'] - record['time']) <= 2]
                if len(candidates) == 1:
                    selected.append(candidates[0])
                else:
                    healthy = False
            rows = list({event['id']: event for event in selected}.values())
            healthy = healthy and monitoring['state'] == 'verified'
        return {'healthy': healthy, 'scope': scope, 'total_events': len(rows), 'has_more': len(rows) > 100,
                # The capture store is not run-scoped; no run-scoped failure total is claimed.
                'failed_judge_attempts': None if scope == 'current_managed_run' else failed_judge_attempts,
                'needs_review': sum(event.get('human_label') is None or event.get('review_conflict') is True
                                    for event in rows) if healthy else None,
                'events': sorted(rows, key=lambda row: (row['time'], row['id']), reverse=True)[:100]}

    def status(self, session_id=None):
        records, subsystems = self._snapshot()
        events = None
        try:
            events = self._event_rows(records, subsystems)
        except SecurityError:
            subsystems['activity'] = {'healthy': False, 'detail': 'Activity records unavailable or invalid.'}
        def count(name, function):
            exists = (any((self.store_path / file).exists() for file in ('qev.jsonl', 'replays.jsonl'))
                      if name == 'replays' else (self.store_path / (name + '.jsonl')).exists())
            if records[name] is None or not exists:
                return None
            return function(records[name])
        counts = {'traffic_captures': count('calls', lambda rs: sum(r.get('origin', 'traffic') == 'traffic' for r in rs)),
                  'verification_captures': count('calls', lambda rs: sum(r.get('origin') == 'verification' for r in rs)),
                  'replay_attempts': count('replays', len), 'label_events': count('labels', len),
                  'reviewed_events': None}
        if events is not None and records['labels'] is not None and (self.store_path / 'labels.jsonl').exists():
            counts['reviewed_events'] = sum(row['reviewable'] and row.get('human_label') is not None
                                            and not row.get('review_conflict') for row in events)
        health = self._health() if self.profile_allowed else []
        observation = self._route(session_id, health) if self.profile_allowed else {
            'state': 'not_covered', 'provider': None, 'detail': 'Default profile only; this profile is not covered.'}
        healthy = records['calls'] is not None
        monitoring = self._monitoring(observation.get('provider'))
        # Full observation requires verified managed-run identity for the watched
        # route; a reachable proxy alone proves neither identity nor coverage.
        if observation.get('state') == 'observing' and monitoring.get('state') != 'verified':
            observation = {**observation, 'state': 'degraded',
                           'detail': observation['detail'] + ' Managed-run identity unverified; route reachable but not proven.'}
        attention = self._attention(events, subsystems, monitoring)
        return {'observed_at': time.time(), 'observation': observation,
                'monitoring': {key: value for key, value in monitoring.items() if key not in ('records', 'journal_path')},
                'route_activity': self._route_activity(session_id), 'attention': attention,
                'enforcement': {'state': 'not_enabled', 'detail': 'Observation only; no live blocking.'},
                'judge': self._judge_status(health),
                'components': [{'id': 'store', 'label': 'Private capture store', 'healthy': healthy,
                                'detail': 'Available.' if healthy else 'Capture records unavailable or invalid.'}] + health,
                'subsystems': subsystems, 'counts': counts, 'pending': []}

    def _event_rows(self, records=None, subsystems=None):
        if records is None:
            records, subsystems = self._snapshot()
        if records['calls'] is None:
            raise SecurityError(503, {'healthy': False, 'subsystems': subsystems})
        labels = {r['id']: r['label'] for r in (records['labels'] or [])}
        replays = {}
        for row in records['replays'] or []:
            key = (row['capture_id'], row['input_sha256'], row['policy_sha256'])
            # Group retries by model/weight/template identity, not per-launch aliases or endpoints.
            identity = model_catalog().model_identity(row.get('model'))
            fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            by_model = replays.setdefault(key, {})
            previous = by_model.get(fingerprint)
            if previous is None or (number(row.get('time')) or 0) >= (number(previous.get('time')) or 0):
                by_model[fingerprint] = row
        def score_metadata(row, role):
            probability, threshold = score(row.get('score')), score(row.get('threshold'))
            return {'role': role, 'model': model_catalog().model_identity(row.get('model')),
                    'score': probability, 'threshold': threshold,
                    'would_flag': probability >= threshold if probability is not None and threshold is not None else None,
                    'error': row.get('error') is not None, 'time': number(row.get('time')), 'provisional': True}
        events = []
        for row in records['calls']:
            cid = checked_id(row.get('id'))
            stamp = number(row.get('time'))
            if stamp is None:
                raise SecurityError(503, 'Observation records unavailable or invalid.')
            comparisons = list(replays.get((cid, row.get('input_sha256'), row.get('policy_sha256')), {}).values())
            live = score_metadata(row, 'live')
            scores = [live] + [score_metadata(replayed, 'replay') for replayed in comparisons]
            events.append({'id': cid, 'time': stamp, 'session_id': None, 'turn_id': None, 'tool_call_id': None,
                           'origin': 'verification' if row.get('origin') == 'verification' else 'traffic',
                           'source': 'safeagents', 'status': 'error' if any(item['error'] for item in scores) else 'observed',
                           'execution': 'not_intercepted', 'tool': None,
                           # Captures are judge inputs, not scanner findings: the journal owns
                           # verdicts/signals/consultation metadata. Explicit defaults keep the
                           # attention schema consistent so a single failed capture cannot
                           # invalidate the whole envelope downstream. No capture_id
                           # self-reference: _merge_findings would treat it as a journal link.
                           'verdict': None, 'signals': [], 'judge_consultation': 'unknown',
                           'scores': scores,
                           'would_flag': live['would_flag'],
                           'human_label': labels.get(cid),
                           # Technical failures (judge/inference/parsing) are health
                           # evidence, never human-review findings — anywhere.
                           'reviewable': row.get('error') is None,
                           'inspectable': isinstance(row.get('payload'), dict),
                           'detail': 'Unassociated proxy capture: no native session correlation; observation only.'})
        try:
            for name in ('journal.jsonl', 'local-journal.jsonl'):
                path = self.repo_root / 'trial' / name
                path_hash = hashlib.sha256(str(path).encode()).hexdigest()[:12]
                for row in self._read(name, journal=True):
                    stamp, index = number(row.get('time')), row.get('id')
                    if stamp is None or type(index) is not int or index < 0:
                        raise ValueError('invalid journal')
                    judge = row.get('judge')
                    failure = isinstance(judge, str) and (judge.lower().startswith('error:') or 'judge-adapter error' in judge.lower())
                    deterministic = 'judge' in row and judge is None and row.get('verdict') in ('flagged', 'blocked', 'suspicious')
                    linked = re.search(r'observation_id=([0-9a-f-]{36})(?![0-9a-f-])', judge) if isinstance(judge, str) else None
                    capture = next((r for r in records['calls'] if linked and r['id'] == linked.group(1)), None)
                    # Content-bound IDs never fall back to ambiguous legacy labels.
                    content_hash = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'),
                                                            ensure_ascii=False).encode('utf-8')).hexdigest()
                    cid = f'journal:{path_hash}:{index}:{content_hash}'
                    detail = ('Journal detection failure.' if failure else 'Judge not consulted; not model-scored.' if deterministic
                              else 'Proxy journal observation; not proof of tool interception.')
                    verdict = row.get('verdict') if row.get('verdict') in ('passed', 'suspicious', 'flagged', 'blocked') else None
                    outcome = next((value for value in ('allow', 'block', 'error')
                                    if isinstance(judge, str) and judge.startswith(value + ':')), None)
                    consultation = ('consulted' if outcome else 'not_consulted'
                                    if 'judge' in row and judge is None else 'unknown')
                    signals = [{'id': item['id'], 'severity': item['severity']}
                               for item in row.get('signals', []) if isinstance(item, dict)
                               and isinstance(item.get('id'), str)
                               and re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,5}', item['id'])
                               and item.get('severity') in ('info', 'low', 'medium', 'high', 'critical')]
                    events.append({'id': cid, 'time': stamp, 'session_id': None, 'turn_id': None, 'tool_call_id': None,
                                   'verdict': verdict, 'signals': signals, 'judge_consultation': consultation,
                                   # Validated metadata only: an invalid journal tool becomes
                                   # unavailable, never rendered as arbitrary title text.
                                   'tool': identifier(row.get('tool')),
                                   'judge_outcome': outcome, 'review_id': cid, 'journal_id': cid,
                                   'call_id': index, 'capture_id': capture['id'] if capture else None,
                                   'identity_status': 'matched' if capture else 'partial',
                                   'review_aliases': [cid] + ([capture['id']] if capture else []),
                                   'review_conflict': False,
                                   'origin': 'verification' if capture and capture.get('origin') == 'verification' else 'traffic',
                                   'source': 'safeagents', 'status': 'error' if failure else 'observed',
                                   'execution': 'not_intercepted', 'scores': [],
                                   'would_flag': verdict in ('flagged', 'blocked') if verdict is not None else None,
                                   'human_label': labels.get(cid),
                                   'reviewable': True, 'inspectable': False, 'detail': detail + ' Unassociated: no native session correlation.'})
            native = {}
            for row in self._read('security-events.jsonl'):
                cid = checked_id(row.get('id'))
                if cid.startswith('journal:') or number(row.get('time')) is None:
                    raise ValueError('invalid native record')
                if cid not in native or row['time'] >= native[cid]['time']:
                    native[cid] = row
            for cid, row in native.items():
                status = row.get('status')
                if status not in ('proposed', 'completed', 'tool_returned', 'error', 'blocked', 'cancelled', 'unknown'):
                    raise ValueError('invalid native status')
                execution = row.get('execution')
                if execution not in ('proposed', 'tool_returned', 'error', 'blocked', 'cancelled', 'unknown'):
                    execution = 'proposed' if status == 'proposed' else 'unknown'
                if status in ('blocked', 'error', 'cancelled'):
                    execution = status  # Legacy tool_returned never overrides a failed/blocked outcome.
                events.append({'id': cid, 'time': row['time'], 'session_id': identifier(row.get('session_id')),
                               'turn_id': identifier(row.get('turn_id')), 'tool_call_id': identifier(row.get('tool_call_id')),
                               'api_request_id': identifier(row.get('api_request_id')),
                               'origin': 'traffic', 'source': 'hermes', 'status': status,
                               'execution': execution,
                               'tool': identifier(row.get('tool')), 'scores': [],
                               'would_flag': None, 'human_label': None, 'reviewable': False, 'inspectable': False,
                               'detail': {'proposed': 'Tool proposed; no contextual enforcement',
                                          'tool_returned': 'Tool returned; no independent execution proof.',
                                          'blocked': 'Tool blocked.', 'cancelled': 'Tool cancelled.',
                                          'unknown': 'Execution outcome unknown.',
                                          'completed': 'Tool returned successfully', 'error': 'Tool returned an error'}[status]})
        except (OSError, ValueError, UnicodeError, SecurityError, TypeError):
            subsystems['activity'] = {'healthy': False, 'detail': 'Activity records unavailable or invalid.'}
            raise SecurityError(503, {'healthy': False, 'subsystems': subsystems}) from None
        return self._merge_findings(events, records['labels'] or [])

    @staticmethod
    def _merge_findings(events, label_rows):
        captures = {event['id']: event for event in events if event['source'] == 'safeagents'
                    and not event['id'].startswith('journal:')}
        capture_counts = {}
        for event in events:
            if event['id'] in captures:
                capture_counts[event['id']] = capture_counts.get(event['id'], 0) + 1
        references = {}
        for event in events:
            if event.get('capture_id'):
                references.setdefault(event['capture_id'], []).append(event)
        merged, consumed = [], set()
        for event in events:
            capture = captures.get(event.get('capture_id'))
            if capture and len(references[capture['id']]) == 1 and capture_counts[capture['id']] == 1:
                consumed.add(capture['id'])
                event.update(scores=capture['scores'], inspectable=capture['inspectable'])
            elif capture:
                event.update(identity_status='conflict', inspectable=False, reviewable=False,
                             review_aliases=[event['id']])
            if capture_counts.get(event['id'], 0) > 1:
                event.update(identity_status='conflict', inspectable=False, reviewable=False, scores=[])
            aliases = event.get('review_aliases', [event['id']])
            alias_rows = [row for row in label_rows if row['id'] in aliases]
            latest = {row['id']: row['label'] for row in alias_rows}
            # One atomic label row can explicitly reconcile all aliases. Historical
            # labels remain recorded, and subsequent alias edits revive a conflict.
            if alias_rows and alias_rows[-1].get('review_aliases') == aliases:
                latest = {alias: alias_rows[-1]['label'] for alias in aliases}
            event['review_labels'] = latest
            distinct = set(latest.values())
            event['review_conflict'] = len(distinct) > 1
            event['human_label'] = next(iter(distinct)) if len(distinct) == 1 else None
            merged.append(event)
        return [event for event in merged if event['id'] not in consumed]

    def events(self, session_id=None, all_sessions=False, include_probes=False, limit=50):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise SecurityError(400, 'Invalid event limit.')
        records, subsystems = self._snapshot()
        rows = [r for r in self._event_rows(records, subsystems) if include_probes or r['origin'] != 'verification']
        session_id = identifier(session_id)
        unassociated = sum(r['session_id'] is None for r in rows)
        selected = [r for r in rows if all_sessions or (session_id is not None and r['session_id'] == session_id)]
        return {'events': sorted(selected, key=lambda r: (r['time'], r['id']), reverse=True)[:limit],
                'unassociated_count': unassociated, 'healthy': True, 'subsystems': subsystems}

    def _external_replay_running(self):
        """Probe without creating a lock or disturbing a child's inherited lock."""
        self._check_store()
        path = self.store_path / 'replay.lock'
        self._safe_path(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError('unsafe lock')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False

    def _poll_replay(self):
        if self._process is not None and self._job['state'] == 'running':
            code = self._process.poll()
            if code is not None:
                self._job.update(state='completed' if code == 0 else 'failed',
                                 finished_at=time.time(), exit_code=code,
                                 detail='Replay completed.' if code == 0 else 'Replay failed; review owner-only local logs.')
                if self._job_lock_file is not None:
                    self._job_lock_file.close()
                    self._job_lock_file = None
                self._process = None
            else:
                return dict(self._job)
        try:
            external = self._external_replay_running()
        except OSError:
            return {'state': 'unavailable', 'started_at': None, 'finished_at': None,
                    'exit_code': None, 'model': None, 'detail': 'Replay lock unavailable or invalid.'}
        if external:
            self._job = {'state': 'running', 'started_at': None, 'finished_at': None,
                         'exit_code': None, 'model': None,
                         'detail': 'Replay lock held; ownership, model and start time unknown.'}
        elif self._job['state'] == 'running':
            self._job = {'state': 'idle', 'started_at': None, 'finished_at': None,
                         'exit_code': None, 'model': None,
                         'detail': 'Previously observed replay lock released; outcome unknown.'}
        return dict(self._job)

    def replay_status(self):
        with self._replay_lock:
            records, subsystems = self._snapshot()
            available = records['calls'] is not None and records['replays'] is not None
            result = self._poll_replay()
            available = available and result['state'] != 'unavailable'
            models = [{'id': value['id'], 'label': value['label']}
                      for value in model_catalog().MODEL_SPECS.values()]
            result.update(available=available, subsystems=subsystems, models=models)
            if not available and result['state'] not in ('running', 'unavailable'):
                result.update(state='unavailable', detail='Capture or replay records unavailable or invalid.')
            return result

    def start_replay(self, model=None):
        try:
            model_catalog().get_model(model)
        except (TypeError, ValueError):
            raise SecurityError(400, 'Choose a supported replay model explicitly.')
        with self._replay_lock:
            if self._poll_replay()['state'] == 'running':
                raise SecurityError(409, 'Replay already running.')
            records, subsystems = self._snapshot()
            if records['calls'] is None or records['replays'] is None:
                raise SecurityError(503, {'healthy': False, 'subsystems': subsystems})
            lockfile = None
            try:
                script = self.repo_root / 'trial/replay_model.py'
                self._safe_path(script)
                if not script.is_file():
                    raise OSError('missing trusted replay')
                lockpath = self.store_path / 'replay.lock'
                self._safe_path(lockpath)
                fd = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                lockfile = os.fdopen(fd, 'r+')
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise OSError('unsafe lock')
                try:
                    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SecurityError(409, 'Replay already running.') from None
                logpath = self.store_path / ('replay-' + str(uuid.uuid4()) + '.log')
                fd = os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as output:
                    self._process = subprocess.Popen(
                        [str(self.repo_root / '.venv-trial/bin/python'), str(script), '--model', model],
                        cwd=self.repo_root, stdin=subprocess.DEVNULL, stdout=output,
                        stderr=subprocess.STDOUT, shell=False, close_fds=True,
                        pass_fds=(lockfile.fileno(),))
                self._job_lock_file = lockfile
                self._job = {'state': 'running', 'started_at': time.time(), 'finished_at': None,
                             'exit_code': None, 'model': model,
                             'detail': 'Offline replay running; live judge unchanged; no captured tool is executed.'}
                return dict(self._job)
            except Exception as exc:
                if lockfile is not None:
                    lockfile.close()
                if isinstance(exc, SecurityError):
                    raise
                self._job = {'state': 'failed', 'started_at': time.time(), 'finished_at': time.time(),
                             'exit_code': None, 'model': model, 'detail': 'Replay could not be started.'}
                raise SecurityError(503, 'Replay could not be started.') from None

    def inspect(self, event_id, include_content=False):
        checked_id(event_id)
        if include_content is not True:
            raise SecurityError(400, 'Explicit content opt-in required.')
        try:
            calls = self._validate('calls', self._read('calls.jsonl'))
        except (OSError, ValueError, UnicodeError, SecurityError, TypeError):
            raise SecurityError(503, 'Capture records unavailable or invalid.') from None
        if sum(row['id'] == event_id for row in calls) > 1:
            raise SecurityError(409, 'Capture identity conflicts; inspection refused.')
        for row in calls:
            if row['id'] == event_id:
                if not isinstance(row.get('payload'), dict):
                    raise SecurityError(400, 'This event has no inspectable raw capture.')
                return bounded_inspection(row)
        raise SecurityError(400, 'This event has no inspectable raw capture.')

    def _load_trusted(self, name):
        import importlib.util
        path = self.repo_root / 'trial' / (name + '.py')
        self._safe_path(path)
        self._safe_path(path.with_name('decision_models.py'))
        spec = importlib.util.spec_from_file_location('_security_trusted_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def append(self, name, row):
        # observe.label's store interface, with no chmod/mkdir side effects.
        import fcntl
        self._check_store()
        if name != 'labels.jsonl':
            raise OSError('invalid target')
        path = self.store_path / name
        self._safe_path(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError('unsafe record')
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(row, allow_nan=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

    def label(self, event_id, value):
        if value not in ('authorized', 'unwanted', 'insufficient-context'):
            raise SecurityError(400, 'Invalid review label.')
        checked_id(event_id)
        records, subsystems = self._snapshot()
        if records['labels'] is None:
            raise SecurityError(503, {'healthy': False, 'subsystems': subsystems})
        event = self.event(event_id)
        if not event['reviewable']:
            raise SecurityError(400, 'This event is not reviewable.')
        try:
            aliases = event.get('review_aliases', [event['id']])
            if len(aliases) > 1:
                self.append('labels.jsonl', {'id': event['id'], 'label': value,
                                           'review_aliases': aliases, 'time': time.time()})
                return {'mode': 'log-only', 'id': event['id'], 'label': value}
            return self._load_trusted('observe').label(self, event_id, value)
        except (OSError, ValueError, ImportError):
            raise SecurityError(503, 'Local label write failed.') from None

    def event(self, event_id):
        checked_id(event_id)
        for row in self._event_rows():
            if row['id'] == event_id or (row.get('identity_status') == 'matched' and event_id in row.get('review_aliases', [])):
                return row
        raise SecurityError(404, 'Event not found.')


service = SecurityService()
