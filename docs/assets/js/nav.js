/**
 * nav.js
 * Sidebar mobile toggle, TOC active-section highlighting, reading progress bar,
 * automatic reading-time calculation, and active sidebar link detection.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    /* ---------- Mobile sidebar toggle ---------- */
    var toggleBtn = document.getElementById("sidebar-toggle-btn");
    var sidebar = document.getElementById("sidebar");
    var backdrop = document.getElementById("sidebar-backdrop");

    function openSidebar() {
      if (sidebar) sidebar.classList.add("open");
      if (backdrop) backdrop.classList.add("open");
    }
    function closeSidebar() {
      if (sidebar) sidebar.classList.remove("open");
      if (backdrop) backdrop.classList.remove("open");
    }
    if (toggleBtn) {
      toggleBtn.addEventListener("click", function () {
        if (sidebar && sidebar.classList.contains("open")) closeSidebar();
        else openSidebar();
      });
    }
    if (backdrop) backdrop.addEventListener("click", closeSidebar);

    /* ---------- Highlight current page in sidebar ---------- */
    /* Compare the full resolved path, not just the filename — many tracks
       share filenames like interview-questions.html / index.html / architecture.html,
       so a filename-only match would highlight several links at once. */
    var herePath = window.location.pathname;
    document.querySelectorAll(".sidebar-link").forEach(function (link) {
      var href = link.getAttribute("href");
      if (!href) return; /* skip disabled <span> placeholders */
      var linkPath = new URL(href, window.location.href).pathname;
      if (linkPath === herePath) {
        link.classList.add("active");
      }
    });

    /* ---------- Collapsible sidebar sections ---------- */
    /* Each topic collapses; the section containing the current page auto-opens,
       so the menu stays short and focused on the tutorial you're reading. */
    var ALWAYS_OPEN = ["Get Started"]; /* section titles that start expanded */
    document.querySelectorAll(".sidebar-section").forEach(function (section) {
      var title = section.querySelector(".sidebar-section-title");
      var links = section.querySelectorAll(".sidebar-link");
      if (!title || links.length === 0) return; /* skip label-only sections */

      section.classList.add("is-collapsible");
      var name = title.textContent.trim();
      var key = "nav-open:" + name;
      var hasActive = !!section.querySelector(".sidebar-link.active");
      var alwaysOpen = ALWAYS_OPEN.indexOf(name) !== -1;

      var saved = null;
      try { saved = localStorage.getItem(key); } catch (e) {}

      var open;
      if (hasActive || alwaysOpen) {
        open = true;                 /* current topic (and Home) always expanded */
      } else if (saved === "open") {
        open = true;
      } else {
        open = false;                /* default: collapse everything else */
      }
      if (!open) section.classList.add("collapsed");

      title.setAttribute("role", "button");
      title.setAttribute("tabindex", "0");
      title.setAttribute("aria-expanded", String(open));

      function toggle() {
        var nowCollapsed = section.classList.toggle("collapsed");
        title.setAttribute("aria-expanded", String(!nowCollapsed));
        try { localStorage.setItem(key, nowCollapsed ? "closed" : "open"); } catch (e) {}
      }
      title.addEventListener("click", toggle);
      title.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });

    /* ---------- Reading progress bar ---------- */
    var progressBar = document.getElementById("progress-bar");
    function updateProgress() {
      if (!progressBar) return;
      var scrollTop = window.scrollY;
      var docHeight = document.documentElement.scrollHeight - window.innerHeight;
      var pct = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
      progressBar.style.width = pct + "%";
    }
    window.addEventListener("scroll", updateProgress, { passive: true });
    updateProgress();

    /* ---------- Reading time estimate ---------- */
    var readingTimeEl = document.getElementById("reading-time");
    var content = document.querySelector(".content");
    if (readingTimeEl && content) {
      var words = content.innerText.trim().split(/\s+/).length;
      var minutes = Math.max(1, Math.round(words / 200));
      readingTimeEl.textContent = minutes + " min read";
    }

    /* ---------- Build + highlight table of contents ---------- */
    var tocList = document.getElementById("toc-list");
    if (tocList && content) {
      var headings = content.querySelectorAll("h2[id]");
      headings.forEach(function (h) {
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = "#" + h.id;
        a.textContent = h.textContent;
        li.appendChild(a);
        tocList.appendChild(li);
      });

      var tocLinks = tocList.querySelectorAll("a");
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            var id = entry.target.getAttribute("id");
            var link = tocList.querySelector('a[href="#' + id + '"]');
            if (!link) return;
            if (entry.isIntersecting) {
              tocLinks.forEach(function (l) { l.classList.remove("active"); });
              link.classList.add("active");
            }
          });
        },
        { rootMargin: "-20% 0px -70% 0px" }
      );
      headings.forEach(function (h) { observer.observe(h); });
    }
  });
})();
