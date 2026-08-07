/**
 * theme.js
 * Handles dark / light mode toggling and persistence.
 * Runs immediately (before paint) via an inline snippet in <head> to avoid
 * a flash of the wrong theme; this file wires up the toggle button.
 */
(function () {
  "use strict";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("course-theme", theme); } catch (e) { /* storage unavailable */ }
    var btn = document.getElementById("theme-toggle-btn");
    if (btn) {
      btn.textContent = theme === "dark" ? "☀️" : "🌙";
      btn.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    }
  }

  function getPreferredTheme() {
    try {
      var stored = localStorage.getItem("course-theme");
      if (stored === "dark" || stored === "light") return stored;
    } catch (e) { /* ignore */ }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(document.documentElement.getAttribute("data-theme") || getPreferredTheme());
    var btn = document.getElementById("theme-toggle-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        var current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
        applyTheme(current === "dark" ? "light" : "dark");
      });
    }
  });
})();
