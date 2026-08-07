/**
 * search.js
 * Lightweight client-side search over /assets/js/search-index.json.
 * No build step, no server required — works on GitHub Pages / S3 / Netlify.
 */
(function () {
  "use strict";

  var INDEX_URL_CANDIDATES = ["/assets/js/search-index.json"];

  function resolveIndexUrl() {
    // Compute a relative path to assets/js/search-index.json based on current depth.
    var depth = window.location.pathname.split("/").filter(Boolean).length;
    // Pages live either at root (index.html) or one level deep (python/xyz.html).
    var prefix = window.__COURSE_ROOT__ || "./";
    return prefix + "assets/js/search-index.json";
  }

  function debounce(fn, delay) {
    var timer;
    return function () {
      var args = arguments, ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, delay);
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("search-input");
    var resultsBox = document.getElementById("search-results");
    if (!input || !resultsBox) return;

    var indexData = null;
    fetch(resolveIndexUrl())
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) { indexData = data; })
      .catch(function () { indexData = []; });

    function render(matches, query) {
      resultsBox.innerHTML = "";
      if (!query) { resultsBox.classList.remove("open"); return; }
      if (!matches.length) {
        resultsBox.innerHTML = '<div class="result-empty">No results for "' + query + '"</div>';
        resultsBox.classList.add("open");
        return;
      }
      var root = window.__COURSE_ROOT__ || "./";
      matches.slice(0, 8).forEach(function (m) {
        var a = document.createElement("a");
        a.href = root + m.url;
        a.innerHTML = '<div class="result-track">' + m.track + '</div>' + m.title;
        resultsBox.appendChild(a);
      });
      resultsBox.classList.add("open");
    }

    var onSearch = debounce(function () {
      var query = input.value.trim().toLowerCase();
      if (!indexData || !query) { render([], query); return; }
      var matches = indexData.filter(function (item) {
        return (
          item.title.toLowerCase().indexOf(query) !== -1 ||
          item.track.toLowerCase().indexOf(query) !== -1 ||
          (item.keywords || []).join(" ").toLowerCase().indexOf(query) !== -1
        );
      });
      render(matches, query);
    }, 120);

    input.addEventListener("input", onSearch);
    input.addEventListener("focus", onSearch);
    document.addEventListener("click", function (e) {
      if (!resultsBox.contains(e.target) && e.target !== input) {
        resultsBox.classList.remove("open");
      }
    });

    // Keyboard shortcut: "/" focuses search
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== input) {
        e.preventDefault();
        input.focus();
      }
    });
  });
})();
