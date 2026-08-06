/* Execute tracker.js against a minimal DOM stub and assert its BEHAVIOR:
   batching, throttling, beacon-on-hide, idempotency ids, retry on failure.
   No jsdom — the tracker touches a small, known surface, so stubbing it is
   more honest than pulling a browser in. */
const fs = require('fs');
const vm = require('vm');

let PASS = 0, FAIL = 0;
function check(label, cond, detail) {
  if (cond) { PASS++; console.log('  PASS  ' + label); }
  else { FAIL++; console.log('  FAIL  ' + label + (detail ? '  ' + detail : '')); }
}

function makeEl(attrs, cls) {
  const el = {
    _attrs: attrs || {}, className: cls || '', style: {},
    getAttribute(n) { return n in this._attrs ? this._attrs[n] : null; },
    closest(sel) { return matches(this, sel) ? this : null; },
  };
  return el;
}
/* Minimal selector engine: leading .class, then any number of [attr] parts.
   Handles the compound selectors tracker.js actually uses, e.g.
   "[data-product-id][data-track-dwell]" and ".card[data-product-id]". */
function matches(el, sel) {
  if (!el || !el._attrs) return false;
  if (sel === 'a') return el._tag === 'a';
  const m = sel.match(/^(\.[A-Za-z0-9_-]+)?((\[[A-Za-z0-9_-]+\])*)$/);
  if (!m) return false;
  if (m[1] && (el.className || '').split(/\s+/).indexOf(m[1].slice(1)) < 0) return false;
  const attrs = (m[2] || '').match(/\[[A-Za-z0-9_-]+\]/g) || [];
  if (!m[1] && !attrs.length) return false;
  return attrs.every(a => a.slice(1, -1) in el._attrs);
}

function runScenario(opts) {
  opts = opts || {};
  const posts = [];       // fetch bodies
  const beacons = [];     // sendBeacon bodies
  const listeners = {};
  const timers = [];
  let now = 1000000;

  const doc = {
    cookie: 'sid=testsession123',
    visibilityState: 'visible',
    referrer: '',
    documentElement: { scrollHeight: 2000, scrollTop: 0 },
    _els: opts.els || [],
    querySelector(sel) { return this._els.find(e => matches(e, sel)) || null; },
    addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
  };

  const sandbox = {
    document: doc,
    location: { pathname: opts.path || '/' },
    navigator: {
      sendBeacon: opts.noBeacon ? undefined : function (url, blob) {
        if (opts.beaconFails) return false;
        beacons.push(blob._text); return true;
      },
    },
    window: null,
    Blob: function (parts) { this._text = parts.join(''); },
    fetch: function (url, init) {
      posts.push(init.body);
      return opts.fetchFails
        ? { catch: function (cb) { cb(new Error('offline')); return this; } }
        : { catch: function () { return this; } };
    },
    setTimeout: function (fn, ms) { timers.push({ fn, at: now + ms }); return timers.length; },
    clearTimeout: function (id) { if (timers[id - 1]) timers[id - 1].cancelled = true; },
    Date: class extends Date { constructor(...a) { super(...(a.length ? a : [now])); }
                               static now() { return now; } },
    Math: Math, JSON: JSON, Object: Object, parseInt: parseInt, crypto: undefined,
    console: console,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window.addEventListener = function (n, fn) { (listeners[n] = listeners[n] || []).push(fn); };
  sandbox.window.scrollY = 0;
  sandbox.window.innerHeight = 800;

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('app/web/static/tracker.js', 'utf8'), sandbox);

  return {
    posts, beacons, sandbox,
    fire(name, ev) { (listeners[name] || []).forEach(fn => fn(ev || {})); },
    advance(ms) {
      now += ms;
      timers.filter(t => !t.cancelled && !t.done && t.at <= now)
            .forEach(t => { t.done = true; t.fn(); });
    },
    setNow(v) { now = v; },
    api() { return sandbox.window.SmartReco; },
  };
}

console.log('== tracker.js behavior ==');

// --- 1. page_view on a plain page, batched not sent immediately
let s = runScenario({ path: '/' });
check('no network call on page load (batching, not per-event)', s.posts.length === 0 && s.beacons.length === 0,
      `posts=${s.posts.length} beacons=${s.beacons.length}`);
check('one event queued', s.api().stats().queued === 1, JSON.stringify(s.api().stats()));

// --- 2. flush happens on the 5s timer
s.advance(5000);
check('flushes after 5s', s.posts.length === 1, `posts=${s.posts.length}`);
let body = JSON.parse(s.posts[0]);
check('payload shape {events:[...]}', Array.isArray(body.events) && body.events.length === 1);
let ev = body.events[0];
check('event_type is page_view', ev.event_type === 'page_view', ev.event_type);
check('has event_uuid for idempotency', typeof ev.event_uuid === 'string' && ev.event_uuid.length > 10);
check('ts is ISO 8601', /^\d{4}-\d{2}-\d{2}T/.test(ev.ts), ev.ts);
check('queue emptied after flush', s.api().stats().queued === 0);

