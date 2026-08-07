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
    el.textContent = text;             // never innerHTML
    log.appendChild(el);
    scrollDown();
  }

  function addBot(text, cards, isError) {
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
          else addBot(m.content, m.cards, false);
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
        addBot(data.answer, data.cards, false);
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

  function openPanel() {
    panel.hidden = false;
    openBtn.hidden = true;
    openBtn.setAttribute("aria-expanded", "true");
    loadHistory();
    input.focus();
  }

  function closePanel() {
    panel.hidden = true;
    openBtn.hidden = false;
    openBtn.setAttribute("aria-expanded", "false");
    openBtn.focus();
  }

  openBtn.addEventListener("click", openPanel);
  if (closeBtn) closeBtn.addEventListener("click", closePanel);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.hidden) closePanel();
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
