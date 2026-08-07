/* Shell interactions: theme toggle and the mobile nav drawer.
 *
 * Kept separate from tracker.js on purpose — an analytics bug must not take
 * the navigation down with it, and vice versa. Both are try/catch-wrapped
 * IIFEs for the same reason (arch §4.1).
 */
(function () {
  "use strict";

  try {
    var root = document.documentElement;
    var toggle = document.getElementById("theme-toggle");

    function current() {
      var set = root.getAttribute("data-theme");
      if (set) return set;
      // No explicit choice yet: report what the system is actually showing,
      // so the first click flips to the opposite of what the user sees rather
      // than appearing to do nothing.
      return window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        var next = current() === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        try { localStorage.setItem("sr-theme", next); } catch (e) { /* private mode */ }
        toggle.setAttribute("aria-label",
          next === "dark" ? "Switch to light theme" : "Switch to dark theme");
      });
    }

    var menu = document.getElementById("menu-toggle");
    var sidebar = document.getElementById("sidebar");
    if (menu && sidebar) {
      menu.addEventListener("click", function () {
        var open = sidebar.classList.toggle("open");
        menu.setAttribute("aria-expanded", open ? "true" : "false");
      });
      // Any navigation closes the drawer; leaving it open over the new page
      // is the classic mobile-nav bug.
      sidebar.addEventListener("click", function (e) {
        if (e.target.closest("a")) {
          sidebar.classList.remove("open");
          menu.setAttribute("aria-expanded", "false");
        }
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && sidebar.classList.contains("open")) {
          sidebar.classList.remove("open");
          menu.setAttribute("aria-expanded", "false");
          menu.focus();
        }
      });
    }
  } catch (e) {
    if (window.console) console.warn("ui.js", e);
  }
})();
