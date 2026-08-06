/* tracker.js — behavioral event capture (arch v1 §4, v2 ▲A8/▲A13). No deps.
 *
 * The requirement it answers: track real activity WITHOUT slowing or breaking
 * the page. Three rules follow from that, and every design choice below is one
 * of them:
 *
 *   1. Never block. Events go into an in-memory array; the network happens on a
 *      timer, never on the interaction itself. A click handler that awaits a
 *      POST makes the click feel slow — so no handler here ever awaits.
 *   2. Never lose the last batch. A user who reads a page and closes the tab
 *      produces the most interesting event (a long dwell) at the exact moment
 *      a normal fetch is killed. sendBeacon survives unload; fetch does not.
 *   3. Never let tracking break the page. The whole file is wrapped in a
 *      try/catch and every send is fire-and-forget with a swallowed rejection.
 *      An analytics bug must not take the product down with it.
 *
 * Throttling: scroll and mousemove fire at screen-refresh rates. Raw handlers
 * on them are the classic jank source, so scroll is sampled on a 150ms timer
 * and emits only the HIGHEST milestone crossed (▲A13) — 25/50/75/100 produce at
 * most four events per page, not hundreds.
 */
(function () {
  "use strict";

  var ENDPOINT = "/api/events";
  var MAX_BATCH = 10;          // flush at 10 events…
  var FLUSH_MS = 5000;         // …or 5s, whichever comes first
  var QUEUE_CAP = 200;         // hard cap; a runaway page can't eat memory
  var SCROLL_SAMPLE_MS = 150;
  var MIN_DWELL_MS = 1000;     // below this it's a bounce, not a read
  var MAX_DWELL_MS = 30 * 60 * 1000;

  var queue = [];
  var timer = null;
  var dropped = 0;

  // ---- identity ---------------------------------------------------------
  // The session rides the `sid` cookie, which the server sets and sendBeacon
  // sends automatically. This matters: sendBeacon cannot set headers (trap #5),
  // so any scheme based on an Authorization header would silently lose exactly
  // the unload events we most want.
  function cookie(name) {
    var m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    // Older browsers: good enough for idempotency, which only needs uniqueness.
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  // ---- queue ------------------------------------------------------------
  function track(type, payload) {
    try {
      if (queue.length >= QUEUE_CAP) { dropped++; return; }
      var ev = {
        event_uuid: uuid(),
        event_type: type,
        ts: new Date().toISOString(),
        product_id: null,
        query: null,
        dwell_ms: null,
        meta: {}
      };
      for (var k in payload) {
        if (Object.prototype.hasOwnProperty.call(payload, k)) ev[k] = payload[k];
      }
      queue.push(ev);
      if (queue.length >= MAX_BATCH) flush();
      else schedule();
    } catch (e) { /* tracking must never break the page */ }
  }

  function schedule() {
    if (timer !== null) return;
    timer = setTimeout(function () { timer = null; flush(); }, FLUSH_MS);
  }

  function flush(useBeacon) {
    if (timer !== null) { clearTimeout(timer); timer = null; }
    if (!queue.length) return;

    var batch = queue;
    queue = [];
    var body = JSON.stringify({ events: batch });

    // On unload, only sendBeacon is guaranteed to survive the page going away.
    if (useBeacon && navigator.sendBeacon) {
      var ok = navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
      // Beacon refuses when its queue is full; put the events back so a later
      // flush can retry rather than dropping them silently.
      if (!ok) { queue = batch.concat(queue).slice(0, QUEUE_CAP); }
      return;
    }

    // keepalive lets this outlive a normal navigation too.
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      credentials: "same-origin",
      keepalive: true
    })["catch"](function () {
      // Network down: keep them for the next flush. Bounded by QUEUE_CAP, so a
      // long offline stretch degrades to dropping the oldest rather than
      // growing without limit.
      queue = batch.concat(queue).slice(0, QUEUE_CAP);
    });
  }

  // ---- page view --------------------------------------------------------
  var detail = document.querySelector("[data-product-id][data-track-dwell]");
  var pageProductId = detail ? parseInt(detail.getAttribute("data-product-id"), 10) : null;

  function pageMeta() {
    return { path: location.pathname, referrer: document.referrer || "" };
  }

  if (detail) {
    track("product_view", {
      product_id: pageProductId,
      meta: {
        slug: detail.getAttribute("data-slug") || "",
        category: detail.getAttribute("data-category") || "",
        level: detail.getAttribute("data-level") || "",
        path: location.pathname
      }
    });
  } else {
    track("page_view", { meta: pageMeta() });
  }

  // Search results: the query is the signal, and the result count tells us
  // whether it was a good one (a zero-result search is a catalog gap).
  var searchEl = document.querySelector("[data-search-query]");
  if (searchEl) {
    var q = searchEl.getAttribute("data-search-query") || "";
    if (q) {
      track("search", {
        query: q,
        meta: { results: parseInt(searchEl.getAttribute("data-result-count"), 10) || 0 }
      });
    }
  }

  // ---- dwell ------------------------------------------------------------
  // Time with the page actually VISIBLE, not wall-clock since load: a tab left
  // open overnight is not thirty thousand seconds of interest. The timer stops
  // on hide and resumes on show.
  var visibleSince = document.visibilityState === "visible" ? Date.now() : null;
  var accumulated = 0;
  var dwellSent = false;

  function activeMs() {
    return accumulated + (visibleSince ? Date.now() - visibleSince : 0);
  }

  function sendDwell(useBeacon) {
    if (dwellSent) return;
    var ms = activeMs();
    if (ms < MIN_DWELL_MS || ms > MAX_DWELL_MS) return;
    dwellSent = true;
    track(detail ? "product_dwell" : "page_dwell", {
      product_id: pageProductId,
      dwell_ms: Math.round(ms),
      meta: { path: location.pathname, max_scroll: maxMilestone }
    });
    flush(useBeacon);
  }

  // ---- scroll depth (▲A13) ----------------------------------------------
  var MILESTONES = [25, 50, 75, 100];
  var maxMilestone = 0;
  var scrollPending = false;

  function scrollPercent() {
    var doc = document.documentElement;
    var scrollable = doc.scrollHeight - window.innerHeight;
    if (scrollable <= 0) return 100;      // page fits: fully seen
    return Math.min(100, Math.round(((window.scrollY || doc.scrollTop) / scrollable) * 100));
  }

  function sampleScroll() {
    scrollPending = false;
    var pct = scrollPercent();
    var reached = 0;
    for (var i = 0; i < MILESTONES.length; i++) {
      if (pct >= MILESTONES[i]) reached = MILESTONES[i];
    }
    // Emit only the highest NEW milestone: scrolling 0→100 in one flick is one
    // event, not four, and scrolling back up emits nothing.
    if (reached > maxMilestone) {
      maxMilestone = reached;
      track("scroll_depth", {
        product_id: pageProductId,
        meta: { depth: reached, path: location.pathname }
      });
    }
  }

  window.addEventListener("scroll", function () {
    if (scrollPending) return;            // throttle: at most one sample/150ms
    scrollPending = true;
    setTimeout(sampleScroll, SCROLL_SAMPLE_MS);
  }, { passive: true });                  // passive: never block scrolling

  sampleScroll();                         // a short page counts as 100% seen

  // ---- clicks (delegated) -----------------------------------------------
  // One listener on document rather than one per card: cards are re-rendered
  // and a delegated listener cannot go stale or leak.
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;

    var dismiss = t.closest("[data-rec-dismiss]");
    if (dismiss) {
      track("rec_dismiss", {
        product_id: parseInt(dismiss.getAttribute("data-rec-dismiss"), 10) || null,
        meta: { path: location.pathname }
      });
      var card = dismiss.closest(".card");
      if (card) card.style.display = "none";   // immediate feedback, no round-trip
      return;
    }

    var cta = t.closest("[data-cta]");
    if (cta) {
      track("cta_click", {
        product_id: parseInt(cta.getAttribute("data-product-id"), 10) || pageProductId,
        meta: { cta: cta.getAttribute("data-cta") || "", path: location.pathname }
      });
      return;
    }

    var recCard = t.closest("[data-rec-id]");
    if (recCard && t.closest("a")) {
      track("rec_click", {
        product_id: parseInt(recCard.getAttribute("data-product-id"), 10) || null,
        meta: {
          rec_id: parseInt(recCard.getAttribute("data-rec-id"), 10) || null,
          rank: parseInt(recCard.getAttribute("data-rank"), 10) || null
        }
      });
      // A click that navigates away races the 5s timer, so flush now, by beacon.
      flush(true);
      return;
    }

    var card = t.closest(".card[data-product-id]");
    if (card && t.closest("a")) {
      var onSearch = !!document.querySelector("[data-search-query]");
      track(onSearch ? "search_result_click" : "product_click", {
        product_id: parseInt(card.getAttribute("data-product-id"), 10) || null,
        query: onSearch ? (searchEl.getAttribute("data-search-query") || null) : null,
        meta: { slug: card.getAttribute("data-slug") || "", path: location.pathname }
      });
      flush(true);
      return;
    }

    var cat = t.closest("[data-category][href]");
    if (cat) {
      track("category_filter", { meta: { category: cat.getAttribute("data-category") || "" } });
    }
  }, true);

  // ---- lifecycle --------------------------------------------------------
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      if (visibleSince) { accumulated += Date.now() - visibleSince; visibleSince = null; }
      // hidden is the ONLY reliable "page is going away" signal on mobile —
      // unload and beforeunload are not fired when an app is backgrounded.
      sendDwell(true);
      flush(true);
    } else {
      visibleSince = Date.now();
    }
  });

  // pagehide covers bfcache navigation, where visibilitychange may not fire.
  window.addEventListener("pagehide", function () {
    sendDwell(true);
    flush(true);
  });

  // ---- public surface ---------------------------------------------------
  // Exposed so server-rendered pages and tests can emit domain events
  // (conversion, for instance) without importing anything.
  window.SmartReco = {
    track: track,
    flush: flush,
    stats: function () {
      return { queued: queue.length, dropped: dropped, dwell_ms: Math.round(activeMs()),
               max_scroll: maxMilestone, sid: cookie("sid") };
    }
  };
})();
