import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import vm from 'node:vm';
const file = new URL('./security-plugin/desktop/plugin.js', import.meta.url);
const replayModels = [{ id: 'lite', label: 'D3 Lite' }, { id: 'qev', label: 'QEV' }];
const scoreFixture = (role, id, score, extra = {}) => ({ role, model: { ...(replayModels.find(model => model.id === id) || { id: null, label: 'Unknown model' }), model_file_sha256: 'a'.repeat(64), native_template_sha256: 'b'.repeat(64) }, score, threshold: 0.88, would_flag: null, error: false, time: 1794355200, provisional: true, ...extra });
const replayButton = (h, tree = h.draw()) => h.nodes(tree).find(n => n.type === 'Button' && n.props.children === 'Replay pending captures');
const replaySelector = (h, tree = h.draw()) => h.nodes(tree).find(n => n.type === 'select' && n.props['aria-label'] === 'Replay model');
function chooseReplay(h, model) {
  const selector = replaySelector(h);
  assert.ok(selector, 'explicit Replay model selector exists');
  selector.props.onChange({ target: { value: model } });
  return h.draw();
}
function load(extra = {}) {
  const source = existsSync(file) ? readFileSync(file, 'utf8') : '';
  const context = vm.createContext({ Date, URLSearchParams, TextEncoder, TextDecoder, console, ...extra });
  vm.runInContext(source.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '').replace('export default', 'globalThis.plugin ='), context);
  return context;
}
test('activity filters scope and probes, groups call findings without conflating sessions', () => {
  const c = load();
  assert.equal(typeof c.activityRows, 'function');
  const events = [
    { id: 'a', session_id: 's', turn_id: 't', tool_call_id: 'call', source: 'proxy', origin: 'traffic', scores: [scoreFixture('live', 'qev', 0)], would_flag: true, execution: 'not_intercepted' },
    { id: 'b', session_id: 's', turn_id: 't', tool_call_id: 'call', source: 'replay', origin: 'traffic', scores: [scoreFixture('replay', 'lite', 0.9)] },
    { id: 'c', session_id: 'other', turn_id: 't', tool_call_id: 'call', origin: 'traffic' },
    { id: 'd', session_id: null, origin: 'traffic' },
    { id: 'e', session_id: 's', origin: 'verification' },
  ];
  const rows = c.activityRows(events, 's', false, false);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].events.length, 2);
  assert.ok(Array.isArray(rows[0].scores), 'preserve per-model score records');
  assert.equal(rows[0].scores.length, 2);
  assert.equal(rows[0].scores[0].score, 0);
  assert.equal(rows[0].scores[0].model.id, 'qev');
  assert.equal(rows[0].scores[0].role, 'live');
  assert.equal(rows[0].scores[1].score, 0.9);
  assert.equal(rows[0].scores[1].model.id, 'lite');
  assert.equal(rows[0].scores[1].role, 'replay');
  assert.equal('lite' in rows[0], false);
  assert.equal('qev' in rows[0], false);
  assert.equal(rows[0].execution, 'Observed only — not stopped');
  assert.equal(c.activityRows(events, 's', true, false).length, 3);
  assert.equal(c.activityRows(events, 's', true, true).length, 4);
  assert.equal(c.activityRows(events, null, false, true).length, 0);
});

test('native lifecycle updates are one call and do not claim side effects or offer capture actions', () => {
  const c = load();
  const events = [
    { id: 'same', session_id: 's', tool_call_id: 'c', source: 'hermes', status: 'proposed', execution: 'not_confirmed', reviewable: false },
    { id: 'same', session_id: 's', tool_call_id: 'c', source: 'hermes', status: 'completed', execution: 'tool_returned', reviewable: false },
  ];
  const row = c.activityRows(events, 's', false, false)[0];
  assert.equal(row.execution, 'Tool returned');
  assert.equal(row.reviewable.length, 0);
});

function harness(initialValues = {}) {
  const state = new Map(), queries = [], calls = [], effects = [], errors = {};
  let slots, cursor, visited;
  const el = (type, props = {}, key) => ({ type, props, key });
  const sdk = Object.fromEntries(['Button', 'Tip', 'Codicon', 'Popover', 'PopoverContent', 'PopoverTrigger'].map(k => [k, k]));
  const values = { profile: 'default', gateway: 'local', connectionId: 'local', activeSessionId: 'runtime-s', focusedStoredSessionId: 's', focusedSessionOwner: { connectionId: 'local', profile: 'default' }, ...initialValues };
  const host = { state: Object.fromEntries(Object.keys(values).map(k => [k, { get: () => values[k], listen: () => () => {} }])) };
  host.paneVisibility = id => ({ listen: () => () => {}, get: () => id === 'plugin-workspace:llm-usage' || (id === 'plugin-workspace:local-agent-security' && !!values.paneVisible) });
  host.revealPane = id => { assert.equal(id, 'plugin-workspace:local-agent-security'); values.paneVisible = true; };
  host.openWorkspace = (id, options) => {
    assert.equal(id, 'local-agent-security');
    const entry = { area: 'panes', title: options.title, data: { placement: 'main', dock: options.dock }, render: options.render };
    ctx.entries.push(entry); values.paneVisible = true;
    return () => { ctx.entries.splice(ctx.entries.indexOf(entry), 1); values.paneVisible = false; options.onClose?.(); };
  };
  const data = { status: { observed_at: new Date().toISOString(), observation: { state: 'observing' }, counts: {}, components: [] }, events: { events: [{ id: 'capture', session_id: 's', source: 'proxy', tool: 'read', reviewable: true, inspectable: true }], unassociated_count: 2 }, replay: { state: 'idle', available: true, models: replayModels.map(model => ({ ...model })), model: null } };
  const c = load({ ...sdk, host, jsx: el, jsxs: el,
    useValue: atom => { assert.equal(typeof atom.listen, 'function', 'useValue needs a nanostore-compatible atom'); return atom.get(); },
    useState: initial => { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; const owned = slots; return [slots[i], next => { owned[i] = typeof next === 'function' ? next(owned[i]) : next; }]; },
    useRef: value => { const i = cursor++; return slots[i] ||= { current: value }; },
    useEffect: (fn, deps) => { const i = cursor++; const old = slots[i]; if (!old || deps.some((v, j) => v !== old.deps[j])) { old?.cleanup?.(); const item = { deps }; slots[i] = item; effects.push(() => { item.cleanup = fn(); }); } },
    useQuery: options => { queries.push(options); return { data: data[options.queryKey.at(-1)], error: errors[options.queryKey.at(-1)] || null, refetch: async () => {} }; },
  });
  const ctx = { entries: [], rest: async (path, options) => { calls.push({ path, options }); return path === '/inspect' ? { content: 'LOCAL RAW SECRET' } : options?.method === 'POST' ? { success: true } : data[path.split('?')[0].slice(1)]; }, register: entry => { ctx.entries.push(entry); if (entry.area === 'statusBar.right') ctx.entry = entry; return () => {}; } };
  function render(node, path = 'root') {
    if (node == null || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map((n, i) => render(n, `${path}.${i}`));
    if (typeof node.type === 'function') {
      const id = `${path}:${node.type.name}:${node.key || ''}`;
      visited.add(id);
      if (!state.has(id)) state.set(id, []);
      slots = state.get(id); cursor = 0;
      return render(node.type(node.props), `${id}.out`);
    }
    return { ...node, props: { ...node.props, children: render(node.props.children, `${path}.children`) } };
  }
  function draw() {
    visited = new Set();
    const tree = render([ctx.entry.render(), values.paneVisible ? ctx.entries.find(e => e.area === 'panes')?.render() : null]);
    for (const [id, hooks] of state) if (!visited.has(id)) { hooks.forEach(hook => hook?.cleanup?.()); state.delete(id); }
    while (effects.length) effects.shift()();
    return tree;
  }
  function nodes(tree) { return tree == null || typeof tree !== 'object' ? [] : Array.isArray(tree) ? tree.flatMap(nodes) : [tree, ...nodes(tree.props?.children)]; }
  const open = () => { if (values.paneVisible) return; nodes(draw()).find(n => n.type === 'Button' && n.props['aria-label'] === 'Open Security').props.onClick(); };
  const close = () => { values.paneVisible = false; draw(); };
  return { c, ctx, calls, queries, values, data, errors, host, draw, nodes, open, close };
}
test('unresolved or mismatched owner disables all queries and direct mutation callbacks without REST', async () => {
  for (const owner of [null, 'default', {}, { profile: 'default' }, { connectionId: 'B', profile: 'default' }]) {
    const h = harness(); h.c.plugin.register(h.ctx);
    const callbacks = h.nodes(h.draw()).filter(n => n.type === 'Button' && n.props.onClick && !n.props['aria-label']).map(n => n.props.onClick);
    h.values.focusedSessionOwner = owner;
    h.queries.length = 0;
    const tree = h.draw();
    assert.match(visibleText(tree), /Not covered/);
    assert.ok(h.queries.every(q => q.enabled === false));
    assert.ok(h.nodes(tree).filter(n => ['select', 'input'].includes(n.type)).every(n => n.props.disabled === true));
    for (const q of h.queries) await assert.rejects(q.queryFn(), /scope|owner/i);
    for (const callback of callbacks) await callback();
    assert.equal(h.calls.length, 0);
    assert.equal(h.nodes(tree).some(n => n.type === 'article'), false, 'ignore cached activity');
  }
});

test('single-backend default-profile polling works with public SDK atoms only', async () => {
  const h = harness();
  assert.equal('apiRequestScope' in h.host.state, false);
  h.c.plugin.register(h.ctx); h.draw();
  assert.ok(h.queries.every(q => q.enabled === true));
  for (const resource of ['status', 'events', 'replay']) await h.queries.find(q => q.queryKey.at(-1) === resource).queryFn();
  assert.equal(h.calls.length, 3, 'mocked REST dispatch, not proof of real transport routing');
});

test('missing or mismatching public projections and unsupported profiles make zero REST calls', async () => {
  for (const values of [
    { connectionId: null }, { profile: null },
    { connectionId: 'B' }, { profile: 'other' },
    { profile: 'other', focusedSessionOwner: { connectionId: 'local', profile: 'other' } },
  ]) {
    const h = harness(values); h.c.plugin.register(h.ctx);
    const tree = h.draw();
    assert.match(visibleText(tree), /Not covered/);
    assert.ok(h.queries.every(q => q.enabled === false));
    for (const q of h.queries) await assert.rejects(q.queryFn());
    assert.equal(h.calls.length, 0);
  }
});

test('owner A to B with identical profile and stored session remounts raw and rejects late queries', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  await h.nodes(h.draw()).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  assert.match(JSON.stringify(h.draw()), /LOCAL RAW SECRET/);
  let resolve;
  h.ctx.rest = () => new Promise(done => { resolve = done; });
  const read = h.queries[0].queryFn();
  h.values.focusedSessionOwner = { connectionId: 'B', profile: 'default' };
  h.values.connectionId = 'B';
  const tree = h.draw();
  assert.doesNotMatch(JSON.stringify(tree), /LOCAL RAW SECRET/);
  resolve({}); await assert.rejects(read, /scope/i);
  assert.notEqual(JSON.stringify(h.queries[0].queryKey), JSON.stringify(h.queries.at(-4).queryKey));
});
test('query scope includes connection identity rather than only socket status', () => {
  const h = harness({ gateway: 'connected', connectionId: 'wsl-source-one' });
  assert.ok(h.c.currentScope().includes('wsl-source-one'));
  h.values.connectionId = 'another-source';
  assert.equal(h.c.sameScope(['default', 'wsl-source-one', 's', 'connected']), false);
});

