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

  // ---- installed page: client-side sort + filter over server-rendered rows ----
  function initInstalledTable() {
    var root = document.getElementById("installed-root");
    if (!root) return;

    var filterInput = document.getElementById("installed-filter");
    var typeSelect = document.getElementById("installed-type-filter");
    var countEl = document.getElementById("installed-count");
    var tableBody = document.getElementById("installed-body");
    var gridEl = document.getElementById("installed-grid");
    var sortButtons = root.querySelectorAll(".sort-btn");

    // Rows are read from data-* attributes and rebuilt with createElement/
    // textContent (never innerHTML) — a <tr> can't legally live inside a plain
    // wrapper <div> for cloning anyway, since the HTML parser would foster-parent
    // it out of the table context.
    function el(tag, className, text) {
      var e = document.createElement(tag);
      if (className) e.className = className;
      if (text !== undefined) e.textContent = text;
      return e;
    }

    function buildRowNode(r) {
      var tr = document.createElement("tr");
      var tdName = el("td", "installed-table__name", r.name);
      var tdType = document.createElement("td");
      tdType.appendChild(el("span", "installed-table__type", r.type));
      var tdBase = el("td", "installed-table__base", r.base);
      var tdPath = el("td", "installed-table__path", r.path);
      tr.append(tdName, tdType, tdBase, tdPath);
      return tr;
    }

    function buildCardNode(r) {
      var card = el("div", "installed-card");
      var swatch = el("div", "installed-card__swatch");
      swatch.style.background = swatchGradient(r.name);
      var body = el("div", "installed-card__body");
      var name = el("div", "installed-card__name", r.name);
      var row = el("div", "installed-card__row");
      row.appendChild(el("span", "installed-table__type", r.type));
      row.appendChild(el("span", "installed-card__base", r.base));
      var path = el("div", "installed-card__path", r.path);
      path.title = r.path;
      body.append(name, row, path);
      card.append(swatch, body);
      return card;
    }

    function swatchGradient(seedStr) {
      var h = 0;
      for (var i = 0; i < seedStr.length; i++) h = (h * 31 + seedStr.charCodeAt(i)) | 0;
      h = Math.abs(h) % 360;
      var h2 = (h + 35) % 360;
      return "linear-gradient(135deg, hsl(" + h + ",22%,20%), hsl(" + h2 + ",18%,12%))";
    }

    var rows = Array.prototype.map.call(root.querySelectorAll("[data-installed-row]"), function (rowEl) {
      return {
        name: rowEl.dataset.name,
        type: rowEl.dataset.type,
        base: rowEl.dataset.base,
        path: rowEl.dataset.path,
      };
    });
    var total = rows.length;
    var sort = { key: "name", dir: 1 };

    function emptyRow() {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.className = "installed-empty";
      td.colSpan = 4;
      td.textContent = "No installed models match this filter.";
      tr.appendChild(td);
      return tr;
    }

    function emptyCard() {
      var p = document.createElement("p");
      p.className = "installed-empty";
      p.style.gridColumn = "1/-1";
      p.textContent = "No installed models match this filter.";
      return p;
    }

    function render() {
      var filterText = filterInput.value.toLowerCase();
      var typeFilter = typeSelect.value;
      var visible = rows.filter(function (r) {
        var matchesText = !filterText || r.name.toLowerCase().indexOf(filterText) !== -1 || r.path.toLowerCase().indexOf(filterText) !== -1;
        var matchesType = !typeFilter || r.type === typeFilter;
        return matchesText && matchesType;
      });
      visible.sort(function (a, b) { return a[sort.key].localeCompare(b[sort.key]) * sort.dir; });

      if (!visible.length) {
        tableBody.replaceChildren(emptyRow());
        gridEl.replaceChildren(emptyCard());
      } else {
        tableBody.replaceChildren.apply(tableBody, visible.map(buildRowNode));
        gridEl.replaceChildren.apply(gridEl, visible.map(buildCardNode));
      }
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
