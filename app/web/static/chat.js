/* Career advisor panel.
 *
 * The one piece of client-side state in an otherwise server-rendered app
 * (arch §1.1). It earns the exception: a chat that reloads the page on every
 * message is not a chat, and the panel must not navigate away from whatever
 * the user is currently reading.
 *
 * Three properties worth stating, because each is a bug if you skip it:
 *   - Rendering is escape-first. Model output is untrusted text; it is written
 *     with textContent and a tiny whitelist formatter, never innerHTML.
 *   - Exactly one request may be in flight. A double-submit costs a real model
 *     call and interleaves two answers into one log.
 *   - The thread is restored from the server on open, so the conversation
 *     survives navigation without keeping message state in the page.
 */
(function () {
  "use strict";

  var panel = document.getElementById("chat-panel");
  var openBtn = document.getElementById("chat-open");
  if (!panel || !openBtn) return;

  var closeBtn = document.getElementById("chat-close");
  var expandBtn = document.getElementById("chat-expand");
  var resetBtn = document.getElementById("chat-reset");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");
  var log = document.getElementById("chat-log");
  var intro = document.getElementById("chat-intro");
  var statusEl = document.getElementById("chat-status");
  var sendBtn = document.getElementById("chat-send");

  var conversationId = null;
  var busy = false;
  var loaded = false;

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  /* ---- rendering ------------------------------------------------------- */

  function inlineInto(el, text) {
    // Supports **bold** only. A full markdown parser is a dependency and an
    // XSS surface; bold is the one thing the model reliably uses for course
    // titles. Everything else lands as plain text, which is the safe default.
    var parts = String(text).split(/\*\*(.+?)\*\*/g);
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2 === 1) {
        var b = document.createElement("strong");
        b.textContent = parts[i];
        el.appendChild(b);
      } else {
        el.appendChild(document.createTextNode(parts[i]));
      }
    }
  }

  function renderBody(container, text) {
    // The [[id:N]] citation markers are the contract with the server, not
    // something to show a reader — the cards below the message are their
    // visible form.
    var clean = String(text || "").replace(/\s*\[\[id:\d+\]\]/g, "");
    var blocks = clean.split(/\n{2,}/);
    blocks.forEach(function (block) {
      var lines = block.split("\n");
      var bullets = lines.filter(function (l) { return /^\s*[-*]\s+/.test(l); });
      if (bullets.length && bullets.length === lines.filter(function (l) {
        return l.trim();
      }).length) {
        var ul = document.createElement("ul");
        bullets.forEach(function (l) {
          var li = document.createElement("li");
          inlineInto(li, l.replace(/^\s*[-*]\s+/, ""));
          ul.appendChild(li);
        });
        container.appendChild(ul);
      } else if (block.trim()) {
        var p = document.createElement("p");
        inlineInto(p, block.replace(/\n/g, " "));
        container.appendChild(p);
      }
    });
  }

  function addUser(text) {
    var el = document.createElement("div");
    el.className = "msg user";
    // The text lives in an inner span so full-screen mode can stretch the
    // outer .msg to the reading column while the coloured bubble still
    // shrink-wraps. In docked mode the span is transparent and inherits, so
    // this changes nothing there.
    var bubble = document.createElement("span");
    bubble.textContent = text;         // never innerHTML
    el.appendChild(bubble);
    log.appendChild(el);
    scrollDown();
  }

  /* Learning-path diagram. The server sends mermaid source built in Python
     from the real prereq ladder (app/chat/pathway.py) — the model never draws
     it. Mermaid is loaded lazily on first use so a user who never triggers a
     pathway never pays for the library. */
  var mermaidReady = null;
  function ensureMermaid() {
    if (mermaidReady) return mermaidReady;
    mermaidReady = new Promise(function (resolve) {
      if (window.mermaid) { resolve(window.mermaid); return; }
      var s = document.createElement("script");
      s.src = "/static/vendor/mermaid.min.js";
      s.onload = function () {
        if (window.mermaid) {
          // fontSize is set here rather than in CSS: mermaid bakes text metrics
          // into the SVG at render time, so a stylesheet rule applied
          // afterwards resizes the glyphs without resizing the boxes around
          // them, and the labels overflow their nodes.
          window.mermaid.initialize({
            startOnLoad: false, securityLevel: "strict", theme: "neutral",
            fontSize: 15,
            flowchart: { nodeSpacing: 34, rankSpacing: 46, padding: 12,
                         useMaxWidth: false }
          });
        }
        resolve(window.mermaid || null);
      };
      // A missing or blocked library must not break the reply — the steps
      // list below the diagram carries the same information as text.
      s.onerror = function () { resolve(null); };
      document.head.appendChild(s);
    });
    return mermaidReady;
  }

  function addPathway(wrap, pathway) {
    if (!pathway || !pathway.mermaid) return;
    var box = document.createElement("div");
    box.className = "chat-pathway";
    var head = document.createElement("b");
    head.textContent = pathway.goal ? "Your path to " + pathway.goal : "Suggested order";
    box.appendChild(head);

    // The header names the destination; this says what the path costs to
    // walk. Total-price-and-steps up front is the question anyone reading a
    // three-course plan asks immediately, and burying it under the diagram
    // makes the plan feel evasive about it.
    var steps = pathway.steps || [];
    if (steps.length) {
      var total = steps.reduce(function (sum, s) { return sum + (s.price || 0); }, 0);
      var sub = document.createElement("span");
      sub.className = "pathway-sub";
      sub.textContent = steps.length + " steps · " +
        (total ? "₹" + Math.round(total).toLocaleString("en-IN") + " total" : "free") +
        " · start today";
      box.appendChild(sub);
    }

    var target = document.createElement("div");
    target.className = "mermaid-target";
    box.appendChild(target);

    // Always render the ordered steps as text first. If mermaid loads, the
    // diagram appears above them; if it doesn't, this is still a usable
    // answer rather than an empty box.
    var ol = document.createElement("ol");
    ol.className = "pathway-steps";
    steps.forEach(function (s) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "/course/" + s.slug;
      a.textContent = s.title;
      a.setAttribute("data-rec-click", "1");
      a.setAttribute("data-product-id", s.id);
      li.appendChild(a);
      var meta = document.createElement("span");
      meta.textContent = " — " + (s.level || "") +
        (s.price ? " · ₹" + Math.round(s.price).toLocaleString("en-IN") : " · Free");
      li.appendChild(meta);
      // What this rung actually gets you. Server-supplied and de-duplicated
      // against the previous step, so it is the delta rather than a repeat.
      if (s.gain) {
        var gain = document.createElement("em");
        gain.className = "pathway-gain";
        gain.appendChild(document.createTextNode("You'll be able to: "));
        // Each skill is its own ask-chip. A path that names "Mixture of
        // Experts" and leaves the reader to look it up elsewhere has handed
        // them homework; one click turns an unfamiliar term into the next
        // question, which is the conversation the advisor exists to have.
        s.gain.split(",").forEach(function (skill, i) {
          skill = skill.trim();
          if (!skill) return;
          if (i) gain.appendChild(document.createTextNode(", "));
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "skill-ask";
          btn.textContent = skill;
          btn.title = "What is " + skill + "?";
          btn.setAttribute("data-ask",
            "What is " + skill + ", and how would it help me in my target role?");
          gain.appendChild(btn);
        });
        li.appendChild(gain);
      }
      ol.appendChild(li);
    });
    box.appendChild(ol);
    wrap.appendChild(box);

    // Kept on the node so a width change can re-render it. Mermaid produces a
    // fixed-size SVG, so without the source there is no way to redraw at the
    // new width and the diagram stays small in a large panel.
    target.__mermaidSrc = pathway.mermaid;
    drawDiagram(target);
  }

  function drawDiagram(target) {
    var src = target.__mermaidSrc;
    if (!src) return;
    ensureMermaid().then(function (m) {
      if (!m) return;
      var id = "pw" + Date.now() + Math.floor(Math.random() * 1000);
      try {
        m.render(id, src).then(function (out) {
          target.innerHTML = out.svg;   // mermaid output, securityLevel strict
          scrollDown();
        }).catch(function () { /* steps list already shown */ });
      } catch (e) { /* same */ }
    });
  }

  // Debounced: applyFull can be followed by a resize event for the same
  // change, and rendering every diagram in a long thread twice is visible.
  var reflowTimer = null;
  function reflowDiagrams() {
    clearTimeout(reflowTimer);
    reflowTimer = setTimeout(function () {
      log.querySelectorAll(".mermaid-target").forEach(drawDiagram);
    }, 120);
  }

  /* The offer. Shown only when the server graded buy-intent warm or hot
     (app/chat/intent.py) — never on an ordinary advice turn. */
  function addOffer(wrap, offer) {
    if (!offer || !offer.product_id) return;
    var box = document.createElement("div");
    box.className = "chat-offer " + (offer.level === "hot" ? "hot" : "warm");
    var p = document.createElement("span");
    p.textContent = offer.text || "Interested?";
    box.appendChild(p);
    var a = document.createElement("a");
    a.className = "chat-offer-cta";
    a.href = "/course/" + (offer.slug || "") ;
    a.textContent = offer.level === "hot" ? "Enrol now" : "See details";
    a.setAttribute("data-rec-click", "1");
    a.setAttribute("data-product-id", offer.product_id);
    box.appendChild(a);
    wrap.appendChild(box);
  }

  function addBot(text, cards, isError, pathway, offer) {
    var wrap = document.createElement("div");
    wrap.className = "msg bot" + (isError ? " err" : "");
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    renderBody(bubble, text);
    wrap.appendChild(bubble);

    if (cards && cards.length) {
      var box = document.createElement("div");
      box.className = "chat-cards";
      cards.forEach(function (c) {
        var a = document.createElement("a");
        a.className = "chat-card";
        a.href = "/course/" + c.slug;
        // Cards are recommendations the user can act on, so a click is a
        // tracked rec_click — the same feedback signal the cards on
        // /recommendations emit (§5).
        a.setAttribute("data-rec-click", "1");
        a.setAttribute("data-product-id", c.id);
        var b = document.createElement("b");
        b.textContent = c.title;
        var s = document.createElement("span");
        var price = c.price ? "₹" + Math.round(c.price).toLocaleString("en-IN") : "Free";
        s.textContent = [c.category, c.level, price].filter(Boolean).join(" · ");
        a.appendChild(b);
        a.appendChild(s);
        box.appendChild(a);
      });
      wrap.appendChild(box);
    }
    addPathway(wrap, pathway);
    addOffer(wrap, offer);
    log.appendChild(wrap);
    scrollDown();
  }

  function showTyping() {
    var el = document.createElement("div");
    el.className = "msg bot";
    el.id = "chat-typing";
    var d = document.createElement("div");
    d.className = "chat-typing";
    d.appendChild(document.createElement("i"));
    d.appendChild(document.createElement("i"));
    d.appendChild(document.createElement("i"));
    el.appendChild(d);
    log.appendChild(el);
    scrollDown();
  }

  function clearTyping() {
    var el = document.getElementById("chat-typing");
    if (el) el.remove();
  }

  /* ---- server ---------------------------------------------------------- */

  function loadHistory() {
    if (loaded) return;
    loaded = true;
    fetch("/api/chat/history", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.messages || !data.messages.length) return;
        conversationId = data.conversation_id;
        if (intro) intro.hidden = true;
        data.messages.forEach(function (m) {
          if (m.role === "user") addUser(m.content);
          else addBot(m.content, m.cards, false, m.pathway, null);
        });
      })
      .catch(function () { /* an empty panel is a fine failure mode here */ });
  }

  function send(text) {
    if (busy) return;
    text = String(text || "").trim();
    if (!text) return;

    busy = true;
    sendBtn.disabled = true;
    if (intro) intro.hidden = true;
    addUser(text);
    input.value = "";
    input.style.height = "auto";
    showTyping();

    fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: conversationId })
    })
      .then(function (r) {
        if (r.status === 401) throw new Error("Your session expired — log in again.");
        if (!r.ok) throw new Error("The advisor is unavailable right now.");
        return r.json();
      })
      .then(function (data) {
        clearTyping();
        conversationId = data.conversation_id;
        addBot(data.answer, data.cards, false, data.pathway, data.offer);
        if (statusEl && data.model === "offline") {
          statusEl.textContent = "Catalog search only — model offline";
        }
      })
      .catch(function (err) {
        clearTyping();
        addBot(err.message || "Something went wrong. Try again.", null, true);
      })
      .then(function () {
        busy = false;
        sendBtn.disabled = false;
        input.focus();
      });
  }

  /* ---- wiring ---------------------------------------------------------- */

  // Size is remembered across navigations. The panel already restores its
  // thread on open, so resetting to half-width on every page click would make
  // the size feel like something the app keeps taking back.
  var FULL_KEY = "smartreco.chat.full";
  function storedFull() {
    try { return localStorage.getItem(FULL_KEY) === "1"; } catch (e) { return false; }
  }
  function rememberFull(on) {
    try { localStorage.setItem(FULL_KEY, on ? "1" : "0"); } catch (e) { /* private mode */ }
  }

  function applyFull(on) {
    panel.classList.toggle("full", on);
    document.body.classList.toggle("chat-full", on);
    document.body.classList.toggle("chat-docked", !on && !panel.hidden);
    if (expandBtn) {
      expandBtn.setAttribute("aria-pressed", on ? "true" : "false");
      expandBtn.setAttribute("aria-label",
        on ? "Exit full screen" : "Expand to full screen");
    }
    // Width changed, so the diagram's available box did too. Mermaid renders
    // to a fixed-size SVG, so re-rendering is what actually uses the new room
    // rather than just letting the old SVG sit in a wider container.
    reflowDiagrams();
  }

  // The panel docks to the right half and the page reflows to sit beside it,
  // which is a body-level layout change — hence a class on <body> rather than
  // a style on the panel. CSS owns the width; JS only says open or shut.
  function openPanel() {
    panel.hidden = false;
    applyFull(storedFull());
    openBtn.hidden = true;
    openBtn.setAttribute("aria-expanded", "true");
    loadHistory();
    input.focus();
  }

  function closePanel() {
    panel.hidden = true;
    // Both classes drop on close: leaving `chat-full` set would keep the
    // shell's margin overridden while nothing is covering it.
    document.body.classList.remove("chat-docked", "chat-full");
    openBtn.hidden = false;
    openBtn.setAttribute("aria-expanded", "false");
    openBtn.focus();
  }

  openBtn.addEventListener("click", openPanel);
  if (closeBtn) closeBtn.addEventListener("click", closePanel);

  if (expandBtn) {
    expandBtn.addEventListener("click", function () {
      var next = !panel.classList.contains("full");
      rememberFull(next);
      applyFull(next);
      input.focus();
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || panel.hidden) return;
    // Escape steps back one level rather than closing outright. Losing a
    // full-screen conversation to the key you pressed to un-maximise it is
    // the kind of thing you only forgive once.
    if (panel.classList.contains("full")) {
      rememberFull(false);
      applyFull(false);
      return;
    }
    closePanel();
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    send(input.value);
  });

  // Enter sends, Shift+Enter breaks the line — the convention every chat
  // interface uses, so anything else feels broken.
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 110) + "px";
  });

  log.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-ask]");
    if (chip) send(chip.getAttribute("data-ask"));
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      if (busy) return;
      fetch("/api/chat/reset", { method: "POST", credentials: "same-origin" })
        .then(function () {
          conversationId = null;
          log.querySelectorAll(".msg").forEach(function (n) { n.remove(); });
          if (intro) intro.hidden = false;
          input.focus();
        })
        .catch(function () { /* leave the thread as it was */ });
    });
  }
})();