test('backend lookup uses durable focused stored session ID, not runtime session ID', async () => {
  const h = harness({ activeSessionId: 'runtime-id', focusedStoredSessionId: 'durable-id' });
  h.c.plugin.register(h.ctx);
  h.draw();
  await h.queries[0].queryFn();
  assert.equal(h.calls[0].path, '/status?session_id=durable-id');
  assert.ok(h.queries[0].queryKey.includes('durable-id'));
});

test('current Session identifier is visible, follows durable focus and exposes full ID in tooltip', () => {
  const h = harness({ focusedStoredSessionId: 'durable-session-identifier' }); h.c.plugin.register(h.ctx);
  const session = () => h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Current session identifier');
  assert.match(visibleText(h.draw()), /Session/);
  assert.equal(session()?.props.children, 'durable-sess…');
  assert.equal(session()?.props.title, 'durable-session-identifier');
  h.values.focusedStoredSessionId = null;
  assert.equal(session()?.props.children, 'None selected');
  assert.equal(h.c.currentScope()[2], null, 'do not fall back to runtime ID on clear');
  h.values.focusedStoredSessionId = 'new-session';
  assert.equal(session()?.props.children, 'new-session');
  assert.equal(h.calls.length, 0, 'identifier needs no extra RPC or REST lookup');
});

test('durable session A to clear to A invalidates pending queries and inspection', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const tree = h.draw();
  let resolveQuery, resolveInspect;
  h.ctx.rest = path => new Promise(done => { if (path === '/inspect') resolveInspect = done; else resolveQuery = done; });
  const query = h.queries[0].queryFn();
  const inspect = h.nodes(tree).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  h.values.focusedStoredSessionId = null; h.draw();
  h.values.focusedStoredSessionId = 's'; h.draw();
  resolveQuery(h.data.status); resolveInspect({ content: 'CLEARED SESSION RAW' });
  await assert.rejects(query, /scope/i); await inspect;
  assert.doesNotMatch(JSON.stringify(h.draw()), /CLEARED SESSION RAW/);
});

test('compact shield reveals a native vertically stacked sidebar pane, not a popup', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const pane = h.ctx.entries.find(e => e.area === 'panes');
  assert.ok(pane, 'register a native pane');
  assert.equal(pane.data.placement, 'main');
  assert.equal(pane.data.dock.pane, 'plugin-workspace:llm-usage');
  assert.equal(pane.data.dock.pos, 'bottom');
  h.close();
  const tree = h.draw();
  assert.equal(h.nodes(tree).some(n => n.type === 'Popover'), false);
  const button = h.nodes(tree).find(n => n.type === 'Button');
  assert.equal(button.props['aria-label'], 'Open Security');
  assert.ok(button.props.style.width <= 24);
  assert.equal(h.nodes(tree).some(n => n.type === 'Codicon' && n.props.name === 'shield'), true);
  assert.equal(h.nodes(tree).find(n => n.type === 'Tip').props.label.includes('Not enabled'), true);
  h.open();
  assert.ok(h.nodes(h.draw()).some(n => n.props?.['aria-label'] === 'Security'));
});

test('chip tooltip uses native label and click toggles pane closed and open', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const chip = () => h.nodes(h.draw()).find(n => n.type === 'Button' && /^(Open|Close) Security$/.test(n.props['aria-label'] || ''));
  assert.equal(chip().props['aria-expanded'], true);
  const tooltip = () => h.nodes(h.draw()).find(n => n.type === 'Tip').props.label;
  assert.equal(tooltip(), 'Security · Observing. Enforcement: Not enabled.');
  assert.doesNotMatch(tooltip(), /(?:Open|Close) sidebar panel/);
  chip().props.onClick();
  assert.equal(h.values.paneVisible, false);
  assert.equal(h.ctx.entries.filter(e => e.area === 'panes').length, 0);
  chip().props.onClick();
  assert.equal(h.values.paneVisible, true);
  assert.equal(h.ctx.entries.filter(e => e.area === 'panes').length, 1);
  assert.equal(chip().props['aria-expanded'], true);
});

test('native status chip opens scoped polling panel, raw capture stays local and native calls are not reviewable', async () => {
  const h = harness();
  assert.equal(h.c.plugin?.id, 'local-agent-security');
  h.c.plugin.register(h.ctx);
  assert.equal(h.ctx.entry.area, 'statusBar.right');
  h.close();
  let tree = h.draw();
  assert.equal(h.nodes(tree).some(n => n.props?.['aria-label'] === 'Security'), false);
  h.open();
  tree = h.draw();
  assert.equal(h.nodes(tree).some(n => n.props?.['aria-label'] === 'Security'), true);
  assert.ok(h.queries.every(q => q.refetchInterval >= 3000));
  assert.ok(h.queries.every(q => q.queryKey.includes('default') && q.queryKey.includes('local') && q.queryKey.includes('s')));
  assert.equal(h.calls.length, 0);
  const inspect = h.nodes(tree).find(n => n.type === 'Button' && n.props.children === 'Inspect raw capture');
  await inspect.props.onClick();
  tree = h.draw();
  assert.match(JSON.stringify(tree), /LOCAL RAW SECRET/);
  assert.equal(h.calls[0].path, '/inspect');
  assert.equal(h.calls[0].options.body.id, 'capture');
  assert.equal(h.calls[0].options.body.include_content, true);
  h.values.focusedStoredSessionId = 'other';
  tree = h.draw();
  assert.doesNotMatch(JSON.stringify(tree), /LOCAL RAW SECRET/);
  h.values.focusedStoredSessionId = 's';
  h.data.events.events[0].inspectable = false;
  tree = h.draw();
  assert.equal(h.nodes(tree).some(n => n.type === 'Button' && n.props.children === 'Inspect raw capture'), false);
});

test('review buttons write labels only to reviewable captures and replay requires an explicit model', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.open();
  let tree = h.draw();
  const authorized = h.nodes(tree).find(n => n.type === 'Button' && n.props.children === 'Expected');
  assert.ok(authorized, 'capture review control exists');
  await authorized.props.onClick();
  assert.equal(h.calls[0].path, '/label');
  assert.equal(h.calls[0].options.body.id, 'capture');
  assert.equal(h.calls[0].options.body.value, 'authorized');
  tree = h.draw();
  const selector = replaySelector(h, tree);
  assert.ok(selector, 'user must choose a backend-listed model');
  assert.equal(selector.props.value, '', 'do not select a default');
  assert.equal(selector.props.disabled, false);
  assert.deepEqual(h.nodes(selector).filter(n => n.type === 'option').map(n => n.props.value), ['', 'lite', 'qev']);
  assert.equal(replayButton(h, tree).props.disabled, true);
  const retainedReplay = replayButton(h, tree).props.onClick;
  await retainedReplay();
  assert.equal(h.calls.filter(x => x.path === '/replay').length, 0, 'no choice means no POST');
  for (const model of ['lite', 'qev']) {
    tree = chooseReplay(h, model);
    assert.equal(replayButton(h, tree).props.disabled, false);
    await retainedReplay();
    const call = h.calls.at(-1);
    assert.equal(call.path, '/replay');
    assert.equal(call.options.method, 'POST');
    assert.deepEqual(JSON.parse(JSON.stringify(call.options.body)), { model });
  }
  h.data.replay = { ...h.data.replay, state: 'running', model: 'qev', detail: 'Evaluating pending captures' };
  tree = h.draw();
  assert.equal(replayButton(h, tree).props.disabled, true);
  assert.equal(replaySelector(h, tree).props.disabled, true);
  assert.match(JSON.stringify(tree), /Evaluating pending captures/);
  h.data.replay = { ...h.data.replay, state: 'failed', exit_code: 7, detail: 'Judge unavailable' };
  tree = h.draw();
  assert.match(JSON.stringify(tree), /exit 7/);
  assert.match(JSON.stringify(tree), /Judge unavailable/);
  h.data.events.events[0].reviewable = false;
  tree = h.draw();
  assert.equal(h.nodes(tree).some(n => n.type === 'Button' && n.props.children === 'Expected'), false);
});

