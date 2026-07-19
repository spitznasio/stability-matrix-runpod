(function () {
  "use strict";

  // ---- view toggle (cards / table), shared by Browse and Installed ----
  // Any element with [data-view-toggle] + data-key/data-grid/data-table
  // controls visibility of two sibling result containers, persisted in
  // localStorage so the choice survives htmx-swapped results and page loads.
  function applyViewToggle(toggle) {
    var key = toggle.dataset.key;
    var saved = localStorage.getItem(key) || "grid";
    var gridEl = document.getElementById(toggle.dataset.grid);
    var tableEl = document.getElementById(toggle.dataset.table);
    if (!gridEl || !tableEl) return;
    gridEl.style.display = saved === "grid" ? "" : "none";
    tableEl.style.display = saved === "table" ? "" : "none";
    toggle.querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("is-active", b.dataset.view === saved);
    });
  }

  function initViewToggles() {
    document.querySelectorAll("[data-view-toggle]").forEach(function (toggle) {
      applyViewToggle(toggle);
      toggle.querySelectorAll("button").forEach(function (btn) {
        btn.addEventListener("click", function () {
          localStorage.setItem(toggle.dataset.key, btn.dataset.view);
          applyViewToggle(toggle);
        });
      });
    });
  }

  // ---- installed page: client-side sort + filter over server-rendered cards/rows ----
  function initInstalledTable() {
    var root = document.getElementById("installed-root");
    if (!root) return;

    var filterInput = document.getElementById("installed-filter");
    var typeSelect = document.getElementById("installed-type-filter");
    var countEl = document.getElementById("installed-count");
    var tableBody = document.getElementById("installed-body");
    var gridEl = document.getElementById("installed-grid");
    var sortButtons = root.querySelectorAll(".sort-btn");
    var emptyCard = document.getElementById("installed-empty-grid");
    var emptyRow = document.getElementById("installed-empty-row");

    var cardEls = Array.prototype.filter.call(
      gridEl.querySelectorAll("[data-installed-row]"),
      function (el) { return el !== emptyCard; }
    );
    var rowEls = Array.prototype.filter.call(
      tableBody.querySelectorAll("[data-installed-row]"),
      function (el) { return el !== emptyRow; }
    );

    var rows = cardEls.map(function (cardEl, i) {
      return {
        card: cardEl,
        row: rowEls[i],
        name: cardEl.dataset.name,
        type: cardEl.dataset.type,
        base: cardEl.dataset.base,
        path: cardEl.dataset.path,
      };
    });
    var total = rows.length;
    var sort = { key: "name", dir: 1 };

    function render() {
      var filterText = filterInput.value.toLowerCase();
      var typeFilter = typeSelect.value;
      var visible = rows.filter(function (r) {
        var matchesText = !filterText || r.name.toLowerCase().indexOf(filterText) !== -1 || r.path.toLowerCase().indexOf(filterText) !== -1;
        var matchesType = !typeFilter || r.type === typeFilter;
        return matchesText && matchesType;
      });
      visible.sort(function (a, b) { return a[sort.key].localeCompare(b[sort.key]) * sort.dir; });

      rows.forEach(function (r) {
        r.card.hidden = true;
        r.row.hidden = true;
      });
      visible.forEach(function (r) {
        r.card.hidden = false;
        r.row.hidden = false;
        gridEl.appendChild(r.card);
        tableBody.appendChild(r.row);
      });
      gridEl.appendChild(emptyCard);
      tableBody.appendChild(emptyRow);
      emptyCard.hidden = visible.length !== 0;
      emptyRow.hidden = visible.length !== 0;

      countEl.textContent = visible.length + " of " + total + " installed";

      sortButtons.forEach(function (btn) {
        var isActive = btn.dataset.sortKey === sort.key;
        btn.classList.toggle("is-active", isActive);
        var svg = btn.querySelector("svg");
        if (svg) svg.classList.toggle("is-desc", isActive && sort.dir === -1);
      });
    }

    filterInput.addEventListener("input", render);
    typeSelect.addEventListener("change", render);
    sortButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.dataset.sortKey;
        if (sort.key === key) sort.dir *= -1;
        else { sort.key = key; sort.dir = 1; }
        render();
      });
    });

    render();
  }

  function init() {
    initViewToggles();
    initInstalledTable();
  }

  document.addEventListener("DOMContentLoaded", init);
  // Browse results (#results) are swapped wholesale by htmx on search/pagination —
  // the fresh nodes need their own toggle wired up again.
  document.body.addEventListener("htmx:afterSwap", init);

  // Browse table rows navigate via a plain URL read from data-row-href —
  // delegated once, not per-row, so freshly-swapped rows work with no re-init.
  document.addEventListener("click", function (e) {
    var row = e.target.closest("[data-row-href]");
    if (row) window.location = row.dataset.rowHref;
  });
})();
