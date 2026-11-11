import { Button, Tip, Codicon, host, useQuery, useValue } from '@hermes/plugin-sdk';
import { jsx, jsxs } from 'react/jsx-runtime';
import { useState, useRef, useEffect } from 'react';

// Plain ESM; evaluated by Hermes Desktop without a build step.
function activityRows(events, sessionId, allSessions, includeProbes) {
  const groups = new Map();
  for (const event of Array.isArray(events) ? events : []) {
    if (!allSessions && (!sessionId || event.session_id !== sessionId)) continue;
    if (!includeProbes && /verification|probe/i.test(event.origin || '')) continue;
    const key = JSON.stringify(event.tool_call_id ? [event.session_id, event.turn_id, event.tool_call_id, event.api_request_id || null] : ['event', event.id]);
    if (!groups.has(key)) groups.set(key, { key, events: [], sources: [], scores: [], flagged: false, execution: 'Execution unknown' });
    const row = groups.get(key);
    row.events.push(event);
    if (event.source && !row.sources.includes(event.source)) row.sources.push(event.source);
    row.scores.push(...eventScores(event));
    row.flagged ||= event.would_flag === true;
  }
  return [...groups.values()].map(row => ({
    ...row,
    reviewable: row.events.filter(e => e.reviewable !== false),
    execution: executionLabel(row.events),
  }));
}
function executionLabel(events) {
  if (events.some(e => e.execution === 'blocked' || e.status === 'blocked')) return 'Blocked · not executed';
  if (events.some(e => e.execution === 'cancelled' || e.status === 'cancelled')) return 'Cancelled · effects unknown';
  if (events.some(e => e.execution === 'error' || e.status === 'error')) return 'Error · effects unknown';
  if (events.some(e => e.execution === 'tool_returned')) return 'Tool returned';
  if (events.some(e => e.execution === 'proposed' || e.execution === 'not_confirmed')) return 'Proposed';
  if (events.every(e => e.execution === 'not_intercepted' || !e.execution) && events.some(e => e.execution === 'not_intercepted')) return 'Observed only — not stopped';
  return 'Execution unknown';
}
function modelLabel(model) {
  return (model?.id === 'lite' && model.label === 'D3 Lite') || (model?.id === 'qev' && model.label === 'QEV') ? model.label : 'Unknown model';
}
function eventScores(event) {
  return Array.isArray(event.scores) ? event.scores.filter(score => score && typeof score === 'object' && !Array.isArray(score)) : [];
}
function scoreSummary(score) {
  const role = score.role === 'live' ? 'Live' : score.role === 'replay' ? 'Replay' : 'Unknown role';
  const failed = score.error === true;
  const flag = failed ? 'Score failed' : score.would_flag === true ? 'Would flag' : score.would_flag === false ? 'Would not flag' : 'Flag unknown';
  return `${role} · ${modelLabel(score.model)} ${!failed && Number.isFinite(score.score) ? score.score : '—'} · ${flag} · provisional`;
}
function liveJudgeLabel(judge, error, stale) {
  if (error) return 'Unavailable';
  if (stale) return 'Unknown model · stale / unavailable';
  if (judge?.state === 'unavailable') return 'Unavailable';
  if (judge?.state === 'mismatch') return 'Unknown model · identity mismatch';
  const label = modelLabel(judge?.model);
  return judge?.state === 'available' && label !== 'Unknown model' ? `${label} · Available` : 'Unknown model';
}
function resourceValid(resource, value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  if (resource === 'events') return Array.isArray(value.events) && value.events.every(e => e && typeof e === 'object' && typeof e.id === 'string');
  if (resource === 'status') return !!value.observation && typeof value.observation.state === 'string' && (!value.components || Array.isArray(value.components));
  if (resource === 'replay') return typeof value.state === 'string' && typeof value.available === 'boolean'
    && Array.isArray(value.models) && value.models.length > 0
    && value.models.every(model => model && ['lite', 'qev'].includes(model.id) && typeof model.label === 'string' && model.label.trim())
    && new Set(value.models.map(model => model.id)).size === value.models.length
    && (value.model === null || ['lite', 'qev'].includes(value.model));
  return true;
}
function invalidResource(resource) {
  return new Error(`Invalid ${resource} response${resource === 'replay' ? ' · model selection unavailable; update/restart the Security backend.' : ''}`);
}
function rawView(capture) {
  const encoded = new TextEncoder().encode(JSON.stringify(capture, null, 2));
  const capped = encoded.byteLength > 256 * 1024;
  return { content: new TextDecoder().decode(capped ? encoded.slice(0, 256 * 1024 - 4) : encoded), truncated: capped || capture?.truncated === true };
}
const ID = 'local-agent-security';
const PANE_ID = `plugin-workspace:${ID}`;
const POLL_MS = 5000;
const muted = { color: 'var(--ui-text-secondary)' };
const block = { padding: '10px 0', borderTop: '1px solid var(--ui-stroke-secondary)', minWidth: 0 };
// Match LLM Usage's quiet tracked headings and native rail's compact metadata.
const heading = { margin: '0 0 6px', fontSize: 10, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ui-text-secondary)' };
const badge = { padding: '3px 6px', borderRadius: 3, background: 'color-mix(in srgb, var(--ui-text-primary) 5%, transparent)', border: '1px solid var(--ui-stroke-secondary)', fontSize: 11, fontWeight: 500, overflowWrap: 'anywhere' };
const summary = { ...muted, cursor: 'pointer', padding: '5px 0', fontSize: 11 };
const flow = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4, minWidth: 0 };
const compactButton = { maxWidth: '100%', height: 'auto', minHeight: 24, padding: '3px 6px', fontSize: 11, whiteSpace: 'normal', textAlign: 'left' };