test('incompatible replay schemas fail closed with restart guidance even for retained callbacks', async () => {
  const invalid = [
    { state: 'idle' },
    { state: 'idle', available: true, model: null, models: null },
    { state: 'idle', available: true, model: null, models: [] },
    { state: 'idle', available: true, model: null, models: [{ id: 'other', label: 'Untrusted model' }] },
    { state: 'idle', available: true, model: null, models: [{ id: 'lite', label: 'D3 Lite' }, { id: 'other', label: 'Untrusted model' }] },
    { state: 'idle', available: true, model: null, models: [{ id: 'lite' }] },
    { state: 'idle', available: true, model: null, models: [null] },
    { state: 'idle', available: true, model: null, models: [replayModels[0], replayModels[0]] },
    { state: 'idle', available: true, model: 'other', models: replayModels },
  ];
  for (const response of invalid) {
    const h = harness(); h.c.plugin.register(h.ctx);
    chooseReplay(h, 'lite');
    const retained = replayButton(h).props.onClick;
    const retainedSelect = replaySelector(h).props.onChange;
    h.data.replay = response;
    const tree = h.draw();
    assert.equal(replayButton(h, tree).props.disabled, true, JSON.stringify(response));
    assert.equal(replaySelector(h, tree).props.disabled, true);
    assert.match(visibleText(tree), /Replay status failed: Invalid replay response/);
    assert.match(visibleText(tree), /restart.*backend/i);
    await retained(); retainedSelect({ target: { value: 'qev' } }); await retained();
    assert.equal(h.calls.length, 0, 'never POST to an old or malformed backend');
    await assert.rejects(h.queries.filter(q => q.queryKey.at(-1) === 'replay').at(-1).queryFn(), /Invalid replay response/);
  }
});

test('replay availability and current owner/error guards also reject retained model actions', async () => {
  for (const failure of ['unavailable', 'running', 'replay-store', 'replay-error', 'status-error', 'status-stale', 'owner-change']) {
    const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
    const retained = replayButton(h).props.onClick;
    const retainedSelect = replaySelector(h).props.onChange;
    if (failure === 'unavailable') h.data.replay.available = false;
    if (failure === 'running') { h.data.replay.state = 'running'; h.data.replay.model = 'lite'; }
    if (failure === 'replay-store') h.data.status.subsystems = { replay: { healthy: false } };
    if (failure === 'replay-error') h.errors.replay = new Error('Replay offline');
    if (failure === 'status-error') h.errors.status = new Error('Status offline');
    if (failure === 'status-stale') h.data.status.observed_at = 0;
    if (failure === 'owner-change') h.values.focusedSessionOwner = { connectionId: 'other', profile: 'default' };
    const tree = h.draw();
    assert.equal(replayButton(h, tree).props.disabled, true, failure);
    assert.equal(replaySelector(h, tree).props.disabled, true, failure);
    retainedSelect({ target: { value: 'qev' } });
    await retained();
    assert.equal(h.calls.length, 0, failure);
    if (failure !== 'owner-change') assert.equal(replaySelector(h).props.value, 'lite', 'disabled retained selector cannot change the choice');
  }
});

test('only backend-listed models can be selected, and clearing a choice disables replay again', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.replay.models = [replayModels[0]];
  let tree = h.draw();
  assert.deepEqual(h.nodes(replaySelector(h, tree)).filter(n => n.type === 'option').map(n => n.props.value), ['', 'lite']);
  tree = chooseReplay(h, 'qev');
  assert.equal(replayButton(h, tree).props.disabled, true);
  await replayButton(h, tree).props.onClick();
  tree = chooseReplay(h, 'lite');
  assert.equal(replayButton(h, tree).props.disabled, false);
  h.data.replay.models = [replayModels[1]];
  tree = h.draw();
  assert.equal(replayButton(h, tree).props.disabled, true, 'a removed model is no longer dispatchable');
  await replayButton(h, tree).props.onClick();
  tree = chooseReplay(h, 'qev');
  assert.equal(replayButton(h, tree).props.disabled, false);
  tree = chooseReplay(h, '');
  assert.equal(replayButton(h, tree).props.disabled, true);
  await replayButton(h, tree).props.onClick();
  assert.equal(h.calls.length, 0);
});

test('replay progress uses the dispatched/job model rather than the next selector choice', async () => {
  const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
  const retained = replayButton(h).props.onClick;
  const retainedSelect = replaySelector(h).props.onChange;
  let resolve;
  h.ctx.rest = (path, options) => { h.calls.push({ path, options }); return new Promise(done => { resolve = done; }); };
  const request = retained();
  const starting = h.draw();
  assert.match(visibleText(starting), /Replay: starting · D3 Lite/);
  assert.equal(replaySelector(h, starting).props.disabled, true);
  retainedSelect({ target: { value: 'qev' } });
  await retained();
  assert.equal(h.calls.length, 1, 'no duplicate dispatch or retained selector change during startup');
  assert.equal(h.calls[0].options.body.model, 'lite');
  assert.equal(replaySelector(h).props.value, 'lite');
  h.data.replay = { ...h.data.replay, state: 'running', model: 'lite' };
  resolve({ ...h.data.replay }); await request;
  assert.match(visibleText(h.draw()), /Replay: running · D3 Lite/);
  h.data.replay.state = 'succeeded';
  const finished = chooseReplay(h, 'qev');
  assert.equal(replaySelector(h, finished).props.value, 'qev');
  assert.match(visibleText(finished), /Replay: succeeded · D3 Lite/);
  h.data.replay = { ...h.data.replay, state: 'running', model: 'qev' };
  assert.match(visibleText(h.draw()), /Replay: running · QEV/);
  h.data.replay.model = null;
  assert.match(visibleText(h.draw()), /Replay: running · Unknown model/);
});

test('backend Unix timestamps are interpreted as seconds and event times remain readable', () => {
  const c = load();
  const status = { observed_at: now / 1000, observation: { state: 'observing' }, components: [{ healthy: true }] };
  assert.equal(c.coverageView(status, null, now).label, 'Observing');
  assert.equal(c.coverageView(status, null, now).stale, false);
  assert.equal(c.displayTime(now / 1000), new Date(now).toLocaleString());
  assert.equal(c.displayTime('not-a-date'), 'Unknown');
  assert.equal(c.displayTime(0), new Date(0).toLocaleString());
});

test('delayed query responses never populate a cache key for the previous host scope', async () => {
  const h = harness(); h.c.plugin.register(h.ctx); h.draw();
  let resolve;
  h.ctx.rest = () => new Promise(done => { resolve = done; });
  const read = h.queries[0].queryFn();
  h.values.gateway = 'remote';
  resolve({ observed_at: now / 1000 });
  await assert.rejects(read, /scope changed/i);
});

test('raw is cleared on close and late inspect cannot reveal content after host changes', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.open();
  let tree = h.draw();
  await h.nodes(tree).find(n => n.type === 'Button' && n.props.children === 'Inspect raw capture').props.onClick();
  tree = h.draw();
  assert.match(JSON.stringify(tree), /LOCAL RAW SECRET/);
  h.close();
  tree = h.draw();
  h.open();
  tree = h.draw();
  assert.doesNotMatch(JSON.stringify(tree), /LOCAL RAW SECRET/);
  let resolve;
  h.ctx.rest = () => new Promise(done => { resolve = done; });
  const inspect = h.nodes(tree).find(n => n.type === 'Button' && n.props.children === 'Inspect raw capture').props.onClick();
  h.values.profile = 'other'; h.draw();
  resolve({ content: 'LATE PRIVATE CONTENT' }); await inspect;
  assert.doesNotMatch(JSON.stringify(h.draw()), /LATE PRIVATE CONTENT/);
});

test('poll endpoints preserve current scope, all-session and verification defaults', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.open();
  let tree = h.draw();
  for (const resource of ['status', 'events', 'replay']) await h.queries.find(q => q.queryKey.at(-1) === resource).queryFn();
  assert.equal(h.calls[0].path, '/status?session_id=s');
  let params = new URLSearchParams(h.calls[1].path.split('?')[1]);
  assert.equal(params.get('all_sessions'), 'false');
  assert.equal(params.get('include_probes'), 'false');
  assert.equal(params.get('limit'), '50');
  h.nodes(tree).find(n => n.type === 'select').props.onChange({ target: { value: 'all' } });
  tree = h.draw();
  h.nodes(tree).find(n => n.type === 'input').props.onChange({ target: { checked: true } });
  h.draw();
  await h.queries.filter(q => q.queryKey.at(-1) === 'events').at(-1).queryFn();
  params = new URLSearchParams(h.calls.at(-1).path.split('?')[1]);
  assert.equal(params.get('all_sessions'), 'true');
  assert.equal(params.get('include_probes'), 'true');
});

