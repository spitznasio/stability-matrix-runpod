(function () {
  "use strict";

  // Merges the 3 RRD-style tiers (oldest-to-newest: tier2, tier1, raw) into
  // one continuous series for display. Resolution degrades going further
  // back in time — this is the tradeoff for bounded memory, not a bug.
  function mergeTiers(seriesSnapshot) {
    return seriesSnapshot.tier2.concat(seriesSnapshot.tier1, seriesSnapshot.raw);
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
  function initChart(containerId, label) {
    var el = document.getElementById(containerId);
    if (!el) return null;

    var chart = new uPlot(
      {
        width: el.clientWidth || 320,
        height: 160,
        cursor: { drag: { x: true, y: false } },
        series: [{}, { label: label, stroke: "#7048e8", width: 2 }],
        scales: { x: { time: true } },
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

  function initAllCharts() {
    CHARTS.cpu = initChart("chart-cpu", "CPU %");
    CHARTS.mem = initChart("chart-mem", "Mem %");
    CHARTS.disk = initChart("chart-disk", "Disk %");
    CHARTS.netSend = initChart("chart-net-send", "Send bps");
    CHARTS.netRecv = initChart("chart-net-recv", "Recv bps");
    CHARTS.diskRead = initChart("chart-disk-read", "Read bps");
    CHARTS.diskWrite = initChart("chart-disk-write", "Write bps");
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
    var now = Date.now() / 1000;
    appendPoint(CHARTS.cpu, now, tick.system.cpu_percent);
    appendPoint(CHARTS.mem, now, tick.system.mem_percent);
    appendPoint(CHARTS.disk, now, tick.system.disk_percent);
    appendPoint(CHARTS.netSend, now, tick.network.send_rate_bps);
    appendPoint(CHARTS.netRecv, now, tick.network.recv_rate_bps);
    appendPoint(CHARTS.diskRead, now, tick.diskio.read_rate_bps);
    appendPoint(CHARTS.diskWrite, now, tick.diskio.write_rate_bps);
  }

  window.charts = { init: initAllCharts, loadAll: loadAll, updateAll: updateAll };
})();
