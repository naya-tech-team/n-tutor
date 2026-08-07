/**
 * interactive.js
 * Tabs, accordions, copy-code buttons, quiz reveal logic, and flashcards.
 * All components are progressively enhanced: markup works without JS,
 * this script just adds interactivity on top.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    /* ---------- Tabs ---------- */
    document.querySelectorAll(".tabs").forEach(function (tabs) {
      var buttons = tabs.querySelectorAll(".tab-btn");
      var panels = tabs.querySelectorAll(".tab-panel");
      buttons.forEach(function (btn, i) {
        btn.addEventListener("click", function () {
          buttons.forEach(function (b) { b.classList.remove("active"); });
          panels.forEach(function (p) { p.classList.remove("active"); });
          btn.classList.add("active");
          if (panels[i]) panels[i].classList.add("active");
        });
      });
    });

    /* ---------- Accordions ---------- */
    document.querySelectorAll(".accordion-header").forEach(function (header) {
      header.addEventListener("click", function () {
        var item = header.closest(".accordion-item");
        if (item) item.classList.toggle("open");
      });
    });

    /* ---------- Copy-code buttons ---------- */
    document.querySelectorAll(".copy-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var block = btn.closest(".code-block");
        var codeEl = block ? block.querySelector("pre code") : null;
        if (!codeEl) return;
        var text = codeEl.innerText;
        navigator.clipboard.writeText(text).then(function () {
          var original = btn.textContent;
          btn.textContent = "Copied!";
          btn.classList.add("copied");
          setTimeout(function () {
            btn.textContent = original;
            btn.classList.remove("copied");
          }, 1500);
        }).catch(function () {
          btn.textContent = "Press Ctrl+C";
        });
      });
    });

    /* ---------- Quiz reveal ---------- */
    document.querySelectorAll(".quiz-card").forEach(function (card) {
      var options = card.querySelectorAll(".quiz-option-btn");
      var explanation = card.querySelector(".quiz-explanation");
      var resetBtn = card.querySelector(".quiz-reset");
      var answered = false;

      options.forEach(function (opt) {
        opt.addEventListener("click", function () {
          if (answered) return;
          answered = true;
          var isCorrect = opt.getAttribute("data-correct") === "true";
          opt.classList.add(isCorrect ? "correct" : "incorrect");
          if (!isCorrect) {
            var correctOpt = card.querySelector('.quiz-option-btn[data-correct="true"]');
            if (correctOpt) correctOpt.classList.add("correct");
          }
          if (explanation) explanation.classList.add("show");
        });
      });

      if (resetBtn) {
        resetBtn.addEventListener("click", function () {
          answered = false;
          options.forEach(function (o) { o.classList.remove("correct", "incorrect"); });
          if (explanation) explanation.classList.remove("show");
        });
      }
    });

    /* ---------- Flashcards ---------- */
    document.querySelectorAll(".flashcard").forEach(function (card) {
      card.addEventListener("click", function () {
        card.classList.toggle("flipped");
      });
      card.setAttribute("tabindex", "0");
      card.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          card.classList.toggle("flipped");
        }
      });
    });

    /* ---------- Mermaid init (if library loaded) ---------- */
    if (window.mermaid) {
      var theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "default";
      window.mermaid.initialize({ startOnLoad: true, theme: theme, securityLevel: "loose" });
    }

    /* ---------- Prism init (if library loaded) ---------- */
    if (window.Prism) {
      window.Prism.highlightAll();
    }
  });
})();
