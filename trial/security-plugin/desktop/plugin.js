import { Button, Tip, Codicon, ScrollArea, host, useQuery, useValue } from '@hermes/plugin-sdk';
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
function sourceLabel(source) {
  const labels = { hermes: 'Hermes lifecycle', safeagents: 'SafeAgents', proxy: 'Proxy capture', replay: 'Replay' };
  return Object.hasOwn(labels, source) ? labels[source] : source;
}
function scoreAbsence(row) {
  if (row.events.some(event => Object.hasOwn(event, 'lite_score') || Object.hasOwn(event, 'qev_score'))) return 'Unknown model · score provenance unavailable';
  const nativeOnly = row.events.every(event => event.source === 'hermes' && event.reviewable === false && event.inspectable !== true
    && (event.scores === undefined || (Array.isArray(event.scores) && event.scores.length === 0)));
  return nativeOnly ? null : 'No model scores recorded';
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
  return judge?.state === 'available' && label !== 'Unknown model' ? `${label} · Ready` : 'Unknown model';
}
function healthComponents(status) {
  const labels = { proxy_glm: 'GLM observation proxy', proxy_local: 'Local observation proxy', decision: 'Local decision model', capture: 'Observation adapter', store: 'Private capture store', recorder: 'Capture recording', activity: 'Activity records', review: 'Review records', replay: 'Replay records' };
  const components = [...(Array.isArray(status?.components) ? status.components : []),
    ...Object.entries(status?.subsystems || {}).map(([id, value]) => ({ ...value, id, label: labels[id] || id }))];
  for (const [id, label] of Object.entries(labels)) if (!components.some(component => component.id === id)) components.push({ id, label });
  return components.map(component => ({ ...component, label: component.label || labels[component.id] || component.id }));
}
function componentState(component, error, stale) {
  return error ? 'Unknown (request failed)' : stale ? 'Unknown (stale)' : component.healthy === true ? 'Healthy' : component.healthy === false ? 'Unavailable' : 'Unknown';
}
function healthGroups(components, observation, error, stale) {
  const storage = component => ['store', 'activity', 'review', 'replay', 'recorder'].includes(component.id);
  const routeIds = ['proxy_glm', 'proxy_local', 'decision', 'capture'];
  // Only these backend states represent fresh native route evidence. Provider
  // alone can be a stale or database-only last-recorded value, not a live route.
  const proxy = !error && !stale && ['observing', 'degraded'].includes(observation?.state)
    ? observation.provider === 'safeagents-glm' ? 'proxy_glm' : observation.provider === 'safeagents-local' ? 'proxy_local' : null : null;
  return [
    ...(proxy ? [
      { label: 'Observed route', components: components.filter(component => [proxy, 'decision', 'capture'].includes(component.id)) },
      { label: 'Other route', components: components.filter(component => ['proxy_glm', 'proxy_local'].includes(component.id) && component.id !== proxy) },
    ] : [{ label: 'Route unconfirmed', components: components.filter(component => routeIds.includes(component.id)) }]),
    { label: 'Components', components: components.filter(component => !storage(component) && !routeIds.includes(component.id)) },
    { label: 'Storage', components: components.filter(storage) },
  ].filter(group => group.components.length);
}
function attentionView(value) {
  return value && typeof value.healthy === 'boolean' && ['current_managed_run', 'stored_history'].includes(value.scope)
    && (value.needs_review === null || Number.isInteger(value.needs_review) && value.needs_review >= 0)
    && Array.isArray(value.events) && value.events.every(event => event && typeof event.id === 'string'
      && Array.isArray(event.signals) && event.signals.every(signal => signal && typeof signal.id === 'string' && typeof signal.severity === 'string'))
    ? value : null;
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
const stateValue = { fontSize: 11, fontWeight: 500, overflowWrap: 'anywhere' };
const summary = { ...muted, cursor: 'pointer', padding: '5px 0', fontSize: 11 };
const flow = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4, minWidth: 0 };
const compactButton = { maxWidth: '100%', height: 'auto', minHeight: 24, padding: '3px 6px', fontSize: 11, whiteSpace: 'normal', textAlign: 'left' };

function stateColor(value) {
  if (value === 'Healthy' || value.endsWith(' · Ready')) return 'var(--ui-green)';
  if (value === 'Observing') return 'var(--ui-accent)';
  if (/unavailable|degraded|stale|failed|mismatch/i.test(value)) return 'var(--ui-yellow)';
  return 'var(--ui-text-secondary)';
}
function stateCue(value) {
  return jsx('span', { 'aria-hidden': true, 'data-state-cue': value, style: { width: 5, height: 5, borderRadius: '50%', flexShrink: 0, background: stateColor(value) } });
}
function statusLine(label, value, icon, ariaLabel = `${label} status`) {
  return jsxs('div', { 'aria-label': ariaLabel, style: { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', alignItems: 'center', gap: 8, padding: '5px 0' }, children: [
    jsxs('span', { style: { ...flow, ...muted, flexWrap: 'nowrap', gap: 7, fontSize: 11 }, children: [jsx(Codicon, { name: icon, size: 13, 'aria-hidden': true, style: { flexShrink: 0, opacity: 0.75 } }), jsx('span', { children: label })] }),
    jsxs('span', { style: { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 5, minWidth: 0 }, children: [stateCue(value), jsx('span', { style: { ...stateValue, color: stateColor(value), textAlign: 'right' }, children: value })] }),
  ] }, label);
}

