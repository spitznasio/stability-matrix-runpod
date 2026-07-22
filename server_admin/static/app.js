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

  // ---- toast (used by the dashboard's "restart to free VRAM" button,
  // which posts with hx-swap="none" so there's no swapped content to show
  // feedback in) ----
  function showToast(message) {
    var container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    var toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function () {
      toast.classList.add("is-leaving");
      toast.addEventListener("transitionend", function () { toast.remove(); });
    }, 2500);
  }

  function initServiceRestartButtons() {
    document.body.addEventListener("htmx:afterRequest", function (evt) {
      var btn = evt.detail.elt;
      if (!btn.classList) return;
      if (!btn.classList.contains("gpu-process-restart-btn") && !btn.classList.contains("env-restart-btn")) return;
      var name = btn.dataset.serviceName || "service";
      showToast(evt.detail.successful ? "Restarting " + name + "…" : "Failed to restart " + name);
    });
  }

  document.body.addEventListener("htmx:afterSwap", classifyLogLines);
  document.addEventListener("DOMContentLoaded", function () {
    classifyLogLines();
    initLogFilter();
    initServiceRestartButtons();
  });
})();