test('empty activity is not confused with backend failure and action errors remain visible', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.open();
  h.data.events.events = [];
  assert.match(JSON.stringify(h.draw()), /No activity in this scope/);
  h.errors.events = new Error('offline');
  const failed = JSON.stringify(h.draw());
  assert.match(failed, /Activity request failed: offline/);
  assert.doesNotMatch(failed, /No activity in this scope/);
  delete h.errors.events;
  h.data.events.events = [{ id: 'capture', session_id: 's', reviewable: true, inspectable: true }];
  h.ctx.rest = async () => { throw new Error('service rejected action'); };
  chooseReplay(h, 'lite');
  for (const [button, message] of [['Expected', 'Review failed'], ['Inspect raw capture', 'Inspect failed'], ['Replay pending captures', 'Replay start failed']]) {
    await h.nodes(h.draw()).find(n => n.type === 'Button' && n.props.children === button).props.onClick();
    assert.match(JSON.stringify(h.draw()), new RegExp(message));
  }
});

// Native <details> retains its children in the DOM but hides them until opened.
function visibleText(tree) {
  if (tree == null) return '';
  if (typeof tree !== 'object') return String(tree);
  if (Array.isArray(tree)) return tree.map(visibleText).join(' ');
  const children = tree.props?.children;
  if (tree.type === 'details' && !tree.props.open) {
    return visibleText([children].flat().find(n => n?.type === 'summary'));
  }
  return visibleText(children);
}

test('status overview uses decorative native icons and state cues without coloring disabled enforcement as active', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.judge = { state: 'available', model: { id: 'lite', label: 'D3 Lite' } };
  const row = label => h.nodes(h.draw()).find(n => n.props['aria-label'] === label);
  const overview = row('Status overview');
  assert.ok(overview, 'a compact visual status group');
  assert.match(overview.props.style.border, /var\(--ui-stroke-secondary\)/);
  for (const [label, icon, value, color] of [
    ['Observation status', 'eye', 'Observing', 'var(--ui-accent)'],
    ['Enforcement status', 'shield', 'Not enabled', 'var(--ui-text-secondary)'],
    ['Live judge', 'pulse', 'D3 Lite · Available', 'var(--ui-green)'],
  ]) {
    const current = row(label);
    assert.ok(current, label);
    assert.ok(h.nodes(current).some(n => n.type === 'Codicon' && n.props.name === icon && n.props['aria-hidden'] === true));
    const cue = h.nodes(current).find(n => n.props['data-state-cue'] === value);
    assert.ok(cue, value);
    assert.equal(cue.props.style.background, color);
    assert.equal(cue.props['aria-hidden'], true, 'text still carries status meaning');
    assert.ok(visibleText(current).includes(value));
  }
  assert.match(visibleText(h.draw()), /Observation only · Hermes handles approvals/);
  assert.doesNotMatch(visibleText(h.draw()), /Observation is not blocking\. Permissions stay/);
});

test('status summary uses quiet unboxed values with explicit observation and enforcement meanings', () => {
  const h = harness(); h.c.plugin.register(h.ctx); h.open();
  h.data.status.observation = { state: 'not_covered', provider: 'last-provider', detail: 'Provider routing diagnostic' };
  h.data.status.components = [{ id: 'proxy', label: 'Proxy', healthy: false, detail: 'Connection refused diagnostic' }];
  h.data.status.counts = { traffic_captures: 12, verification_captures: 3, replay_attempts: 4, label_events: 5 };
  const tree = h.draw(), nodes = h.nodes(tree), text = visibleText(tree);
  assert.ok(nodes.some(n => n.type === 'h3' && n.props.children === 'Status'));
  assert.equal(nodes.some(n => n.type === 'h3' && n.props.children === 'Coverage'), false);
  for (const value of ['Not covered', 'Not enabled', 'Unknown model']) {
    const state = nodes.find(n => n.type === 'span' && n.props.children === value);
    assert.ok(state, value);
    assert.equal(state.props.style.border, undefined);
    assert.equal(state.props.style.background, undefined);
  }
  assert.equal(nodes.some(n => n.type === 'h2' && n.props.children === 'Security'), false, 'native pane already supplies the title');
  for (const label of ['Observation', 'Not covered', 'Enforcement', 'Not enabled', 'Traffic', '12', 'Verification', '3', 'Replay', '4', 'Reviews', '5', 'Proxy', 'Unavailable']) assert.ok(text.includes(label), label);
  const diagnostics = nodes.find(n => n.type === 'details' && visibleText(n).includes('Diagnostics'));
  assert.ok(diagnostics, 'diagnostics are expandable');
  assert.doesNotMatch(text, /Connection refused diagnostic|Provider routing diagnostic|last-provider/);
  diagnostics.props.open = true;
  assert.match(visibleText(diagnostics), /Connection refused diagnostic/);
  assert.match(visibleText(diagnostics), /Provider routing diagnostic/);
  assert.match(visibleText(diagnostics), /last-provider/);
  assert.match(text, /Observation only · Hermes handles approvals/);
  const grid = nodes.find(n => n.props?.['aria-label'] === 'Capture counts');
  assert.equal(grid.props.style.gridTemplateColumns, 'repeat(4, minmax(0, 1fr))');
});

test('activity scores retain actual QEV-live/Lite-replay identities while outcomes and review stay separate', () => {
  const h = harness(); h.c.plugin.register(h.ctx); h.open();
  h.data.events.events = [
    { id: 'capture', session_id: 's', tool: 'read', tool_call_id: 'private-call-id', source: 'proxy', execution: 'not_intercepted', scores: [scoreFixture('live', 'qev', 0, { would_flag: false }), scoreFixture('replay', 'lite', 0.9, { would_flag: true })], detail: 'Verbose provider capture caveat', reviewable: true, inspectable: true },
    { id: 'native-id', session_id: 's', tool: 'write', source: 'hermes', execution: 'tool_returned', reviewable: false, detail: 'Native lifecycle caveat' },
  ];
  const tree = h.draw(), text = visibleText(tree);
  assert.match(text, /Live · QEV 0 · Would not flag · provisional/);
  assert.match(text, /Replay · D3 Lite 0.9 · Would flag · provisional/);
  assert.doesNotMatch(text, /Live · D3 Lite|Replay · QEV|Lite 0 · QEV 0.9/);
  assert.match(text, /Observed only — not stopped/);
  assert.match(text, /Tool returned · effects unverified/);
  assert.doesNotMatch(text, /Verbose provider capture caveat|Native lifecycle caveat|private-call-id|native-id/);
  const rows = h.nodes(tree).filter(n => n.type === 'article');
  assert.equal(rows.length, 2);
  const details = h.nodes(rows[0]).find(n => n.type === 'details');
  assert.ok(details);
  details.props.open = true;
  assert.match(visibleText(details), /Verbose provider capture caveat/);
  assert.match(visibleText(details), /private-call-id/);
  assert.match(visibleText(details), /Inspect raw capture/);
  assert.match(visibleText(details), /Post-hoc review: Not reviewed/);
  assert.match(visibleText(details), /Annotation only · no effect on execution/);
  assert.match(visibleText(details), /Threshold: 0.88 · provisional/);
  assert.match(visibleText(details), /Scored: /);
  assert.equal(h.nodes(rows[1]).some(n => n.type === 'Button'), false);
  assert.doesNotMatch(text, /safe|%/i);
});

test('missing score provenance is unknown, failures are not verdicts, and replay attempts are not collapsed', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.events.events = [
    { id: 'legacy', session_id: 's', source: 'proxy', lite_score: 0.12, qev_score: 0.95, reviewable: false },
    { id: 'unknown', session_id: 's', source: 'proxy', reviewable: false, scores: [scoreFixture('live', null, 0.3, { model: null }), scoreFixture('replay', null, null, { model: { label: 'QEV' }, error: true, would_flag: false, threshold: null, time: null })] },
    { id: 'attempts', session_id: 's', source: 'replay', reviewable: false, scores: [scoreFixture('replay', 'qev', 0.2, { would_flag: false }), scoreFixture('replay', 'qev', 0.9, { would_flag: true }), scoreFixture('replay', 'lite', 0.1, { error: true, would_flag: false })] },
  ];
  const rows = h.nodes(h.draw()).filter(n => n.type === 'article');
  assert.match(visibleText(rows[0]), /Unknown model · score provenance unavailable/);
  assert.doesNotMatch(visibleText(rows[0]), /Lite|QEV|0.12|0.95/);
  assert.match(visibleText(rows[1]), /Live · Unknown model 0.3 · Flag unknown · provisional/);
  assert.match(visibleText(rows[1]), /Replay · Unknown model — · Score failed · provisional/);
  assert.doesNotMatch(visibleText(rows[1]), /Live · D3 Lite|Replay · QEV|Would not flag/);
  assert.match(visibleText(rows[2]), /Replay · QEV 0.2 · Would not flag/);
  assert.match(visibleText(rows[2]), /Replay · QEV 0.9 · Would flag/);
  assert.match(visibleText(rows[2]), /Replay · D3 Lite — · Score failed/);
  const details = h.nodes(rows[1]).find(n => n.type === 'details'); details.props.open = true;
  assert.match(visibleText(details), /Threshold: Unknown · provisional · Scored: Unknown/);
});