function countGrid(entries, ariaLabel) {
  return jsx('div', { 'aria-label': ariaLabel, style: { display: 'grid', gridTemplateColumns: `repeat(${entries.length}, minmax(0, 1fr))`, gap: 4, padding: '8px 0' }, children: entries.map(([label, value]) => jsxs('div', { style: { minWidth: 0 }, children: [
    jsx('div', { style: { fontSize: 16, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }, children: Number.isInteger(value) && value >= 0 ? value : 'Unknown' }),
    jsx('div', { style: { ...muted, fontSize: 10, overflowWrap: 'anywhere' }, children: label }),
  ] }, label)) });
}
function scopeTrim(value, sessionId) {
  // Hermes composes turn/api-request IDs as session:task:turn; when the task
  // equals the session the prefix renders twice. Trim one leading repetition
  // for display only — the stored identifiers stay untouched.
  if (typeof value !== 'string' || !sessionId || !value.startsWith(sessionId + ':')) return value;
  return value.slice(sessionId.length + 1);
}

function monitoringSections(status, unavailable) {
  const monitoring = !unavailable ? status?.monitoring : null;
  const active = monitoring?.scope === 'current_managed_run' && ['verified', 'degraded'].includes(monitoring?.state);
  const guard = active ? monitoring?.guard : null;
  const judge = active ? monitoring?.judge : null;
  const policy = active ? monitoring?.policy : null;
  const route = !unavailable ? status?.route_activity : null;
  const enabled = value => value === true ? 'Enabled' : value === false ? 'Disabled' : 'Unknown';
  const quiet = unavailableText => [
    jsx('div', { style: { ...muted, fontSize: 11 }, children: 'No verified managed SafeAgents run.' }),
    jsx('div', { style: { ...muted, fontSize: 11 }, children: unavailableText }),
  ];
  return [
    jsxs('div', { 'aria-label': 'Guard monitoring', style: block, children: [
      jsx('h3', { style: heading, children: 'Guard' }),
      ...(active ? [
        jsx('div', { style: { ...muted, fontSize: 11 }, children: `Current managed run · ${monitoring.state === 'verified' ? 'Verified' : 'Degraded'}` }),
        countGrid([['Reviewed', guard?.reviewed], ['PASS', guard?.passed], ['WARN', guard?.warn], ['FLAG', guard?.flagged], ['BLOCK', guard?.blocked]]),
        jsx('div', { style: { ...muted, fontSize: 11 }, children: `${policy?.mode === 'log-only' ? 'Log-only' : policy?.mode === 'enforcing' ? 'Enforcing' : 'Policy unknown'} · Scanner: ${enabled(policy?.scanner_enabled)} · Judge: ${enabled(policy?.judge_enabled)} · Journal: ${enabled(policy?.journal_enabled)}` }),
        jsx('div', { style: { ...muted, fontSize: 11 }, children: `Block severity: ${policy?.block_severity || 'Unknown'} · Judge failure: ${policy?.judge_failure || 'Unknown'}` }),
      ] : quiet('Guard totals and policy unavailable.')),
      jsx('div', { style: { ...muted, fontSize: 11 }, children: monitoring?.detail || 'Managed-run monitoring unavailable; update/restart the Security backend if needed.' }),
    ] }),
    jsxs('div', { 'aria-label': 'Hermes route history', style: block, children: [
      jsx('h3', { style: heading, children: 'Hermes route history' }),
      jsx('div', { style: { ...muted, fontSize: 11 }, children: `${Number.isInteger(route?.attempts) && route.attempts >= 0 ? route.attempts : 'Unknown'} provider attempts · selected session · pre-request, not SafeAgents receipts · Last attempt: ${displayTime(route?.last_attempt)}` }),
    ] }),
    jsxs('div', { 'aria-label': 'Judge consultations', style: block, children: [
      jsx('h3', { style: heading, children: 'Judge' }),
      ...(active ? [
        jsx('div', { style: { ...muted, fontSize: 11 }, children: 'Current managed run · consultations, not runtime readiness' }),
        countGrid([['Consultations', judge?.consulted], ['Allow', judge?.allow], ['Block', judge?.block], ['Errors', judge?.errors]]),
        ...[['Last consultation', 'last_consultation'], ['Last success', 'last_success'], ['Last failure', 'last_failure']].map(([label, key]) => jsx('div', { style: { ...muted, fontSize: 11 }, children: `${label}: ${judge?.[key] ? `${displayTime(judge[key].time)} · ${judge[key].outcome}` : monitoring.state === 'verified' && judge?.consulted === 0 ? 'None in current managed run' : 'Unknown'}` }, key)),
      ] : quiet('Judge consultation totals unavailable.')),
    ] }),
  ];
}