function ownerPair(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && typeof value.connectionId === 'string' && value.connectionId.trim()
    && typeof value.profile === 'string' && value.profile.trim()
    ? [value.connectionId, value.profile] : [null, null];
}
let scopeSignature = '', scopeEpoch = 0;
function currentScope() {
  const owner = ownerPair(host.state.focusedSessionOwner?.get());
  // SINGLE-BACKEND, default-profile deployment only. These public atoms are
  // ambient presentation projections, not authoritative ctx.rest routing.
  // Compare them with the resolved owner as a guard; this is not a general
  // cross-backend transport guarantee (see SECURITY_APP.md).
  const ambient = ownerPair({ connectionId: host.state.connectionId?.get(), profile: host.state.profile?.get() });
  const scope = [owner[1], owner[0],
    host.state.focusedStoredSessionId?.get() ?? null,
    host.state.gateway.get(), ...ambient];
  const signature = JSON.stringify(scope);
  if (signature !== scopeSignature) { scopeSignature = signature; scopeEpoch++; }
  return [...scope, scopeEpoch];
}
function scopeReason(scope) {
  if (!scope[0] || !scope[1]) return 'Focused session owner is unresolved.';
  if (!scope[4] || !scope[5]) return 'Ambient connection/profile projection is unavailable.';
  if (scope[1] !== scope[4] || scope[0] !== scope[5]) return 'Focused session owner does not match the ambient connection/profile projection.';
  if (scope[0] !== 'default') return 'Only the default profile is supported by this single-backend deployment.';
  return '';
}
function allowedScope(scope) { return !scopeReason(scope) && sameScope(scope); }
function sameScope(scope) {
  return JSON.stringify(currentScope()) === JSON.stringify(scope);
}
function useResource(ctx, scope, resource, path, filters = []) {
  const query = useQuery({
    queryKey: [ID, ...scope, ...filters, resource],
    enabled: allowedScope(scope),
    queryFn: async () => {
      if (!allowedScope(scope)) throw new Error('Host scope changed or owner unavailable');
      const result = await ctx.rest(path);
      if (!allowedScope(scope)) throw new Error('Host scope changed or owner unavailable');
      if (!resourceValid(resource, result)) throw invalidResource(resource);
      return result;
    },
    refetchInterval: POLL_MS,
    staleTime: 3000,
    retry: 1,
  });
  if (!allowedScope(scope)) return { ...query, data: undefined, error: null };
  if (query.data !== undefined && !resourceValid(resource, query.data)) return { ...query, data: undefined, error: invalidResource(resource) };
  return query;
}
const absent = { get: () => null, listen: () => () => {}, subscribe: () => () => {} };
function useSecurityScope() {
  useValue(host.state.focusedSessionOwner || absent);
  useValue(host.state.profile || absent);
  useValue(host.state.connectionId || absent);
  useValue(host.state.gateway);
  useValue(host.state.focusedStoredSessionId || absent);
  return currentScope();
}
function SecurityChip({ ctx, togglePane }) {
  const scope = useSecurityScope();
  const visible = useValue(host.paneVisibility(PANE_ID));
  const status = useResource(ctx, scope, 'status', `/status?session_id=${encodeURIComponent(scope[2] || '')}`);
  const view = coverageView(status.data, status.error, Date.now(), scopeReason(scope));
  return jsx(Tip, {
    label: `Security · ${view.label}. Enforcement: ${view.enforcement}. ${visible ? 'Close' : 'Open'} sidebar panel.`,
    children: jsx(Button, { variant: 'ghost', size: 'sm', 'aria-label': `${visible ? 'Close' : 'Open'} Security`, 'aria-expanded': visible,
      style: { width: 24, minWidth: 24, height: 22, padding: 0, color: view.healthy ? 'var(--ui-accent)' : 'var(--ui-text-secondary)' },
      onClick: togglePane,
      children: jsx(Codicon, { name: 'shield', size: 14, 'aria-hidden': true }),
    }),
  });
}
function SecurityPane({ ctx }) {
  const scope = useSecurityScope();
  const visible = useValue(host.paneVisibility(PANE_ID));
  const status = useResource(ctx, scope, 'status', `/status?session_id=${encodeURIComponent(scope[2] || '')}`);
  // Hiding/closing a docked pane also unmounts raw inspection state.
  return visible ? jsx('div', { style: { height: '100%', minHeight: 0, overflowY: 'auto', padding: 12, boxSizing: 'border-box', color: 'var(--ui-text-primary)', fontSize: 12 },
    children: jsx(SecurityPanel, { ctx, scope, status }, JSON.stringify(scope)),
  }) : null;
}
function securityDock() {
  const sibling = ['plugin-workspace:llm-usage', 'llm-usage:pane-v2'].find(id => host.paneVisibility(id).get());
  return sibling ? { pane: sibling, pos: 'bottom' } : { pane: 'workspace', pos: 'right' };
}
function SecurityPanel({ ctx, scope, status }) {
  const [allSessions, setAllSessions] = useState(false);
  const [includeProbes, setIncludeProbes] = useState(false);
  const [raw, setRaw] = useState(null);
  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState('');
  const [replayModel, setReplayModel] = useState('');
  const [startingReplay, setStartingReplay] = useState(null);
  const replayPending = useRef(false);
  const alive = useRef(true);
  const ticket = useRef(0);
  useEffect(() => { alive.current = true; return () => { alive.current = false; ticket.current++; }; }, []);
  const session = scope[2];
  const params = new URLSearchParams({ session_id: session || '', all_sessions: String(allSessions), include_probes: String(includeProbes), limit: '50' });
  const events = useResource(ctx, scope, 'events', `/events?${params}`, [allSessions, includeProbes]);
  const replay = useResource(ctx, scope, 'replay', '/replay');
  const latest = useRef(null);
  latest.current = { status, events, replay, replayModel };
  function ready(kind, event) {
    const current = latest.current;
    if (!alive.current || !allowedScope(scope) || current.status.error || !current.status.data
      || coverageView(current.status.data, null).stale) return false;
    const subsystems = current.status.data.subsystems;
    if (kind === 'replay' || kind === 'replay-choice') return !replayPending.current && resourceValid('replay', current.replay.data) && !current.replay.error
      && current.replay.data.available === true && current.replay.data.state !== 'running' && subsystems?.replay?.healthy !== false
      && (kind === 'replay-choice' || current.replay.data.models.some(model => model.id === current.replayModel));
    if (current.events.error || !current.events.data || subsystems?.activity?.healthy === false
      || current.events.data.subsystems?.activity?.healthy === false
      || (current.events.dataUpdatedAt && Date.now() - current.events.dataUpdatedAt > 60000)) return false;
    const fresh = current.events.data.events?.find(e => e.id === event?.id);
    return !!fresh && (kind === 'inspect' ? fresh.inspectable === true
      : fresh.reviewable !== false && subsystems?.review?.healthy !== false);
  }
  const activityFailed = !!events.error || events.data?.subsystems?.activity?.healthy === false || status.data?.subsystems?.activity?.healthy === false;
  useEffect(() => { if (activityFailed || status.error) { ticket.current++; setRaw(null); setBusy(null); } }, [activityFailed, status.error]);
  async function label(event, value) {
    if (!ready('review', event)) return;
    setBusy(event.id); setActionError('');
    try {
      const result = await ctx.rest('/label', { method: 'POST', body: { id: event.id, value } });
      if (result?.success === false) throw new Error('Review was not saved');
      if (alive.current && sameScope(scope)) await events.refetch();
    } catch (error) { if (alive.current && sameScope(scope)) setActionError(`Review failed: ${error.message || error}`); }
    finally { if (alive.current && sameScope(scope)) setBusy(null); }
  }
  async function runPending() {
    if (!ready('replay')) return;
    const selected = latest.current.replay.data.models.find(model => model.id === latest.current.replayModel);
    const model = selected.id;
    replayPending.current = true;
    setStartingReplay({ id: model, label: selected.label });
    setBusy('replay'); setActionError('');
    try {
      await ctx.rest('/replay', { method: 'POST', body: { model } });
      if (alive.current && sameScope(scope)) await replay.refetch();
    } catch (error) { if (alive.current && sameScope(scope)) setActionError(`Replay start failed: ${error.message || error}`); }
    finally {
      replayPending.current = false;
      if (alive.current && sameScope(scope)) { setStartingReplay(null); setBusy(null); }
    }
  }
  const view = coverageView(status.data, status.error, Date.now(), scopeReason(scope));
  const jobModel = startingReplay?.label || replay.data?.models.find(model => model.id === replay.data.model)?.label || 'Unknown model';
  const hasReplayJob = !!startingReplay || replay.data?.model != null || replay.data?.state === 'running';
  const judgeLabel = liveJudgeLabel(status.data?.judge, status.error, view.stale);
  const rows = activityRows(events.data?.events, session, allSessions, includeProbes);
  const components = [...(Array.isArray(status.data?.components) ? status.data.components : []),
    ...Object.entries(status.data?.subsystems || {}).map(([id, value]) => ({ ...value, id, label: { activity: 'Activity records', review: 'Review records', replay: 'Replay records' }[id] || id }))];
  async function inspect(event) {
    if (!ready('inspect', event)) return;
    const request = ++ticket.current;
    setRaw(null); setBusy(event.id); setActionError('');
    try {
      const capture = await ctx.rest('/inspect', { method: 'POST', body: { id: event.id, include_content: true } });
      if (ready('inspect', event) && ticket.current === request) setRaw({ id: event.id, ...rawView(capture) });
    } catch (error) { if (alive.current && sameScope(scope) && ticket.current === request) setActionError(`Inspect failed: ${error.message || error}`); }
    finally { if (alive.current && sameScope(scope) && ticket.current === request) setBusy(null); }
  }
  return jsxs('section', { 'aria-label': 'Security', style: { minWidth: 0, lineHeight: 1.45, overflowWrap: 'anywhere' }, children: [
    jsxs('div', { style: { ...flow, justifyContent: 'space-between', marginBottom: 6 }, children: [
      jsx('span', { style: muted, children: 'Session' }),
      jsx('span', { 'aria-label': 'Current session identifier', title: session || 'No focused stored session', style: { fontSize: 11, overflowWrap: 'anywhere' }, children: session ? (session.length > 12 ? `${session.slice(0, 12)}…` : session) : 'None selected' }),
    ] }),
    jsx('h3', { style: heading, children: 'Coverage' }),
    ...[['Observation', view.label], ['Enforcement', view.enforcement]].map(([label, value]) => jsxs('div', { style: { ...flow, justifyContent: 'space-between', marginBottom: 5 }, children: [jsx('span', { style: muted, children: label }), jsx('span', { style: badge, children: value })] }, label)),
    jsxs('div', { 'aria-label': 'Live judge', style: { ...flow, justifyContent: 'space-between', marginBottom: 5 }, children: [jsx('span', { style: muted, children: 'Live judge' }), jsx('span', { style: badge, children: judgeLabel })] }),
    jsx('p', { style: { ...muted, fontSize: 11, margin: '6px 0' }, children: 'Observation is not blocking. Permissions stay in Hermes approval cards.' }),
    scopeReason(scope) ? jsx('p', { children: scopeReason(scope) }) : null,
    status.error ? jsx('div', { role: 'alert', children: `Coverage request failed: ${status.error.message || status.error}` }) : null,
    jsx('div', { style: { ...flow, gap: '4px 10px', padding: '4px 0' }, children: components.map(component => jsxs('span', { style: { ...flow, fontSize: 11 }, children: [
      jsx('span', { 'aria-hidden': true, style: { width: 5, height: 5, borderRadius: '50%', background: status.error || view.stale || component.healthy == null ? 'var(--ui-text-secondary)' : component.healthy ? 'var(--ui-green)' : 'var(--ui-red)' } }),
      `${component.label}: ${status.error || view.stale ? 'Unknown (stale)' : component.healthy === true ? 'Healthy' : component.healthy === false ? 'Unavailable' : 'Unknown'}`,
    ] }, component.id)) }),
    jsxs('details', { children: [jsx('summary', { style: summary, children: 'Diagnostics' }),
      jsx('div', { style: muted, children: status.data?.observation?.detail || '' }),
      jsx('div', { style: muted, children: `Last-recorded provider: ${status.data?.observation?.provider || 'Unknown'}` }),
      jsx('div', { style: muted, children: `Last checked: ${view.checked}${view.stale ? ' · stale / unavailable' : ''}` }),
      jsx('div', { style: muted, children: `Live judge: ${status.data?.judge?.detail || 'No verified model identity recorded'}` }),
      Number.isFinite(status.data?.judge?.threshold) ? jsx('div', { style: muted, children: `Live judge threshold: ${status.data.judge.threshold} · provisional` }) : null,
      ...components.map(component => jsx('div', { style: muted, children: `${component.label}: ${component.detail || 'No diagnostics recorded'}` }, component.id)),
      jsx('p', { style: muted, children: 'This panel adds no permission system.' }),
    ] }),
    jsx('div', { 'aria-label': 'Capture counts', style: { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 4, padding: '8px 0' }, children: [['Traffic', 'traffic_captures'], ['Verification', 'verification_captures'], ['Replay', 'replay_attempts'], ['Reviews', 'label_events']].map(([label, key]) => jsxs('div', { style: { minWidth: 0 }, children: [jsx('div', { style: { fontSize: 16, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }, children: (key === 'label_events' && Object.hasOwn(status.data?.counts || {}, 'reviewed_events') ? status.data.counts.reviewed_events : status.data?.counts?.[key]) ?? '—' }), jsx('div', { style: { ...muted, fontSize: 10, overflowWrap: 'anywhere' }, children: label })] }, key)) }),
    jsxs('div', { style: block, children: [
      jsx('h3', { style: heading, children: 'Activity scope' }),
      jsx('label', { style: { display: 'block' }, children: jsx('select', { disabled: !allowedScope(scope), 'aria-label': 'Activity scope', style: { width: '100%', maxWidth: '100%', minWidth: 0, padding: '4px 6px', borderRadius: 3, font: 'inherit', color: 'var(--ui-text-primary)', background: 'var(--ui-bg-input)', border: '1px solid var(--ui-stroke-secondary)' }, value: allSessions ? 'all' : 'current', onChange: e => { ticket.current++; setRaw(null); setBusy(null); setAllSessions(e.target.value === 'all'); }, children: [jsx('option', { value: 'current', children: 'Current session' }), jsx('option', { value: 'all', children: 'All sessions' })] }) }),
      jsxs('label', { style: { ...flow, marginTop: 6, fontSize: 11 }, children: [jsx('input', { style: { accentColor: 'var(--ui-accent)', margin: 0 }, type: 'checkbox', disabled: !allowedScope(scope), checked: includeProbes, onChange: e => { ticket.current++; setRaw(null); setBusy(null); setIncludeProbes(e.target.checked); } }), ' Include verification traffic'] }),
      jsxs('details', { children: [jsx('summary', { style: summary, children: `${events.data?.unassociated_count ?? '—'} unassociated · scope notes` }), jsx('div', { style: { ...muted, fontSize: 11 }, children: 'Unassociated captures appear in All sessions only. Up to 50 recent events; totals may cover a wider window.' })] }),
    ] }),
    jsx('h3', { style: { ...heading, paddingTop: 8 }, children: 'Activity' }),
    events.error ? jsx('div', { role: 'alert', children: `Activity request failed: ${events.error.message || events.error}` }) : !events.data ? jsx('p', { children: 'Loading activity…' }) : !rows.length ? jsx('p', { children: !allSessions && !session ? 'Select a session or choose All sessions.' : 'No activity in this scope.' }) : null,
    ...rows.map(row => jsx('article', { style: block, children: [
      jsxs('div', { style: { ...flow, justifyContent: 'space-between' }, children: [
        jsx('strong', { style: { minWidth: 0, overflowWrap: 'anywhere', fontWeight: 600 }, children: row.events[0].tool || 'Captured call' }),
        jsx('span', { style: { ...muted, fontSize: 10 }, children: displayTime(row.events[0].time) }),
      ] }),
      jsx('div', { style: { ...muted, fontSize: 11, marginTop: 3 }, children: row.sources.join(' · ') || 'Source unknown' }),
      jsx('div', { style: { fontVariantNumeric: 'tabular-nums', margin: '4px 0' }, children: row.scores.length ? row.scores.map((score, index) => jsx('div', { children: scoreSummary(score) }, index)) : row.events.some(event => Object.hasOwn(event, 'lite_score') || Object.hasOwn(event, 'qev_score')) ? 'Unknown model · score provenance unavailable' : 'No model scores recorded' }),
      jsx('div', { style: { fontSize: 11 }, children: row.execution === 'Tool returned' ? 'Tool returned · effects unverified' : row.execution }),
      jsxs('details', { children: [
        jsx('summary', { style: summary, children: row.reviewable.length ? 'Details & review' : 'Details' }),
        jsx('div', { style: muted, children: `Session: ${row.events[0].session_id || 'Unassociated'} · Turn: ${row.events[0].turn_id || 'Unknown'} · Tool call: ${row.events[0].tool_call_id || 'Unknown'} · API request: ${row.events[0].api_request_id || 'Unknown'}` }),
        jsx('div', { style: muted, children: `Recorded status: ${[...new Set(row.events.map(e => e.status).filter(Boolean))].join(', ') || 'unknown'}` }),
        ...row.events.map((event, index) => jsxs('div', { style: { paddingTop: 6 }, children: [
          jsx('div', { style: muted, children: event.detail || '' }),
          ...eventScores(event).map((score, scoreIndex) => jsxs('div', { style: { ...muted, fontSize: 11, paddingTop: 4 }, children: [
            jsx('div', { children: scoreSummary(score) }),
            jsx('div', { children: `Threshold: ${Number.isFinite(score.threshold) ? score.threshold : 'Unknown'} · provisional · Scored: ${displayTime(score.time)}` }),
          ] }, scoreIndex)),
          event.reviewable !== false ? jsxs('div', { style: flow, children: [
            jsx('span', { style: { ...muted, width: '100%', fontSize: 11 }, children: `Post-hoc review: ${event.human_label === 'authorized' ? 'Expected' : event.human_label || 'Not reviewed'}` }),
            jsx('span', { style: { ...muted, width: '100%', fontSize: 11 }, children: 'Annotation only · no effect on execution.' }),
            ...[['authorized', 'Expected'], ['unwanted', 'Unwanted'], ['insufficient-context', 'Insufficient context']].map(([value, text]) => jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, disabled: busy !== null || !ready('review', event), 'aria-pressed': event.human_label === value, onClick: () => label(event, value), children: text }, value)),
          ] }) : null,
          event.inspectable === true ? jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, disabled: busy !== null || !ready('inspect', event), onClick: () => inspect(event), children: 'Inspect raw capture' }) : null,
          raw?.id === event.id && ready('inspect', event) ? jsxs('div', { children: [jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, onClick: () => { ticket.current++; setRaw(null); }, children: 'Hide raw capture' }), jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 240, overflowY: 'auto' }, children: raw.content }), raw.truncated ? jsx('div', { role: 'status', children: 'Raw capture truncated · response / display limit reached.' }) : null] }) : null,
        ] }, `${event.id}:${index}`)),
      ] }),
    ] }, row.key)),
    jsxs('div', { style: block, children: [
      jsx('h3', { style: heading, children: 'Replay' }),
      jsx('div', { style: { ...muted, fontSize: 11, marginBottom: 4 }, children: 'Manual · all sessions' }),
      jsxs('label', { style: { display: 'block', marginBottom: 4 }, children: [
        jsx('span', { style: muted, children: 'Replay model' }),
        jsx('select', { 'aria-label': 'Replay model', disabled: busy !== null || !ready('replay-choice'), value: replayModel,
          style: { width: '100%', maxWidth: '100%', minWidth: 0, padding: '4px 6px', borderRadius: 3, font: 'inherit', color: 'var(--ui-text-primary)', background: 'var(--ui-bg-input)', border: '1px solid var(--ui-stroke-secondary)' },
          onChange: e => { if (ready('replay-choice')) setReplayModel(e.target.value); },
          children: [jsx('option', { value: '', children: 'Choose a model…' }), ...(replay.data?.models || []).map(model => jsx('option', { value: model.id, children: model.label }, model.id))],
        }),
      ] }),
      jsx('p', { style: { ...muted, fontSize: 11, margin: '4px 0' }, children: 'Replay does not change the live judge and does not execute captured tools.' }),
      jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, disabled: busy !== null || !ready('replay'), onClick: runPending, children: 'Replay pending captures' }),
      jsx('div', { style: muted, 'aria-live': 'polite', children: `Replay: ${startingReplay ? 'starting' : replay.data?.state || 'checking'}${hasReplayJob ? ` · ${jobModel}` : ''}${replay.data?.exit_code != null ? ` · exit ${replay.data.exit_code}` : ''}${replay.data?.detail ? ` · ${replay.data.detail}` : ''}` }),
      replay.data?.started_at ? jsx('div', { style: muted, children: `Started ${displayTime(replay.data.started_at)}${replay.data.finished_at ? ` · Finished ${displayTime(replay.data.finished_at)}` : ''}` }) : null,
      replay.error ? jsx('div', { role: 'alert', children: `Replay status failed: ${replay.error.message || replay.error}` }) : null,
    ] }),
    actionError ? jsx('div', { role: 'alert', children: actionError }) : null,
  ] });
}
export default {
  id: ID,
  name: 'Security',
  register(ctx) {
    let closePane = null;
    const openPane = () => {
      closePane = host.openWorkspace(ID, { title: 'Security', dock: securityDock(), minWidth: '240px',
        render: () => jsx(SecurityPane, { ctx }), onClose: () => { closePane = null; },
      });
    };
    const togglePane = () => {
      if (host.paneVisibility(PANE_ID).get() && closePane) closePane();
      else if (closePane) host.revealPane(PANE_ID);
      else openPane();
    };
    openPane();
    ctx.onDispose?.(() => closePane?.());
    ctx.register({ id: 'security-status', area: 'statusBar.right', render: () => jsx(SecurityChip, { ctx, togglePane }) });
  },
};

function timeMillis(value) {
  return typeof value === 'number' ? (Math.abs(value) < 1e12 ? value * 1000 : value) : typeof value === 'string' ? Date.parse(value) : NaN;
}
function displayTime(value) {
  const stamp = timeMillis(value);
  return Number.isFinite(stamp) ? new Date(stamp).toLocaleString() : 'Unknown';
}
function coverageView(status, error, now = Date.now(), reason = '') {
  const checked = timeMillis(status?.observed_at);
  const stale = !Number.isFinite(checked) || now - checked > 60000 || checked > now + 5000;
  const state = status?.observation?.state;
  const labels = { observing: 'Observing', not_covered: 'Not covered', unknown: 'Coverage unknown', degraded: 'Degraded' };
  return {
    label: reason ? 'Not covered' : error ? 'Backend unavailable' : !status ? 'Checking coverage' : stale ? 'Coverage stale' : labels[state] || 'Coverage unknown',
    healthy: !reason && !error && !stale && state === 'observing',
    stale,
    checked: Number.isFinite(checked) ? new Date(checked).toLocaleString() : 'Not checked',
    // Observation is never evidence of enforcement. This trial has no blocking path.
    enforcement: 'Not enabled',
  };
}