test('scope controls and replay evaluator fit a narrow rail without hiding global replay scope', () => {
  const h = harness(); h.c.plugin.register(h.ctx); h.open();
  h.data.replay = { ...h.data.replay, state: 'failed', exit_code: 7, detail: 'long-unbroken-diagnostic'.repeat(12) };
  const tree = h.draw(), nodes = h.nodes(tree);
  assert.ok(nodes.some(n => n.type === 'h3' && n.props.children === 'Replay'));
  assert.ok(nodes.some(n => n.type === 'h3' && n.props.children === 'Activity'));
  const panel = nodes.find(n => n.props?.['aria-label'] === 'Security');
  assert.equal(panel.props.style.overflowWrap, 'anywhere');
  const select = nodes.find(n => n.type === 'select');
  assert.equal(select.props.style.maxWidth, '100%');
  const button = replayButton(h, tree);
  assert.equal(button.props.style.whiteSpace, 'normal');
  assert.match(visibleText(tree), /Manual · all sessions/);
  assert.match(visibleText(tree), /does not change the live judge/);
  assert.match(visibleText(tree), /does not execute captured tools/);
  assert.match(visibleText(tree), /exit 7/);
  assert.match(visibleText(tree), /long-unbroken-diagnostic/);
});

test('execution states and request-qualified groups preserve distinct IDs without detector correlation claims', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const base = { session_id: 's', turn_id: 'turn', tool_call_id: 'call', source: 'hermes', reviewable: false };
  h.data.events.events = ['blocked', 'cancelled', 'error', 'tool_returned', 'proposed', 'unknown'].map((execution, i) => ({ ...base, id: String(i), api_request_id: `request-${i}`, execution }));
  h.data.events.events.push({ ...base, id: 'legacy', api_request_id: 'request-legacy', execution: 'tool_returned', status: 'error' });
  const tree = h.draw(), rows = h.nodes(tree).filter(n => n.type === 'article');
  assert.equal(rows.length, 7);
  const text = visibleText(tree);
  for (const label of ['Blocked · not executed', 'Cancelled · effects unknown', 'Error · effects unknown', 'Tool returned · effects unverified', 'Proposed', 'Execution unknown']) assert.ok(text.includes(label), label);
  assert.match(visibleText(rows.at(-1)), /Error · effects unknown/);
  const details = h.nodes(rows[0]).find(n => n.type === 'details'); details.props.open = true;
  for (const label of ['Session: s', 'Turn: turn', 'Tool call: call', 'API request: request-0']) assert.ok(visibleText(details).includes(label));
  assert.equal(h.c.activityRows([{ ...base, id: 'legacy', execution: 'not_confirmed' }], 's', false, false)[0].execution, 'Proposed');
});

test('returning to owner A after B cannot revive an in-flight A query or inspect', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const tree = h.draw();
  let resolveQuery, resolveInspect;
  h.ctx.rest = path => new Promise(done => { if (path === '/inspect') resolveInspect = done; else resolveQuery = done; });
  const query = h.queries[0].queryFn();
  const inspect = h.nodes(tree).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  h.values.focusedSessionOwner = { connectionId: 'B', profile: 'default' };
  h.draw();
  h.values.focusedSessionOwner = { connectionId: 'local', profile: 'default' };
  h.draw();
  resolveQuery(h.data.status); resolveInspect({ content: 'ABA RAW' });
  await assert.rejects(query, /scope/i); await inspect;
  assert.doesNotMatch(JSON.stringify(h.draw()), /ABA RAW/);
});

test('review counts prefer unique events and preserve explicit unknown instead of label history', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.counts = { reviewed_events: 2, label_events: 9 };
  const counts = () => h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Capture counts');
  assert.match(visibleText(counts()), /2 Reviews/);
  assert.doesNotMatch(visibleText(counts()), /9/);
  h.data.status.counts.reviewed_events = null;
  assert.match(visibleText(counts()), /Unknown Reviews/);
  assert.doesNotMatch(visibleText(counts()), /—|0/);
  h.data.events.unassociated_count = null;
  assert.match(visibleText(h.draw()), /Unknown.*scope notes/);
  h.data.status.counts.traffic_captures = 0;
  assert.match(visibleText(counts()), /0 Traffic/);
});

test('store health stays distinct from actual live judge identity and replay choice', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.subsystems = { activity: { healthy: true, detail: 'Activity ready' }, review: { healthy: false, detail: 'Review damaged' }, replay: { healthy: true, detail: 'Replay records ready' } };
  h.data.status.judge = { state: 'available', model: { id: 'qev', label: 'QEV', model_file_sha256: 'a'.repeat(64), native_template_sha256: 'b'.repeat(64) }, threshold: 0.88, detail: 'Verified backend identity' };
  const tree = chooseReplay(h, 'lite'), text = visibleText(tree);
  for (const label of ['Storage', '2 healthy', 'Live judge', 'QEV · Available']) assert.ok(text.includes(label), label);
  assert.match(text, /Review records\s+Unavailable/);
  assert.ok(h.nodes(tree).some(n => n.props['aria-label'] === 'Review records: Unavailable'));
  assert.doesNotMatch(text, /Activity records: Healthy|Replay records: Healthy/);
  assert.doesNotMatch(text, /QEV: Healthy|QEV model healthy|D3 Lite · Available/);
  const live = h.nodes(tree).find(n => n.props['aria-label'] === 'Live judge');
  assert.ok(live, 'live identity is independently labeled');
  assert.match(visibleText(live), /QEV · Available/);
  assert.equal(replaySelector(h, tree).props.value, 'lite');
  const diagnostics = h.nodes(tree).find(n => n.type === 'details' && visibleText(n).includes('Diagnostics'));
  diagnostics.props.open = true;
  assert.match(visibleText(diagnostics), /Verified backend identity/);
  for (const label of ['Activity records: Healthy', 'Review records: Unavailable', 'Replay records: Healthy', 'Review damaged']) assert.ok(visibleText(diagnostics).includes(label), label);
  assert.match(visibleText(diagnostics), /0.88.*provisional/i);
  assert.ok(text.indexOf('Activity') < text.indexOf('Replay model'));
  assert.ok(h.nodes(tree).findIndex(n => n.type === 'article') < h.nodes(tree).findIndex(n => n.type === 'h3' && n.props.children === 'Replay'));
});

test('live judge unavailable, mismatch and stale identities never inherit healthy record-store claims', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.subsystems = { replay: { healthy: true }, activity: { healthy: true }, review: { healthy: true } };
  const liveText = () => visibleText(h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Live judge'));
  const model = { id: 'lite', label: 'D3 Lite', model_file_sha256: null, native_template_sha256: null };
  for (const judge of [undefined, { state: 'unknown', model }, { state: 'available', model: null }, { state: 'available', model: { label: 'QEV' } }, { state: 'available', model: { id: 'qev', label: 'D3 Lite' } }]) {
    h.data.status.judge = judge;
    assert.match(liveText(), /Unknown model/);
    assert.doesNotMatch(liveText(), /Available|Healthy/);
  }
  h.data.status.judge = { state: 'unavailable', model };
  assert.match(liveText(), /Unavailable/);
  h.data.status.judge = { state: 'mismatch', model };
  assert.match(liveText(), /Unknown model.*identity mismatch/);
  h.data.status.judge = { state: 'available', model };
  assert.match(liveText(), /D3 Lite · Available/);
  h.data.status.observed_at = 0;
  assert.match(liveText(), /Unknown model.*stale/);
  h.data.status.observed_at = new Date().toISOString();
  h.errors.status = new Error('offline');
  assert.match(liveText(), /Unavailable/);
  assert.doesNotMatch(liveText(), /Available|D3 Lite/);
});

test('raw response cap and truncation are explicit, malformed resource schemas are contained', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.ctx.rest = async () => ({ truncated: true, content: 'x'.repeat(300000) });
  await h.nodes(h.draw()).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  const tree = h.draw();
  assert.match(JSON.stringify(tree), /truncated/i);
  const raw = h.nodes(tree).find(n => n.type === 'pre').props.children;
  assert.ok(Buffer.byteLength(raw) <= 256 * 1024);
  h.data.events = { events: 'invalid' };
  assert.doesNotThrow(() => h.draw());
  h.ctx.rest = async () => ({ events: 'invalid', private: 'DO NOT ECHO' });
  await assert.rejects(h.queries.find(q => q.queryKey.at(-1) === 'events').queryFn(), /Invalid events response/);
});

test('raw display retains full below-limit serialized response and bounds multibyte truncation', () => {
  const c = load();
  const capture = { content: '' };
  capture.content = 'x'.repeat(256 * 1024 - 1 - Buffer.byteLength(JSON.stringify(capture, null, 2)));
  assert.equal(c.rawView(capture).content, JSON.stringify(capture, null, 2));
  assert.equal(c.rawView(capture).truncated, false);
  const large = c.rawView({ content: '😀'.repeat(100000) });
  assert.ok(Buffer.byteLength(large.content) <= 256 * 1024);
  assert.equal(large.truncated, true);
});

