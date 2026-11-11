"""Metadata-only native observations. No approval authority, input capture or injection."""
import hashlib
import json
import logging
import os
from pathlib import Path
import importlib.util
import stat
import threading
import time
from urllib.parse import urlsplit
import uuid

LOG = logging.getLogger('local-agent-security')


if __package__:
    from .identity import identifier
else:
    # File-path fixture loaders do not provide a package for relative imports.
    _identity_spec = importlib.util.spec_from_file_location(
        'local_agent_security_identity', Path(__file__).with_name('identity.py'))
    _identity = importlib.util.module_from_spec(_identity_spec)
    _identity_spec.loader.exec_module(_identity)
    identifier = _identity.identifier


def observed_route(base_url, api_mode):
    if api_mode not in ('chat_completions', 'chat') or not isinstance(base_url, str):
        return None
    try:
        url = urlsplit(base_url)
        if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or url.username or url.password or url.query or url.fragment:
            return None
        if url.port == 8788 and url.path.rstrip('/') == '/api/coding/paas/v4':
            return 'safeagents-glm'
        if url.port == 8789 and url.path.rstrip('/') == '/v1':
            return 'safeagents-local'
    except ValueError:
        pass
    return None


class Telemetry:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()

    def _append(self, name, row):
        # Fixed filenames only; raw callback kwargs are never serialized.
        if self.directory.is_symlink():
            raise OSError('symlink refused')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.stat().st_uid != os.getuid():
            raise PermissionError('store owner mismatch')
        self.directory.chmod(0o700)
        data = (json.dumps(row, allow_nan=False) + '\n').encode()
        with self.lock:
            fd = os.open(self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'ab') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise PermissionError('invalid store target')
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())

    def pre_api_request(self, *, session_id='', turn_id='', api_request_id='', provider='', base_url='', api_mode='', **_private):
        if not identifier(session_id):
            return
        self._append('security-routes.jsonl', {
            'schema': 1, 'time': time.time(), 'session_id': identifier(session_id),
            'turn_id': identifier(turn_id), 'api_request_id': identifier(api_request_id),
            'provider': identifier(provider), 'api_mode': identifier(api_mode),
            'route': observed_route(base_url, api_mode), 'origin': 'native_hook', 'host_pid': os.getpid(),
        })

    def _tool(self, stage, *, tool_name='', session_id='', turn_id='', tool_call_id='', api_request_id=None, status=None, **_private):
        session_id, turn_id, tool_call_id, api_request_id = (
            identifier(value) for value in (session_id, turn_id, tool_call_id, api_request_id))
        if not session_id or not tool_call_id:
            return
        components = [session_id, turn_id, tool_call_id]
        if api_request_id is not None:
            components.append(api_request_id)
        key = json.dumps(components, separators=(',', ':'))
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'local-agent-security:' + key))
        if stage == 'pre':
            outcome = 'proposed'
        elif status in ('ok', 'success', 'completed'):
            outcome = 'completed'
        elif status in ('error', 'blocked', 'cancelled'):
            outcome = status
        else:
            outcome = 'unknown'
        execution = 'tool_returned' if outcome == 'completed' else outcome
        detail = {
            'proposed': 'Tool proposed; no contextual enforcement',
            'completed': 'Tool returned successfully; side effects not independently verified',
            'blocked': 'Tool blocked before execution; not executed',
            'cancelled': 'Tool cancelled; execution and side effects unknown',
            'error': 'Tool reported an error; side effects unknown',
            'unknown': 'Tool outcome unknown; execution and side effects unknown',
        }[outcome]
        self._append('security-events.jsonl', {
            'schema': 1, 'id': event_id, 'time': time.time(), 'session_id': identifier(session_id),
            'turn_id': turn_id, 'tool_call_id': tool_call_id, 'api_request_id': api_request_id,
            'source': 'hermes', 'origin': 'traffic', 'tool': identifier(tool_name),
            'status': outcome, 'execution': execution, 'detail': detail,
            'reviewable': False,
        })

    def pre_tool_call(self, **kwargs):
        self._tool('pre', **kwargs)
        return None

    def post_tool_call(self, **kwargs):
        self._tool('post', **kwargs)
        return None


def register_observers(ctx, collector):
    def guarded(name):
        def observe(**kwargs):
            try:
                getattr(collector, name)(**kwargs)
            except Exception as exc:
                # Observation is fail-open by design, not a policy gate.
                LOG.warning('Security observation unavailable (%s); execution not intercepted', type(exc).__name__)
            return None
        return observe
    for event in ('pre_api_request', 'pre_tool_call', 'post_tool_call'):
        ctx.register_hook(event, guarded(event))
