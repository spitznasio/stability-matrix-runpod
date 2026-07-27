(function () {
  "use strict";

  // Merges the 3 RRD-style tiers into one continuous series for display.
  // The server rolls every sample into all 3 tiers concurrently (raw AND
  // tier1 AND tier2 all cover the most recent window, just at different
  // resolutions) rather than aging data out of one tier into the next, so
  // naively concatenating them plots overlapping time ranges twice — each
  // coarser tier must be truncated to only the portion older than the next
  // finer tier's earliest point before concatenating.
  function mergeTiers(seriesSnapshot) {
    var raw = seriesSnapshot.raw;
    var tier1 = seriesSnapshot.tier1;
    var tier2 = seriesSnapshot.tier2;

    var rawStart = raw.length ? raw[0][0] : Infinity;
    var tier1Cut = tier1.filter(function (p) { return p[0] < rawStart; });

    var tier1Start = tier1Cut.length ? tier1Cut[0][0] : rawStart;
    var tier2Cut = tier2.filter(function (p) { return p[0] < tier1Start; });

    return tier2Cut.concat(tier1Cut, raw);
  }

  function toUplotData(points) {
    var xs = [];
    var ys = [];
    points.forEach(function (p) {
      xs.push(p[0]);
      ys.push(p[1]);
    });
    return [xs, ys];
  }

  // Drag-to-zoom (x-axis only) + double-click-to-reset, following uPlot's
  // own documented zoom pattern (cursor.drag + a setSelect hook) rather than
  // a separate wheel-zoom plugin file.
  function initChart(containerId, label, yRange) {
    var el = document.getElementById(containerId);
    if (!el) return null;

    var chart = new uPlot(
      {
        width: el.clientWidth || 320,
        height: 160,
        cursor: { drag: { x: true, y: false } },
        series: [{}, { label: label, stroke: "#e4e4e7", width: 2 }],
        scales: { x: { time: true }, y: { range: yRange } },
        axes: [
          { stroke: "#a1a1aa", grid: { stroke: "#27272a" }, ticks: { stroke: "#27272a" } },
          { stroke: "#a1a1aa", grid: { stroke: "#27272a" }, ticks: { stroke: "#27272a" } },
        ],
        hooks: {
          setSelect: [
            function (u) {
              if (u.select.width > 5) {
                var min = u.posToVal(u.select.left, "x");
                var max = u.posToVal(u.select.left + u.select.width, "x");
                u.setScale("x", { min: min, max: max });
              }
            },
          ],
        },
      },
      [[], []],
      el
    );

    el.addEventListener("dblclick", function () {
      var xs = chart.data[0];
      if (xs.length > 1) {
        chart.setScale("x", { min: xs[0], max: xs[xs.length - 1] });
      }
    });

    return chart;
  }

  function loadSnapshot(chart, seriesSnapshot) {
    if (!chart || !seriesSnapshot) return;
    chart.setData(toUplotData(mergeTiers(seriesSnapshot)));
  }

  // Client-side point cap mirrors the server's raw-tier size order of
  // magnitude so a long-lived open tab doesn't grow memory unbounded.
  var MAX_CLIENT_POINTS = 2000;

  function appendPoint(chart, ts, value) {
    if (!chart) return;
    var xs = chart.data[0].concat([ts]);
    var ys = chart.data[1].concat([value]);
    if (xs.length > MAX_CLIENT_POINTS) {
      xs = xs.slice(-MAX_CLIENT_POINTS);
      ys = ys.slice(-MAX_CLIENT_POINTS);
    }
    chart.setData([xs, ys]);
  }

  var CHARTS = {
    cpu: null,
    mem: null,
    disk: null,
    netSend: null,
    netRecv: null,
    diskRead: null,
    diskWrite: null,
  };

  // Percentage series get a hard-fixed 0-100 range. Throughput (bps) series
  // have no natural fixed ceiling, so only the floor is pinned at 0 — this
  // still removes the main distortion (the auto-scaled minimum creeping
  // above 0 and exaggerating small fluctuations) without clipping real
  // spikes or guessing an arbitrary cap.
  var PERCENT_RANGE = [0, 100];
  var FLOOR_ZERO_RANGE = function (u, dataMin, dataMax) {
    return [0, dataMax];
  };

  function initAllCharts() {
    CHARTS.cpu = initChart("chart-cpu", "CPU %", PERCENT_RANGE);
    CHARTS.mem = initChart("chart-mem", "Mem %", PERCENT_RANGE);
    CHARTS.disk = initChart("chart-disk", "Disk %", PERCENT_RANGE);
    CHARTS.netSend = initChart("chart-net-send", "Send bps", FLOOR_ZERO_RANGE);
    CHARTS.netRecv = initChart("chart-net-recv", "Recv bps", FLOOR_ZERO_RANGE);
    CHARTS.diskRead = initChart("chart-disk-read", "Read bps", FLOOR_ZERO_RANGE);
    CHARTS.diskWrite = initChart("chart-disk-write", "Write bps", FLOOR_ZERO_RANGE);
  }

  function loadAll(snapshot) {
    var s = snapshot.series;
    loadSnapshot(CHARTS.cpu, s.cpu_percent);
    loadSnapshot(CHARTS.mem, s.mem_percent);
    loadSnapshot(CHARTS.disk, s.disk_percent);
    loadSnapshot(CHARTS.netSend, s.net_send_bps);
    loadSnapshot(CHARTS.netRecv, s.net_recv_bps);
    loadSnapshot(CHARTS.diskRead, s.disk_read_bps);
    loadSnapshot(CHARTS.diskWrite, s.disk_write_bps);
  }

  function updateAll(tick) {
    // Use the server's own clock (tick.ts) rather than the browser's, so a
    // live-appended point can't drift from the server-timestamped snapshot
    // history on a client with clock skew.
    var ts = tick.ts;
    appendPoint(CHARTS.cpu, ts, tick.system.cpu_percent);
    appendPoint(CHARTS.mem, ts, tick.system.mem_percent);
    appendPoint(CHARTS.disk, ts, tick.system.disk_percent);
    appendPoint(CHARTS.netSend, ts, tick.network.send_rate_bps);
    appendPoint(CHARTS.netRecv, ts, tick.network.recv_rate_bps);
    appendPoint(CHARTS.diskRead, ts, tick.diskio.read_rate_bps);
    appendPoint(CHARTS.diskWrite, ts, tick.diskio.write_rate_bps);
  }

  window.charts = { init: initAllCharts, loadAll: loadAll, updateAll: updateAll };
})();