test('cached activity error disables buttons and stale callbacks, clears raw and discards late inspection', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const button = (tree, label) => h.nodes(tree).find(n => n.type === 'Button' && n.props.children === label);
  let tree = h.draw();
  const oldReview = button(tree, 'Expected')?.props.onClick;
  const oldInspect = button(tree, 'Inspect raw capture').props.onClick;
  await oldInspect(); assert.match(JSON.stringify(h.draw()), /LOCAL RAW SECRET/);
  let resolve;
  h.ctx.rest = () => new Promise(done => { resolve = done; });
  const pending = oldInspect();
  h.errors.events = new Error('offline'); tree = h.draw();
  assert.equal(button(tree, 'Expected').props.disabled, true);
  assert.equal(button(tree, 'Inspect raw capture').props.disabled, true);
  await oldReview(); await oldInspect();
  resolve({ content: 'LATE SECRET' }); await pending;
  assert.doesNotMatch(JSON.stringify(h.draw()), /LOCAL RAW SECRET|LATE SECRET/);
  delete h.errors.events;
  assert.doesNotMatch(JSON.stringify(h.draw()), /LOCAL RAW SECRET|LATE SECRET/);
});

test('subsystem failures independently gate review and replay, not observation or raw inspection', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.subsystems = { review: { healthy: false, detail: 'Review unavailable' }, replay: { healthy: false, detail: 'Replay records unavailable' } };
  h.data.status.counts = { reviewed_events: null, label_events: 9 };
  const tree = h.draw(), buttons = h.nodes(tree).filter(n => n.type === 'Button');
  assert.match(visibleText(tree), /Observing/);
  assert.equal(buttons.find(n => n.props.children === 'Expected').props.disabled, true);
  assert.equal(buttons.find(n => n.props.children === 'Replay pending captures').props.disabled, true);
  assert.equal(buttons.find(n => n.props.children === 'Inspect raw capture').props.disabled, false);
  for (const b of buttons.filter(n => ['Expected', 'Replay pending captures'].includes(n.props.children))) await b.props.onClick();
  assert.equal(h.calls.length, 0);
  assert.equal(h.c.coverageView(h.data.status, null).healthy, true);
});

test('stale status, resource failures and current capabilities reject retained callbacks', async () => {
  for (const failure of ['status-stale', 'status-error', 'events-unhealthy', 'activity-unhealthy', 'replay-error', 'review-unhealthy', 'inspect-disabled']) {
    const h = harness(); h.c.plugin.register(h.ctx);
    chooseReplay(h, 'qev');
    const retained = h.nodes(h.draw()).filter(n => n.type === 'Button' && ['Expected', 'Inspect raw capture', 'Replay pending captures'].includes(n.props.children));
    if (failure === 'status-stale') h.data.status.observed_at = 0;
    if (failure === 'status-error') h.errors.status = new Error('offline');
    if (failure === 'events-unhealthy') h.data.events.subsystems = { activity: { healthy: false } };
    if (failure === 'activity-unhealthy') h.data.status.subsystems = { activity: { healthy: false } };
    if (failure === 'review-unhealthy') h.data.status.subsystems = { review: { healthy: false } };
    if (failure === 'replay-error') h.errors.replay = new Error('offline');
    if (failure === 'inspect-disabled') h.data.events.events[0].inspectable = false;
    h.draw();
    const blocked = failure.startsWith('status') ? retained : retained.filter(n => failure === 'replay-error' ? n.props.children === 'Replay pending captures' : failure === 'review-unhealthy' ? n.props.children === 'Expected' : failure === 'inspect-disabled' ? n.props.children === 'Inspect raw capture' : n.props.children !== 'Replay pending captures');
    for (const b of blocked) await b.props.onClick();
    assert.equal(h.calls.length, 0, failure);
  }
});

test('inspection is an explicit capability independent of reviewability', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.events.events = [{ id: 'native', session_id: 's', reviewable: false, inspectable: true }, { id: 'review', session_id: 's', reviewable: true }];
  const articles = h.nodes(h.draw()).filter(n => n.type === 'article');
  assert.ok(h.nodes(articles[0]).some(n => n.props.children === 'Inspect raw capture'));
  assert.equal(h.nodes(articles[1]).some(n => n.props.children === 'Inspect raw capture'), false);
});

test('health groups use icon headings and compact aligned component states while full names remain in diagnostics', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.observation = { state: 'not_covered' };
  h.data.status.components = [
    { id: 'proxy_glm', label: 'GLM observation proxy', healthy: false },
    { id: 'decision', label: 'Local decision model', healthy: false },
    { id: 'store', label: 'Private capture store', healthy: true },
  ];
  const tree = h.draw();
  for (const [group, icon] of [['Route unconfirmed', 'git-branch'], ['Storage', 'database']]) {
    const element = h.nodes(tree).find(n => n.props['aria-label'] === group);
    assert.ok(h.nodes(element).some(n => n.type === 'Codicon' && n.props.name === icon && n.props['aria-hidden'] === true), group);
  }
  for (const [name, shortName] of [['GLM observation proxy', 'GLM proxy'], ['Local decision model', 'Decision model']]) {
    const row = h.nodes(tree).find(n => n.props['aria-label'] === `${name}: Unavailable`);
    assert.ok(row, name);
    assert.equal(row.props.style.display, 'grid');
    assert.equal(row.props.style.gridTemplateColumns, 'minmax(0, 1fr) auto');
    assert.match(visibleText(row), new RegExp(shortName));
    const cue = h.nodes(row).find(n => n.props['data-state-cue'] === 'Unavailable');
    assert.equal(cue.props.style.background, 'var(--ui-yellow)');
  }
  const storage = h.nodes(tree).find(n => n.props['aria-label'] === 'Storage');
  assert.match(visibleText(storage), /1 healthy/);
  const diagnostics = h.nodes(tree).find(n => n.type === 'details' && visibleText(n) === 'Diagnostics');
  diagnostics.props.open = true;
  assert.match(visibleText(diagnostics), /GLM observation proxy: Unavailable/);
  assert.match(visibleText(diagnostics), /Local decision model: Unavailable/);
});

test('health summary counts healthy components and keeps every component state and detail in diagnostics', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.components = [
    { id: 'decision', label: 'Local decision model', healthy: true, detail: 'Decision diagnostic' },
    { id: 'capture', label: 'Observation adapter', healthy: false, detail: 'Adapter diagnostic' },
    { id: 'store', label: 'Private capture store', healthy: true, detail: 'Store diagnostic' },
  ];
  const tree = h.draw(), text = visibleText(tree);
  const health = h.nodes(tree).find(n => n.props['aria-label'] === 'Health summary');
  assert.ok(health);
  assert.match(visibleText(health), /1 healthy/);
  assert.match(text, /Capture adapter\s+Unavailable/);
  assert.doesNotMatch(text, /Local decision model: Healthy|Private capture store: Healthy|diagnostic/);
  const diagnostics = h.nodes(tree).find(n => n.type === 'details' && visibleText(n) === 'Diagnostics');
  diagnostics.props.open = true;
  for (const label of ['Local decision model: Healthy', 'Observation adapter: Unavailable', 'Private capture store: Healthy', 'Decision diagnostic', 'Adapter diagnostic', 'Store diagnostic']) assert.ok(visibleText(diagnostics).includes(label), label);
});

test('only fresh observing or degraded route evidence separates the other proxy without changing coverage', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.components = [
    { id: 'proxy_glm', label: 'GLM observation proxy', healthy: true },
    { id: 'proxy_local', label: 'Local observation proxy', healthy: false },
    { id: 'decision', label: 'Local decision model', healthy: true },
    { id: 'capture', label: 'Observation adapter', healthy: true },
  ];
  const group = (tree, label) => h.nodes(tree).find(n => n.props['aria-label'] === label);
  h.data.status.observation = { state: 'observing', provider: 'safeagents-glm' };
  let tree = h.draw();
  assert.match(visibleText(group(tree, 'Observed route')), /3 healthy/);
  assert.doesNotMatch(visibleText(group(tree, 'Observed route')), /Unavailable/);
  assert.match(visibleText(group(tree, 'Other route')), /Local proxy\s+Unavailable/);
  assert.equal(h.c.coverageView(h.data.status, null).label, 'Observing');
  h.data.status.observation = { state: 'degraded', provider: 'safeagents-local' };
  tree = h.draw();
  assert.match(visibleText(group(tree, 'Observed route')), /Local proxy\s+Unavailable/);
  assert.match(visibleText(group(tree, 'Other route')), /1 healthy/);
  assert.equal(h.c.coverageView(h.data.status, null).label, 'Degraded');
  for (const state of ['unknown', 'not_covered']) {
    h.data.status.observation = { state, provider: 'safeagents-glm', detail: 'Last-recorded only' };
    tree = h.draw();
    assert.equal(group(tree, 'Observed route'), undefined);
    assert.equal(group(tree, 'Other route'), undefined);
    assert.match(visibleText(group(tree, 'Route unconfirmed')), /Local proxy\s+Unavailable/);
  }
  for (const provider of [null, 'unrecognized-provider', 'constructor', '__proto__']) {
    h.data.status.observation = { state: 'degraded', provider };
    assert.ok(group(h.draw(), 'Route unconfirmed'), String(provider));
  }
  h.data.status.observation = { state: 'observing', provider: 'safeagents-glm' };
  h.data.status.observed_at = 0;
  tree = h.draw();
  assert.equal(group(tree, 'Observed route'), undefined);
  assert.ok(group(tree, 'Route unconfirmed'));
});