// --- 3. product page emits product_view with the id
const detailEl = makeEl({ 'data-product-id': '7', 'data-track-dwell': '1',
                          'data-slug': 'rag-course', 'data-category': 'ai', 'data-level': 'advanced' });
s = runScenario({ path: '/course/rag-course', els: [detailEl] });
s.advance(5000);
ev = JSON.parse(s.posts[0]).events[0];
check('product_view on a detail page', ev.event_type === 'product_view', ev.event_type);
check('carries product_id as an int', ev.product_id === 7, String(ev.product_id));
check('carries slug/category/level in meta',
      ev.meta.slug === 'rag-course' && ev.meta.category === 'ai' && ev.meta.level === 'advanced',
      JSON.stringify(ev.meta));

// --- 4. batch size trigger: 10 events flush without waiting
s = runScenario({ path: '/' });
for (let i = 0; i < 9; i++) s.api().track('custom', {});
check('flushes at MAX_BATCH=10 without the timer', s.posts.length === 1, `posts=${s.posts.length}`);
check('batch contains exactly 10', JSON.parse(s.posts[0]).events.length === 10);

// --- 5. scroll throttling + highest-milestone-only
s = runScenario({ path: '/course/x', els: [detailEl] });
s.sandbox.window.scrollY = 0;
for (let i = 0; i < 50; i++) s.fire('scroll');          // 50 raw scroll events
check('scroll handler does not emit per event (throttled)',
      s.api().stats().queued === 1, JSON.stringify(s.api().stats()));  // just product_view
s.sandbox.window.scrollY = 1200;                        // 1200/(2000-800) = 100%
s.fire('scroll'); s.advance(150);
let types = [];
s.advance(5000);
JSON.parse(s.posts[0]).events.forEach(e => types.push(e.event_type));
const depthEvents = JSON.parse(s.posts[0]).events.filter(e => e.event_type === 'scroll_depth');
check('scrolling 0→100% emits ONE scroll_depth, not four',
      depthEvents.length === 1, JSON.stringify(types));
check('reports the highest milestone (100)',
      depthEvents[0] && depthEvents[0].meta.depth === 100,
      depthEvents[0] && JSON.stringify(depthEvents[0].meta));

// --- 6. dwell on hide, via sendBeacon
s = runScenario({ path: '/course/x', els: [detailEl] });
s.advance(4000);                       // 4s of visible time (advance < 5s flush)
s.setNow(1000000 + 4000);
s.sandbox.document.visibilityState = 'hidden';
s.fire('visibilitychange');
check('uses sendBeacon on hide (survives unload)', s.beacons.length >= 1,
      `beacons=${s.beacons.length} posts=${s.posts.length}`);
const beaconEvents = s.beacons.map(b => JSON.parse(b).events).flat();
const dwell = beaconEvents.find(e => e.event_type === 'product_dwell');
check('emits product_dwell on hide', !!dwell, JSON.stringify(beaconEvents.map(e => e.event_type)));
check('dwell_ms is the visible time (~4000)', dwell && dwell.dwell_ms >= 3900 && dwell.dwell_ms <= 4100,
      dwell && String(dwell.dwell_ms));

// --- 7. dwell below the floor is not sent
s = runScenario({ path: '/course/x', els: [detailEl] });
s.advance(300);
s.setNow(1000000 + 300);
s.sandbox.document.visibilityState = 'hidden';
s.fire('visibilitychange');
const shortDwell = s.beacons.map(b => JSON.parse(b).events).flat().filter(e => e.event_type === 'product_dwell');
check('a 300ms bounce emits no dwell event', shortDwell.length === 0, JSON.stringify(shortDwell));

// --- 8. failed send is retried, not lost
s = runScenario({ path: '/', fetchFails: true });
s.advance(5000);
check('fetch failure re-queues the batch (not dropped)',
      s.api().stats().queued === 1, JSON.stringify(s.api().stats()));

// --- 9. click delegation
const card = makeEl({ 'data-product-id': '42', 'data-slug': 'ml' }, 'card');
const anchor = { _tag: 'a', _attrs: {}, closest(sel) { if (sel === 'a') return this;
                                                       return matches(card, sel) ? card : null; } };
s = runScenario({ path: '/', els: [] });
s.fire('click', { target: anchor });
const clickEvents = [];
s.advance(5000);
(s.posts.concat(s.beacons)).forEach(b => JSON.parse(b).events.forEach(e => clickEvents.push(e)));
const pc = clickEvents.find(e => e.event_type === 'product_click');
check('delegated click emits product_click', !!pc, JSON.stringify(clickEvents.map(e => e.event_type)));
check('click carries product_id 42', pc && pc.product_id === 42, pc && String(pc.product_id));

// --- 10. queue cap
s = runScenario({ path: '/', fetchFails: true });
for (let i = 0; i < 400; i++) s.api().track('spam', {});
check('queue is capped (no unbounded memory growth)',
      s.api().stats().queued <= 200, JSON.stringify(s.api().stats()));

console.log(`\n== ${PASS} passed, ${FAIL} failed ==`);
process.exit(FAIL ? 1 : 0);