function definitions(entries) {
  return jsx('dl', { style: { display: 'grid', gridTemplateColumns: 'minmax(0, 84px) minmax(0, 1fr)', gap: '4px 8px', margin: '6px 0', fontSize: 11 }, children: entries.flatMap(([label, value]) => [
    jsx('dt', { style: muted, children: `${label}:` }, `${label}:label`),
    jsx('dd', { style: { margin: 0, minWidth: 0, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', userSelect: 'text' }, children: value }, `${label}:value`),
  ]) });
}

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
    label: `Security · ${view.label}. Enforcement: ${view.enforcement}.`,
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
  // Scrolling follows the LLM Usage pane pattern: the root fills the tile as a
  // flex column and the SDK's ScrollArea owns the scrollbar (its thumb paints
  // reliably; raw overflowY on a height:100% wrapper produced stuck/missing
  // scrollbars, and no scroll container left the scrollbar unpainted).
  return visible ? jsx('div', { style: { height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', padding: 12, boxSizing: 'border-box', color: 'var(--ui-text-primary)', fontSize: 12 },
    children: jsx(ScrollArea, { style: { flex: '1 1 0', minHeight: 0 }, children: jsx(SecurityPanel, { ctx, scope, status }, JSON.stringify(scope)) }),
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
  const [busy, updateBusy] = useState(null);
  const busyAction = useRef(null);
  const [eventErrors, setEventErrors] = useState({});
  const [replayError, setReplayError] = useState('');
  const [replayModel, setReplayModel] = useState('');
  const [startingReplay, setStartingReplay] = useState(null);
  // Liveness heartbeat: re-render every few seconds while the pane is mounted
  // and its JS is alive. If the pane ever freezes (dead poller, caught render
  // error), the "Pane refresh" age in Diagnostics stops counting up and the
  // freeze becomes visible instead of silent (2026-11-12 click-deadness).
  const [paneTick, setPaneTick] = useState(() => Date.now());
  // Timer globals are absent in sandboxed render contexts (test VM); the
  // heartbeat is best-effort and must not break rendering without them.
  useEffect(() => {
    if (typeof setInterval !== 'function') return;
    const timer = setInterval(() => setPaneTick(Date.now()), 5000);
    return () => { if (typeof clearInterval === 'function') clearInterval(timer); };
  }, []);
  const replayPending = useRef(false);
  const alive = useRef(true);
  const ticket = useRef(0);
  const actionEpoch = useRef(0);
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
    const fresh = event?._attention
      ? attentionView(current.status.data.attention)?.events?.find(e => e.id === event.id)
      : current.events.data.events?.find(e => e.id === event?.id);
    return !!fresh && (kind === 'inspect' ? fresh.inspectable === true
        && (!event?._attention || typeof fresh.capture_id === 'string' && !!fresh.capture_id && fresh.capture_id === event.capture_id)
      : fresh.reviewable !== false && subsystems?.review?.healthy !== false
        && (!event?._attention || current.status.data.attention?.healthy === true));
  }
  const activityFailed = !!events.error || events.data?.subsystems?.activity?.healthy === false || status.data?.subsystems?.activity?.healthy === false;
  const statusUnavailable = !!status.error || !status.data || coverageView(status.data, null).stale;
  useEffect(() => { if (activityFailed || statusUnavailable) resetActivity(); }, [activityFailed, statusUnavailable]);
  function setEventError(id, kind, message) {
    setEventErrors(previous => ({ ...previous, [id]: { ...previous[id], [kind]: message } }));
  }
  function setBusy(value) {
    busyAction.current = value; updateBusy(value);
  }
  function resetActivity() {
    ticket.current++; actionEpoch.current++; setRaw(null); setBusy(replayPending.current ? 'replay' : null); setEventErrors({}); setReplayError('');
  }
  function actionCurrent(epoch) {
    return alive.current && sameScope(scope) && actionEpoch.current === epoch;
  }
  async function label(event, value) {
    if (busyAction.current !== null || !ready('review', event)) return;
    const epoch = actionEpoch.current;
    setBusy(event.id); setEventError(event.id, 'review', '');
    try {
      const result = await ctx.rest('/label', { method: 'POST', body: { id: event.id, value } });
      if (result?.success === false) throw new Error('Review was not saved');
      if (actionCurrent(epoch)) { await events.refetch(); if (event._attention && actionCurrent(epoch)) await latest.current.status.refetch(); }
    } catch (error) { if (actionCurrent(epoch)) setEventError(event.id, 'review', `Review failed: ${error.message || error}`); }
    finally { if (actionCurrent(epoch)) setBusy(null); }
  }
  async function runPending() {
    if (busyAction.current !== null || !ready('replay')) return;
    const epoch = actionEpoch.current;
    const selected = latest.current.replay.data.models.find(model => model.id === latest.current.replayModel);
    const model = selected.id;
    replayPending.current = true;
    setStartingReplay({ id: model, label: selected.label });
    setBusy('replay'); setReplayError('');
    try {
      await ctx.rest('/replay', { method: 'POST', body: { model } });
      if (alive.current && sameScope(scope)) await replay.refetch();
    } catch (error) { if (actionCurrent(epoch)) setReplayError(`Replay start failed: ${error.message || error}`); }
    finally {
      replayPending.current = false;
      if (alive.current && sameScope(scope)) { setStartingReplay(null); setBusy(null); }
    }
  }
  const view = coverageView(status.data, status.error, Date.now(), scopeReason(scope));
  const jobModel = startingReplay?.label || replay.data?.models.find(model => model.id === replay.data.model)?.label || 'Unknown model';
  const hasReplayJob = !!startingReplay || replay.data?.model != null || replay.data?.state === 'running';
  const judgeLabel = liveJudgeLabel(status.data?.judge, status.error, view.stale);
  const attention = statusUnavailable ? null : attentionView(status.data?.attention);
  const attentionEvents = Array.isArray(attention?.events) ? attention.events.filter(event => event && typeof event.id === 'string'
    && ['suspicious', 'flagged', 'blocked'].includes(event.verdict)
    && (includeProbes || !/verification|probe/i.test(event.origin || ''))) : [];
  // Attention is an unassociated detector feed, never a native session join.
  const attentionRows = attentionEvents.map(event => ({ key: `attention:${event.id}`, attention: true,
    events: [{ ...event, _attention: true }], sources: [event.source || 'safeagents'], scores: eventScores(event), reviewable: event.reviewable === true ? [event] : [] }));
  const attentionIds = new Set(attentionEvents.flatMap(event => [event.id, event.capture_id, event.journal_id, event.review_id, ...(Array.isArray(event.review_aliases) ? event.review_aliases : [])]).filter(id => typeof id === 'string'));
  const rows = activityRows(events.data?.events?.filter(event => event.source === 'hermes' || !attentionIds.has(event.id)), session, allSessions, includeProbes);
  const components = healthComponents(status.data);
  async function inspect(event) {
    if (busyAction.current !== null || !ready('inspect', event)) return;
    const request = ++ticket.current;
    setRaw(null); setBusy(event.id); setEventError(event.id, 'inspect', '');
    try {
      const capture = await ctx.rest('/inspect', { method: 'POST', body: { id: event.capture_id || event.id, include_content: true } });
      if (ready('inspect', event) && ticket.current === request) setRaw({ id: event.id, ...rawView(capture) });
    } catch (error) { if (alive.current && sameScope(scope) && ticket.current === request) setEventError(event.id, 'inspect', `Inspect failed: ${error.message || error}`); }
    finally { if (alive.current && sameScope(scope) && ticket.current === request) setBusy(null); }
  }
  function judgeSummary(event, scores) {
    const primaryScore = (scores || []).find(score => score && score.role === 'live') || (scores || [])[0];
    const model = primaryScore ? modelLabel(primaryScore.model) : null;
    const score = primaryScore && Number.isFinite(primaryScore.score) ? primaryScore.score : null;
    if (event.judge_consultation === 'consulted') {
      // Compact record of what the judge returned; the score is the judge's own
      // output, never an interpretation against the threshold.
      const outcome = ({ allow: 'Allow', block: 'Block', error: 'Error' })[event.judge_outcome] || 'Outcome unknown';
      return `Judge: ${[model && model !== 'Unknown model' ? model : null, outcome, score].filter(value => value !== null).join(' · ')}`;
    }
    if (event.judge_consultation === 'not_consulted')
      return event.would_flag === true || event.verdict === 'flagged' || event.verdict === 'blocked'
        ? 'Judge not consulted · blocking finding' : 'Judge not consulted';
    return 'Judge consultation unknown';
  }
  const renderRow = row => {
    const primary = row.events[0];
    const signals = Array.isArray(primary.signals) ? primary.signals : [];
    const verdictTag = ({ passed: 'PASS', suspicious: 'WARN', flagged: 'FLAG', blocked: 'BLOCK' })[primary.verdict];
    // Findings are signal-first: the primary detector signal names the finding,
    // the validated journal tool is secondary metadata.
    const title = row.attention
      ? (signals[0]?.id || verdictTag || 'SafeAgents finding')
      : (primary.tool || 'Captured call');
    // Bare inert rows (no judgment, no review, no inspection, no scores) have
    // nothing to organize behind a second disclosure: identity flattens.
    const flattenIdentity = !row.attention && !row.scores.length
      && row.events.every(event => event.reviewable === false && event.inspectable !== true
        && !eventErrors[event.id]);
    return jsx('article', { style: block, children: [
      jsxs('div', { style: { ...flow, justifyContent: 'space-between' }, children: [
        jsx('strong', { style: { minWidth: 0, overflowWrap: 'anywhere', fontWeight: 600 }, children: title }),
        jsx('span', { style: { ...muted, fontSize: 10 }, children: displayTime(primary.time) }),
      ] }),
      jsx('div', { style: { ...muted, fontSize: 11, marginTop: 3 }, children: row.attention
        ? `${primary.tool || 'Tool unknown'} · SafeAgents`
        : (row.sources.map(sourceLabel).join(' · ') || 'Source unknown') }),
      row.attention ? jsxs('div', { style: { fontSize: 11, marginTop: 3 }, children: [
        jsx('strong', { children: [verdictTag || 'Verdict unknown', signals[0]?.severity ? ` · ${signals[0].severity.toUpperCase()}` : ''].join('') }),
        ...signals.slice(1).map((signal, i) => jsx('div', { children: `${signal.id || 'Unknown signal'} · ${signal.severity || 'Unknown severity'}` }, i)),
        jsx('div', { children: judgeSummary(primary, row.scores) }),
        primary.review_conflict === true ? jsx('div', { style: { color: 'var(--ui-yellow)' }, children: 'Review conflict · historical annotations disagree' }) : null,
        ['partial', 'conflict'].includes(primary.identity_status) ? jsx('div', { style: muted, children: 'Not correlated to a Hermes session' }) : null,
      ] }) : null,
      !row.attention && (row.scores.length || scoreAbsence(row)) ? jsx('div', { style: { fontVariantNumeric: 'tabular-nums', margin: '4px 0' }, children: row.scores.length ? row.scores.map((score, index) => jsx('div', { children: scoreSummary(score) }, index)) : scoreAbsence(row) }) : null,
      !row.attention ? jsx('div', { style: { fontSize: 11 }, children: row.execution === 'Tool returned' ? 'Tool returned · effects unverified' : row.execution }) : null,
      jsxs('details', { children: [
        jsx('summary', { style: summary, children: row.reviewable.length ? 'Details & review' : 'Details' }),
        ...(row.attention && row.scores.length ? [
          jsx('div', { style: { ...muted, fontSize: 11 }, children: 'Judge' }),
          definitions(row.scores.map(score => [({ live: 'Live', replay: 'Replay' })[score.role] || 'Score',
            `${Number.isFinite(score.score) ? score.score : 'Unknown'} · Threshold: ${Number.isFinite(score.threshold) ? score.threshold : 'Unknown'} · provisional · Scored: ${displayTime(score.time)}`])),
        ] : []),
        ...(row.attention && ['partial', 'conflict'].includes(primary.identity_status)
          ? [jsx('div', { style: muted, children: `Identity status: ${primary.identity_status}` })] : []),
        ...(!flattenIdentity ? [
        ...row.events.map((event, index) => jsxs('div', { 'data-event-id': event.id, style: { paddingTop: 6 }, children: [
          // Activity rows carry their detail line at the first level; only the
          // raw IDs and per-event scores stay nested under Technical identity.
          !row.attention && event.detail ? jsx('div', { style: { ...muted, fontSize: 11 }, children: event.detail }) : null,
          event.reviewable !== false ? jsxs('div', { style: flow, children: [
            jsx('span', { style: { ...muted, width: '100%', fontSize: 11 }, children: `Post-hoc review: ${event.review_conflict === true ? 'Conflict' : event.human_label === 'authorized' ? 'Expected' : event.human_label || 'Not reviewed'}` }),
            jsx('span', { style: { ...muted, width: '100%', fontSize: 11 }, children: 'Annotation only · no effect on execution.' }),
            ...[['authorized', 'Expected'], ['unwanted', 'Unwanted'], ['insufficient-context', 'Insufficient context']].map(([value, text]) => jsx(Button, { variant: 'ghost', size: 'sm', style: { ...compactButton, border: event.review_conflict !== true && event.human_label === value ? '1px solid var(--ui-accent)' : '1px solid transparent', background: event.review_conflict !== true && event.human_label === value ? 'color-mix(in srgb, var(--ui-accent) 12%, transparent)' : 'transparent', fontWeight: event.review_conflict !== true && event.human_label === value ? 600 : 400 }, disabled: busy !== null || !ready('review', event), 'aria-pressed': event.review_conflict !== true && event.human_label === value, onClick: () => label(event, value), children: text }, value)),
          ] }) : null,
          eventErrors[event.id]?.review ? jsx('div', { role: 'alert', children: eventErrors[event.id].review }) : null,
          event.inspectable === true ? jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, disabled: busy !== null || !ready('inspect', event), onClick: () => inspect(event), children: 'Inspect raw capture' }) : null,
          eventErrors[event.id]?.inspect ? jsx('div', { role: 'alert', children: eventErrors[event.id].inspect }) : null,
          raw?.id === event.id && ready('inspect', event) ? jsxs('div', { 'aria-label': 'Local-only raw capture', children: [jsx('div', { style: { ...muted, fontSize: 11, marginTop: 6 }, children: 'Raw capture · local-only display' }), jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, onClick: () => { ticket.current++; setRaw(null); }, children: 'Hide raw capture' }), jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 240, overflowY: 'auto', padding: 8, border: '1px solid var(--ui-stroke-secondary)', borderRadius: 3, background: 'color-mix(in srgb, var(--ui-text-primary) 3%, transparent)', fontFamily: 'monospace', fontSize: 11, userSelect: 'text' }, children: raw.content }), raw.truncated ? jsx('div', { role: 'status', children: 'Raw capture truncated · response / display limit reached.' }) : null] }) : null,
        ] }, `${event.id}:${index}`)),
        jsxs('details', { style: { paddingTop: 6 }, children: [
          jsx('summary', { style: summary, children: 'Technical identity' }),
          definitions([['Session', primary.session_id || 'Without a session'],
                       ['Turn', scopeTrim(primary.turn_id, primary.session_id) || 'Unknown'],
                       ['Tool call', primary.tool_call_id || 'Unknown'],
                       ['API request', scopeTrim(primary.api_request_id, primary.session_id) || 'Unknown'],
                       ['Recorded status', [...new Set(row.events.map(e => e.status).filter(Boolean))].join(', ') || 'unknown']]),
          ...row.events.map(event => jsxs('div', { style: { paddingTop: 4 }, children: [
            definitions([['Event ID', event.id]]),
            row.attention && event.detail ? jsx('div', { style: muted, children: event.detail }) : null,
            ...eventScores(event).map((score, scoreIndex) => jsxs('div', { style: { ...muted, fontSize: 11, paddingTop: 4 }, children: [
              jsx('div', { children: scoreSummary(score) }),
              jsx('div', { children: `Threshold: ${Number.isFinite(score.threshold) ? score.threshold : 'Unknown'} · provisional · Scored: ${displayTime(score.time)}` }),
            ] }, scoreIndex)),
          ] }, `technical:${event.id}`)),
        ] }),
        ] : [
          // Bare lifecycle rows: nothing to organize behind a second toggle —
          // the identity IS the content, flattened into the first level.
          definitions([['Session', primary.session_id || 'Without a session'],
                       ['Turn', scopeTrim(primary.turn_id, primary.session_id) || 'Unknown'],
                       ['Tool call', primary.tool_call_id || 'Unknown'],
                       ['API request', scopeTrim(primary.api_request_id, primary.session_id) || 'Unknown'],
                       ['Recorded status', [...new Set(row.events.map(e => e.status).filter(Boolean))].join(', ') || 'unknown']]),
          ...row.events.map(event => jsxs('div', { style: { paddingTop: 4 }, children: [
            definitions([['Event ID', event.id]]),
            event.detail ? jsx('div', { style: { ...muted, fontSize: 11 }, children: event.detail }) : null,
            ...eventScores(event).map((score, scoreIndex) => jsxs('div', { style: { ...muted, fontSize: 11, paddingTop: 4 }, children: [
              jsx('div', { children: scoreSummary(score) }),
              jsx('div', { children: `Threshold: ${Number.isFinite(score.threshold) ? score.threshold : 'Unknown'} · provisional · Scored: ${displayTime(score.time)}` }),
            ] }, scoreIndex)),
          ] }, `flat:${event.id}`)),
        ]),
      ] }),
    ] }, row.key);
  };
  return jsxs('section', { 'aria-label': 'Security', style: { minWidth: 0, lineHeight: 1.45, overflowWrap: 'anywhere' }, children: [
    jsxs('div', { style: { ...flow, justifyContent: 'space-between', marginBottom: 6 }, children: [
      jsx('span', { style: muted, children: 'Session' }),
      jsx('span', { 'aria-label': 'Current session identifier', title: session || 'No focused stored session', style: { fontSize: 11, overflowWrap: 'anywhere' }, children: session ? (session.length > 12 ? `${session.slice(0, 12)}…` : session) : 'None selected' }),
    ] }),
    jsx('h3', { style: heading, children: 'Status' }),
    jsxs('div', { 'aria-label': 'Status overview', style: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 7, padding: '3px 9px', background: 'color-mix(in srgb, var(--ui-text-primary) 2%, transparent)' }, children: [
      statusLine('Observation', view.label, 'eye'),
      statusLine('Enforcement', view.enforcement, 'shield'),
      statusLine('Live judge', judgeLabel, 'pulse', 'Live judge'),
    ] }),
    jsx('p', { style: { ...muted, fontSize: 10, margin: '7px 0' }, children: 'Observation only · Hermes handles approvals.' }),
    scopeReason(scope) ? jsx('p', { children: scopeReason(scope) }) : null,
    status.error ? jsx('div', { role: 'alert', children: `Coverage request failed: ${status.error.message || status.error}` }) : null,
    jsx('div', { 'aria-label': 'Health summary', style: { fontSize: 11, marginTop: 8 }, children: healthGroups(components, status.data?.observation, status.error, view.stale).map(group => {
      const healthy = group.components.filter(component => componentState(component, status.error, view.stale) === 'Healthy').length;
      const names = { proxy_glm: 'GLM proxy', proxy_local: 'Local proxy', decision: 'Decision model', capture: 'Capture adapter', store: 'Private captures', recorder: 'Recorder', activity: 'Activity records', review: 'Review records', replay: 'Replay records' };
      return jsxs('div', { 'aria-label': group.label, style: { padding: '7px 0', borderTop: '1px solid var(--ui-stroke-secondary)' }, children: [
        jsxs('div', { style: { ...flow, justifyContent: 'space-between', gap: 6, marginBottom: 3 }, children: [
          jsxs('span', { style: { ...flow, ...muted, gap: 6, fontWeight: 500, fontSize: 10 }, children: [jsx(Codicon, { name: group.label === 'Storage' ? 'database' : group.label === 'Components' ? 'server' : 'git-branch', size: 12, 'aria-hidden': true }), jsx('span', { children: group.label })] }),
          healthy ? jsx('span', { style: { color: 'var(--ui-green)', fontSize: 10 }, children: `${healthy} healthy` }) : null,
        ] }),
        ...group.components.filter(component => componentState(component, status.error, view.stale) !== 'Healthy').map(component => {
          const value = componentState(component, status.error, view.stale);
          return jsxs('div', { 'aria-label': `${component.label}: ${value}`, title: `${component.label}: ${value}`, style: { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 8, alignItems: 'center', padding: '3px 0 3px 18px' }, children: [
            jsx('span', { style: { ...muted, minWidth: 0, overflowWrap: 'anywhere' }, children: Object.hasOwn(names, component.id) ? names[component.id] : component.label }),
            jsxs('span', { style: { display: 'flex', alignItems: 'center', gap: 5, maxWidth: 110, textAlign: 'right', color: stateColor(value), fontSize: 10 }, children: [stateCue(value), jsx('span', { children: value })] }),
          ] }, component.id);
        }),
      ] }, group.label);
    }) }),
    jsxs('details', { children: [jsx('summary', { style: summary, children: 'Diagnostics' }),
      jsx('div', { style: muted, children: status.data?.observation?.detail || '' }),
      jsx('div', { style: muted, children: `Last-recorded provider: ${status.data?.observation?.provider || 'Unknown'}` }),
      jsx('div', { style: muted, children: `Last checked: ${view.checked}${view.stale ? ' · stale / unavailable' : ''}` }),
      jsx('div', { style: muted, children: `Pane refresh: ${Math.max(0, Math.round((Date.now() - paneTick) / 1000))}s ago${Number.isFinite(status.data?.observed_at) ? ` · status data ${Math.max(0, Math.round(Date.now() / 1000 - status.data.observed_at))}s old` : ''}` }),
      jsx('div', { style: muted, children: `Live judge: ${status.data?.judge?.detail || 'No verified model identity recorded'}` }),
      jsx('div', { style: muted, children: `Failed judge attempts: ${Number.isInteger(attention?.failed_judge_attempts) ? attention.failed_judge_attempts : 'Unknown'} · recorded, excluded from review${Number.isInteger(status.data?.judge?.capture_errors) ? ` · capture write errors: ${status.data.judge.capture_errors}` : ''}` }),
      Number.isFinite(status.data?.judge?.threshold) ? jsx('div', { style: muted, children: `Live judge threshold: ${status.data.judge.threshold} · provisional` }) : null,
      ...components.map(component => jsx('div', { style: muted, children: `${component.label}: ${componentState(component, status.error, view.stale)}${status.error || view.stale ? ` · Last recorded: ${componentState(component, null, false)}` : ''} · ${component.detail || 'No diagnostics recorded'}` }, component.id)),
      jsx('p', { style: muted, children: 'This panel adds no permission system.' }),
    ] }),
    ...monitoringSections(status.data, statusUnavailable),
    jsxs('div', { 'aria-label': 'Needs review', style: block, children: [
      jsx('h3', { style: heading, children: 'Needs review' }),
      jsx('div', { style: { ...stateValue, marginBottom: 4 }, children: `${attention?.healthy === true && status.data?.subsystems?.review?.healthy !== false && Number.isInteger(attention.needs_review) && attention.needs_review >= 0 ? attention.needs_review : 'Unknown'} needs review` }),
      jsx('div', { style: { ...muted, fontSize: 11 }, children: `${attention?.scope === 'current_managed_run' ? 'Current managed run' : attention?.scope === 'stored_history' ? 'Stored history' : 'Scope unknown'} · Unassociated with Hermes sessions · not current-session findings` }),
      !attention ? jsx('div', { style: muted, children: 'Attention unavailable · no known review total.' }) : !attentionRows.length ? jsx('div', { style: muted, children: attention.healthy === true ? 'No recent attention events in this scope.' : 'Attention records unavailable.' }) : null,
      attention ? jsx('div', { style: { ...muted, fontSize: 11 }, children: `${attentionRows.length} recent findings shown · post-hoc annotations only` }) : null,
      ...attentionRows.map(renderRow),
    ] }),
    jsxs('div', { style: block, children: [
      jsx('h3', { style: heading, children: 'Activity' }),
      jsx('label', { style: { display: 'block' }, children: jsx('select', { disabled: !allowedScope(scope), 'aria-label': 'Activity scope', style: { width: '100%', maxWidth: '100%', minWidth: 0, padding: '4px 6px', borderRadius: 3, font: 'inherit', color: 'var(--ui-text-primary)', background: 'var(--ui-bg-input)', border: '1px solid var(--ui-stroke-secondary)' }, value: allSessions ? 'all' : 'current', onChange: e => { resetActivity(); setAllSessions(e.target.value === 'all'); }, children: [jsx('option', { value: 'current', children: 'Current session' }), jsx('option', { value: 'all', children: 'All sessions' })] }) }),
      jsxs('label', { style: { ...flow, marginTop: 6, fontSize: 11 }, children: [jsx('input', { style: { accentColor: 'var(--ui-accent)', margin: 0 }, type: 'checkbox', disabled: !allowedScope(scope), checked: includeProbes, onChange: e => { resetActivity(); setIncludeProbes(e.target.checked); } }), ' Include verification traffic'] }),
      jsxs('details', { children: [jsx('summary', { style: summary, children: `${events.data?.unassociated_count ?? 'Unknown'} events without a session · scope notes` }), jsx('div', { style: { ...muted, fontSize: 11 }, children: 'Events without a session appear in All sessions only. Up to 50 recent events; totals may cover a wider window.' })] }),
    ] }),

    events.error ? jsx('div', { role: 'alert', children: `Activity request failed: ${events.error.message || events.error}` }) : !events.data ? jsx('p', { children: 'Loading activity…' }) : !rows.length ? jsx('p', { children: !allSessions && !session ? 'Select a session or choose All sessions.' : 'No activity in this scope.' }) : null,
    ...rows.map(renderRow),
    jsxs('div', { 'aria-label': 'Evaluation & audit', style: block, children: [
      jsx('h3', { style: heading, children: 'Evaluation & audit' }),
      jsx('div', { style: { ...muted, fontSize: 11 }, children: 'Stored history · not current-run Guard traffic' }),
      countGrid([['Judge captures', statusUnavailable ? null : status.data?.counts?.traffic_captures], ['Verification', statusUnavailable ? null : status.data?.counts?.verification_captures], ['Replay', statusUnavailable ? null : status.data?.counts?.replay_attempts], ['Reviews', statusUnavailable ? null : Object.hasOwn(status.data?.counts || {}, 'reviewed_events') ? status.data.counts.reviewed_events : status.data?.counts?.label_events]], 'Capture counts'),
    ] }),
    jsxs('div', { 'aria-label': 'Replay controls', style: block, children: [
      jsx('h3', { style: heading, children: 'Replay' }),
      jsx('div', { style: { ...muted, fontSize: 11, marginBottom: 4 }, children: 'Manual · all sessions' }),
      jsxs('label', { style: { display: 'block', marginBottom: 4 }, children: [
        jsx('span', { style: muted, children: 'Replay model' }),
        jsx('select', { 'aria-label': 'Replay model', disabled: busy !== null || !ready('replay-choice'), value: replayModel,
          style: { width: '100%', maxWidth: '100%', minWidth: 0, padding: '4px 6px', borderRadius: 3, font: 'inherit', color: 'var(--ui-text-primary)', background: 'var(--ui-bg-input)', border: '1px solid var(--ui-stroke-secondary)' },
          onChange: e => { if (busyAction.current === null && ready('replay-choice')) { setReplayError(''); setReplayModel(e.target.value); } },
          children: [jsx('option', { value: '', children: 'Choose a model…' }), ...(replay.data?.models || []).map(model => jsx('option', { value: model.id, children: model.label }, model.id))],
        }),
      ] }),
      jsx('p', { style: { ...muted, fontSize: 11, margin: '4px 0' }, children: 'Replay does not change the live judge and does not execute captured tools.' }),
      jsx(Button, { variant: 'ghost', size: 'sm', style: compactButton, disabled: busy !== null || !ready('replay'), onClick: runPending, children: 'Replay pending captures' }),
      jsx('div', { style: muted, 'aria-live': 'polite', children: `Replay: ${startingReplay ? 'starting' : replay.data?.state || 'checking'}${hasReplayJob ? ` · ${jobModel}` : ''}${replay.data?.exit_code != null ? ` · exit ${replay.data.exit_code}` : ''}${replay.data?.detail ? ` · ${replay.data.detail}` : ''}` }),
      replay.data?.started_at ? jsx('div', { style: muted, children: `Started ${displayTime(replay.data.started_at)}${replay.data.finished_at ? ` · Finished ${displayTime(replay.data.finished_at)}` : ''}` }) : null,
      replay.error ? jsx('div', { role: 'alert', children: `Replay status failed: ${replay.error.message || replay.error}` }) : null,
      replayError ? jsx('div', { role: 'alert', children: replayError }) : null,
    ] }),
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