test('missing, stale and failed health reads remain distinct without healthy counts or invented routes', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.status.observation = { state: 'observing', provider: 'safeagents-glm' };
  h.data.status.components = [{ id: 'proxy_glm', label: 'GLM observation proxy', healthy: true, detail: 'TCP only' }];
  const healthText = tree => visibleText(h.nodes(tree).find(n => n.props['aria-label'] === 'Health summary'));
  let tree = h.draw();
  assert.match(healthText(tree), /Capture adapter\s+Unknown/);
  assert.match(healthText(tree), /Private captures\s+Unknown/);
  assert.match(healthText(tree), /Review records\s+Unknown/);
  assert.match(healthText(tree), /Other route.*Local proxy\s+Unknown/);
  h.data.status.observed_at = 0;
  tree = h.draw();
  assert.match(healthText(tree), /GLM proxy\s+Unknown \(stale\)/);
  assert.doesNotMatch(healthText(tree), /healthy|Observed route|Other route/);
  const diagnostics = h.nodes(tree).find(n => n.type === 'details' && visibleText(n) === 'Diagnostics');
  diagnostics.props.open = true;
  assert.match(visibleText(diagnostics), /GLM observation proxy: Unknown \(stale\).*Last recorded: Healthy.*TCP only/);
  h.errors.status = new Error('offline');
  tree = h.draw();
  assert.match(healthText(tree), /GLM proxy\s+Unknown \(request failed\)/);
  assert.doesNotMatch(healthText(tree), /healthy|Observed route|Other route/);
});

test('only native-only lifecycle rows omit score absence while capture provenance and real failures stay visible', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.events.events = [
    { id: 'native', source: 'hermes', session_id: 's', reviewable: false, scores: [], execution: 'tool_returned' },
    { id: 'capture', source: 'safeagents', session_id: 's', reviewable: false, scores: [] },
    { id: 'replay', source: 'replay', session_id: 's', reviewable: false },
    { id: 'legacy', source: 'hermes', session_id: 's', reviewable: false, lite_score: null },
    { id: 'failure', source: 'hermes', session_id: 's', reviewable: false, scores: [scoreFixture('live', 'qev', null, { error: true })] },
    { id: 'mixed-native', source: 'hermes', session_id: 's', turn_id: 't', tool_call_id: 'c', reviewable: false },
    { id: 'mixed-capture', source: 'safeagents', session_id: 's', turn_id: 't', tool_call_id: 'c', reviewable: false },
  ];
  const rows = h.nodes(h.draw()).filter(n => n.type === 'article');
  assert.doesNotMatch(visibleText(rows[0]), /No model scores|score provenance/);
  assert.match(visibleText(rows[1]), /No model scores recorded/);
  assert.match(visibleText(rows[2]), /No model scores recorded/);
  assert.match(visibleText(rows[3]), /Unknown model · score provenance unavailable/);
  assert.match(visibleText(rows[4]), /Score failed/);
  assert.match(visibleText(rows[5]), /No model scores recorded/);
  assert.equal(rows.length, 6, 'only explicit session/turn/tool/request keys group events');
});

test('source labels describe native, capture and replay evidence without asserting correlation', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.events.events = ['hermes', 'safeagents', 'proxy', 'replay', 'future-source', null, 'constructor', '__proto__'].map((source, i) => ({ id: String(i), session_id: 's', source, reviewable: false }));
  const rows = h.nodes(h.draw()).filter(n => n.type === 'article');
  for (const [index, label] of ['Hermes lifecycle', 'SafeAgents', 'Proxy capture', 'Replay', 'future-source', 'Source unknown', 'constructor', '__proto__'].entries()) assert.ok(visibleText(rows[index]).includes(label), label);
  assert.equal(h.c.sourceLabel('safeagents'), 'SafeAgents', 'Journal detections and private captures share this source; do not imply a capture exists.');
  assert.equal(rows.length, 8);
  assert.doesNotMatch(visibleText(rows), /correlated|matched|protected|approved/i);
});

test('Activity owns its accessible scope controls and truthfully names events without a session', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  let tree = h.draw(), nodes = h.nodes(tree);
  const activityHeadings = nodes.filter(n => n.type === 'h3' && /Activity/.test(n.props.children));
  assert.deepEqual(activityHeadings.map(n => n.props.children), ['Activity']);
  const selector = nodes.find(n => n.type === 'select' && n.props['aria-label'] === 'Activity scope');
  assert.ok(selector);
  assert.ok(nodes.indexOf(activityHeadings[0]) < nodes.indexOf(selector));
  assert.ok(nodes.indexOf(selector) < nodes.findIndex(n => n.type === 'article'));
  assert.match(visibleText(tree), /2 events without a session · scope notes/);
  assert.doesNotMatch(visibleText(tree), /unassociated/i);
  selector.props.onChange({ target: { value: 'all' } });
  tree = h.draw();
  assert.equal(h.nodes(tree).find(n => n.props['aria-label'] === 'Activity scope').props.value, 'all');
  const notes = h.nodes(tree).find(n => n.type === 'details' && visibleText(n).includes('scope notes')); notes.props.open = true;
  assert.match(visibleText(notes), /Events without a session appear in All sessions only/);
});

test('expanded event identifiers and recorded status use wrapping definition values without truncation', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  const long = 'unbroken-identifier-'.repeat(18);
  h.data.events.events = [{ id: `event-${long}`, session_id: 's', turn_id: `turn-${long}`, tool_call_id: `call-${long}`, api_request_id: `request-${long}`, status: 'completed', source: 'hermes', reviewable: false }];
  const row = h.nodes(h.draw()).find(n => n.type === 'article');
  const details = h.nodes(row).find(n => n.type === 'details'); details.props.open = true;
  const lists = h.nodes(details).filter(n => n.type === 'dl');
  assert.ok(lists.length);
  const values = lists.flatMap(list => h.nodes(list).filter(n => n.type === 'dd'));
  for (const value of ['s', `event-${long}`, `turn-${long}`, `call-${long}`, `request-${long}`, 'completed']) {
    const node = values.find(n => n.props.children === value);
    assert.ok(node, value);
    assert.equal(node.props.style.overflowWrap, 'anywhere');
    assert.equal(node.props.style.userSelect, 'text');
    assert.equal(node.props.style.textOverflow, undefined);
    assert.equal(node.props.style.overflow, undefined);
  }
  for (const label of ['Session:', 'Turn:', 'Tool call:', 'API request:', 'Recorded status:', 'Event ID:']) assert.ok(h.nodes(details).some(n => n.type === 'dt' && n.props.children === label), label);
});

test('selected post-hoc labels have a persistent visual distinction without claiming approval', () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  for (const selected of ['authorized', 'unwanted', 'insufficient-context']) {
    h.data.events.events[0].human_label = selected;
    const row = h.nodes(h.draw()).find(n => n.type === 'article');
    const buttons = h.nodes(row).filter(n => n.type === 'Button' && typeof n.props['aria-pressed'] === 'boolean');
    const active = buttons.find(n => n.props['aria-pressed']);
    assert.ok(active);
    assert.match(active.props.style.border || '', /var\(--ui-accent\)/);
    assert.match(active.props.style.background || '', /var\(--ui-accent\)/);
    assert.equal(active.props.style.fontWeight, 600);
    assert.ok(buttons.filter(n => !n.props['aria-pressed']).every(n => n.props.style.background !== active.props.style.background));
    assert.match(JSON.stringify(row), /Annotation only · no effect on execution/);
    assert.doesNotMatch(JSON.stringify(row), /Approved|Allow tool|Block tool/);
  }
});

test('raw capture is an opt-in local-only padded monospace surface that hides and clears on filters', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  assert.equal(h.nodes(h.draw()).some(n => n.type === 'pre'), false);
  await h.nodes(h.draw()).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  let tree = h.draw();
  const surface = h.nodes(tree).find(n => n.props['aria-label'] === 'Local-only raw capture');
  assert.ok(surface);
  assert.match(JSON.stringify(surface), /Raw capture · local-only display/);
  const raw = h.nodes(surface).find(n => n.type === 'pre');
  assert.match(raw.props.style.border || '', /var\(--ui-stroke-secondary\)/);
  assert.ok(raw.props.style.padding);
  assert.match(raw.props.style.fontFamily || '', /monospace/);
  assert.equal(raw.props.style.whiteSpace, 'pre-wrap');
  assert.equal(raw.props.style.overflowWrap, 'anywhere');
  h.nodes(surface).find(n => n.props.children === 'Hide raw capture').props.onClick();
  assert.doesNotMatch(JSON.stringify(h.draw()), /LOCAL RAW SECRET/);
  await h.nodes(h.draw()).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Activity scope').props.onChange({ target: { value: 'all' } });
  assert.doesNotMatch(JSON.stringify(h.draw()), /LOCAL RAW SECRET/);
  await h.nodes(h.draw()).find(n => n.props.children === 'Inspect raw capture').props.onClick();
  h.nodes(h.draw()).find(n => n.type === 'input').props.onChange({ target: { checked: true } });
  assert.doesNotMatch(JSON.stringify(h.draw()), /LOCAL RAW SECRET/);
});

