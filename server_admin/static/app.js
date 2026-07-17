(function () {
  "use strict";

  // ---- log line classification (error/warning highlighting) ----
  var ANSI_RE = /\x1b\[[0-9;]*m/g;
  var ERROR_RE = /\bERROR\b|^ERROR:|Traceback \(most recent call last\):|Exception/i;
  var WARN_RE = /\bWARN(?:ING)?\b|^WARNING:/i;

  function classifyLogLines() {
    document.querySelectorAll(".log-line").forEach(function (span) {
      if (span.dataset.classified) return;
      var text = span.textContent.replace(ANSI_RE, "");
      if (text !== span.textContent) span.textContent = text;
      if (ERROR_RE.test(text)) {
        span.classList.add("is-error");
      } else if (WARN_RE.test(text)) {
        span.classList.add("is-warn");
      }
      span.dataset.classified = "1";
    });
  }

  // ---- log level filter (All / Errors / Warnings) ----
  function initLogFilter() {
    var group = document.querySelector(".log-filter");
    var logTail = document.getElementById("log-tail");
    if (!group || !logTail) return;

    group.querySelectorAll(".log-filter__btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        group.querySelectorAll(".log-filter__btn").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        logTail.classList.remove("filter-errors", "filter-warnings");
        if (btn.dataset.filter === "errors") {
          logTail.classList.add("filter-errors");
        } else if (btn.dataset.filter === "warnings") {
          logTail.classList.add("filter-warnings");
        }
      });
    });
  }

  document.body.addEventListener("htmx:afterSwap", classifyLogLines);
  document.addEventListener("DOMContentLoaded", function () {
    classifyLogLines();
    initLogFilter();
  });
})();