test('review and inspection failures stay with the exact event while Replay errors stay beside Replay', async () => {
  const h = harness(); h.c.plugin.register(h.ctx);
  h.data.events.events = ['one', 'two'].map(id => ({ id, session_id: 's', turn_id: 't', tool_call_id: 'c', source: 'safeagents', reviewable: true, inspectable: true }));
  chooseReplay(h, 'lite');
  const event = id => h.nodes(h.draw()).find(n => n.props['data-event-id'] === id);
  const action = (id, text) => h.nodes(event(id)).find(n => n.type === 'Button' && n.props.children === text);
  assert.ok(event('one'), 'exact event owns its details and actions, even in a grouped call');
  h.ctx.rest = async (path, options) => {
    h.calls.push({ path, options });
    if (options?.body?.id === 'one' || path === '/replay') throw new Error('rejected fixture');
    return { success: true };
  };
  await action('one', 'Expected').props.onClick();
  assert.match(JSON.stringify(event('one')), /Review failed: rejected fixture/);
  assert.doesNotMatch(JSON.stringify(event('two')), /Review failed/);
  await action('one', 'Inspect raw capture').props.onClick();
  assert.match(JSON.stringify(event('one')), /Review failed: rejected fixture/);
  assert.match(JSON.stringify(event('one')), /Inspect failed: rejected fixture/);
  assert.equal(action('two', 'Expected').props.disabled, false);
  await action('two', 'Expected').props.onClick();
  assert.doesNotMatch(JSON.stringify(event('two')), /failed/);
  await replayButton(h).props.onClick();
  const tree = h.draw();
  const replay = h.nodes(tree).find(n => n.props['aria-label'] === 'Replay controls');
  assert.ok(replay);
  assert.match(visibleText(replay), /Replay start failed: rejected fixture/);
  assert.doesNotMatch(visibleText(replay), /Review failed|Inspect failed/);
  assert.doesNotMatch(JSON.stringify(event('one')), /Replay start failed/);
  assert.match(JSON.stringify(event('one')), /Review failed.*Inspect failed/);
  assert.equal(replayButton(h).props.disabled, false, 'failure does not disable retry');
});

test('completed action errors clear on Activity filters, owner changes and pane close without reviving', async () => {
  for (const change of ['sessions', 'verification', 'owner', 'close']) {
    const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
    h.ctx.rest = async () => { throw new Error('PRIVATE ACTION FAILURE'); };
    for (const name of ['Expected', 'Inspect raw capture', 'Replay pending captures']) await h.nodes(h.draw()).find(n => n.type === 'Button' && n.props.children === name).props.onClick();
    assert.match(JSON.stringify(h.draw()), /PRIVATE ACTION FAILURE/);
    if (change === 'sessions') h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Activity scope').props.onChange({ target: { value: 'all' } });
    if (change === 'verification') h.nodes(h.draw()).find(n => n.type === 'input').props.onChange({ target: { checked: true } });
    if (change === 'owner') h.values.focusedSessionOwner = { connectionId: 'B', profile: 'default' };
    if (change === 'close') h.close();
    assert.doesNotMatch(JSON.stringify(h.draw()), /PRIVATE ACTION FAILURE/, change);
    if (change === 'sessions') h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Activity scope').props.onChange({ target: { value: 'current' } });
    if (change === 'verification') h.nodes(h.draw()).find(n => n.type === 'input').props.onChange({ target: { checked: false } });
    if (change === 'owner') h.values.focusedSessionOwner = { connectionId: 'local', profile: 'default' };
    if (change === 'close') h.open();
    assert.doesNotMatch(JSON.stringify(h.draw()), /PRIVATE ACTION FAILURE/, `${change} round-trip`);
  }
});

test('late action failures cannot repopulate errors after a filter or owner round-trip', async () => {
  for (const name of ['Expected', 'Inspect raw capture', 'Replay pending captures']) {
    for (const change of ['sessions', 'verification', 'owner', 'close']) {
      const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
      let reject;
      h.ctx.rest = () => new Promise((_, fail) => { reject = fail; });
      const request = h.nodes(h.draw()).find(n => n.type === 'Button' && n.props.children === name).props.onClick();
      const filter = value => change === 'sessions'
        ? h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Activity scope').props.onChange({ target: { value } })
        : h.nodes(h.draw()).find(n => n.type === 'input').props.onChange({ target: { checked: value } });
      if (change === 'sessions') { filter('all'); h.draw(); filter('current'); }
      if (change === 'verification') { filter(true); h.draw(); filter(false); }
      if (change === 'owner') { h.values.focusedSessionOwner = { connectionId: 'B', profile: 'default' }; h.draw(); h.values.focusedSessionOwner = { connectionId: 'local', profile: 'default' }; }
      if (change === 'close') { h.close(); h.open(); }
      h.draw(); reject(new Error('LATE PRIVATE FAILURE')); await request;
      const alerts = h.nodes(h.draw()).filter(n => n.props.role === 'alert').map(visibleText).join(' ');
      assert.doesNotMatch(alerts, /LATE PRIVATE FAILURE/, `${name}: ${change}`);
      assert.equal(replaySelector(h).props.disabled, false, 'pending state is released after completion');
    }
  }
});

test('unavailable or stale resources erase action failures and raw without recovery reviving late responses', async () => {
  for (const failure of ['activity', 'status', 'stale']) {
    const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
    const click = text => h.nodes(h.draw()).find(n => n.type === 'Button' && n.props.children === text).props.onClick();
    await click('Inspect raw capture');
    h.ctx.rest = async () => { throw new Error('OLD FAILURE'); };
    await click('Expected'); await click('Replay pending captures');
    let reject;
    h.ctx.rest = () => new Promise((_, fail) => { reject = fail; });
    const request = click('Expected');
    if (failure === 'activity') h.errors.events = new Error('offline');
    if (failure === 'status') h.errors.status = new Error('offline');
    if (failure === 'stale') h.data.status.observed_at = 0;
    h.draw(); // flush cleanup effects, as React does after the failed read
    const privateState = () => h.nodes(h.draw()).filter(n => n.props.role === 'alert' || n.type === 'pre').map(visibleText).join(' ');
    assert.doesNotMatch(privateState(), /OLD FAILURE|LOCAL RAW SECRET/, failure);
    delete h.errors.events; delete h.errors.status; h.data.status.observed_at = new Date().toISOString(); h.draw();
    reject(new Error('LATE FAILURE')); await request;
    assert.doesNotMatch(privateState(), /OLD FAILURE|LATE FAILURE|LOCAL RAW SECRET/, `${failure} recovery`);
  }
});

test('pending actions retain busy gating for retained callbacks and Replay stays busy across filters', async () => {
  for (const name of ['Expected', 'Inspect raw capture', 'Replay pending captures']) {
    const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
    const buttons = h.nodes(h.draw()).filter(n => n.type === 'Button' && ['Expected', 'Inspect raw capture', 'Replay pending captures'].includes(n.props.children));
    const select = replaySelector(h).props.onChange;
    let finish;
    h.ctx.rest = (path, options) => { h.calls.push({ path, options }); return new Promise(done => { finish = done; }); };
    const pending = buttons.find(n => n.props.children === name).props.onClick();
    if (name === 'Replay pending captures') {
      h.nodes(h.draw()).find(n => n.props['aria-label'] === 'Activity scope').props.onChange({ target: { value: 'all' } });
    }
    const tree = h.draw();
    assert.ok(h.nodes(tree).filter(n => n.type === 'Button' && ['Expected', 'Inspect raw capture', 'Replay pending captures'].includes(n.props.children)).every(n => n.props.disabled), name);
    // Directly invoke retained callbacks; disabled presentation alone is not a guard.
    for (const button of buttons) {
      const duplicate = button.props.onClick();
      assert.equal(h.calls.length, 1, `${name}: ${button.props.children}`);
      await duplicate;
    }
    select({ target: { value: 'qev' } });
    assert.equal(replaySelector(h).props.value, 'lite');
    finish({ success: true, content: 'fixture' }); await pending;
    assert.equal(replaySelector(h).props.disabled, false);
  }
});

test('changing the next Replay model clears only its start error, not an event review failure', async () => {
  const h = harness(); h.c.plugin.register(h.ctx); chooseReplay(h, 'lite');
  h.ctx.rest = async () => { throw new Error('fixture rejection'); };
  await h.nodes(h.draw()).find(n => n.props.children === 'Expected').props.onClick();
  await replayButton(h).props.onClick();
  const tree = chooseReplay(h, 'qev');
  const replay = h.nodes(tree).find(n => n.props['aria-label'] === 'Replay controls');
  assert.doesNotMatch(visibleText(replay), /Replay start failed/);
  assert.match(JSON.stringify(h.nodes(tree).find(n => n.type === 'article')), /Review failed: fixture rejection/);
  assert.equal(replayButton(h, tree).props.disabled, false);
});

const now = Date.parse('2026-11-10T16:00:00Z');
test('coverage separates observation from enforcement and distrusts stale or unknown health', () => {
  const c = load();
  assert.equal(typeof c.coverageView, 'function');
  const status = { observed_at: new Date(now).toISOString(), observation: { state: 'observing', provider: 'recorded provider' }, enforcement: { state: 'not_enabled' }, components: [{ healthy: true }] };
  assert.equal(c.coverageView(status, null, now).label, 'Observing');
  assert.equal(c.coverageView(status, null, now).enforcement, 'Not enabled');
  assert.equal(c.coverageView(status, null, now + 61000).healthy, false);
  assert.equal(c.coverageView({ ...status, observation: { state: 'unknown' } }, null, now).healthy, false);
  assert.equal(c.coverageView(status, new Error('offline'), now).label, 'Backend unavailable');
  assert.equal(c.coverageView(null, null, now).label, 'Checking coverage');
});
