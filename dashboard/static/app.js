/* Polls /api/logs every 2 s and updates the grids and charts in place (no page reloads).
   Audit data can contain untrusted text (product names, reasons, image URLs), so everything is written with
   textContent / DOM nodes, never innerHTML, and images are only loaded from https URLs. */
(function () {
  "use strict";
  var L = window.AGLogic;
  var POLL_MS = 2000;
  var VERDICT_TEXT = {
    paid: "Paid",
    allow: "Allowed",
    step_up: "Needs approval",
    deny: "Blocked",
    approved: "Approved",
    denied: "Denied",
    expired: "Expired",
    error: "Error",
  };
  var RISK_TEXT = {
    ok: "Normal",
    warn: "Approvals required",
    block: "Read-only",
  };
  var CHART_IDS = {
    risk: "chart-risk",
    mix: "chart-mix",
    spend: "chart-spend",
  };

  var $ = function (id) {
    return document.getElementById(id);
  };
  var state = {
    file: "auto",
    activeFile: null,
    after: -1,
    events: [],
    rows: [],
    fresh: {},
    productKey: null,
    filter: "all",
    follow: true,
    paused: false,
    inflight: false,
    tab: "activity",
  };
  var grid,
    pgrid,
    Charts,
    enterprise = false,
    charts = {},
    chartKey = {};

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }
  function clock(ms) {
    var d = new Date(ms);
    return (
      pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
    );
  }
  function money(n) {
    return n == null
      ? ""
      : n.toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function isDark() {
    return !!(
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
    );
  }
  function cssVar(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
  }

  /* ---------- boot: load the grid (Enterprise if the user installed it locally), then the charts ---------- */
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () {
        reject(new Error("Could not load " + src));
      };
      document.head.appendChild(s);
    });
  }
  function boot() {
    var cfg = { enterprise: false, license: "" };
    fetch("/api/config", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .catch(function () {
        return cfg;
      })
      .then(function (c) {
        cfg = c;
        return cfg.enterprise
          ? loadScript(
              "vendor-enterprise/ag-grid-enterprise.min.noStyle.js",
            ).then(
              function () {
                return true;
              },
              function () {
                return false;
              },
            )
          : false;
      })
      .then(function (ent) {
        return ent
          ? true
          : loadScript("vendor/ag-grid-community.min.noStyle.js").then(
              function () {
                return false;
              },
            );
      })
      .then(function (ent) {
        enterprise = ent;
        if (ent && cfg.license && window.agGrid.LicenseManager)
          window.agGrid.LicenseManager.setLicenseKey(cfg.license);
        return loadScript("vendor/ag-charts-community.min.js").then(
          function () {
            Charts = window.agCharts;
          },
          function () {
            Charts = null;
          },
        );
      })
      .then(function () {
        init(cfg);
      })
      .catch(function (err) {
        setLive(false, err.message);
      });
  }

  /* ---------- grids ---------- */
  function thumb(p) {
    var box = el("div", "thumb");
    var none = function () {
      box.className = "thumb none";
      box.textContent = "No image";
    };
    if (!p.value) {
      none();
      return box;
    }
    var img = document.createElement("img");
    img.alt = p.data.name || "";
    img.loading = "lazy";
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    img.onerror = none;
    img.src = p.value;
    box.appendChild(img);
    return box;
  }
  function eventColumns() {
    var cols = [
      { field: "seq", headerName: "#", width: 70, cellClass: "mono" },
      {
        field: "time",
        headerName: "Time",
        width: 100,
        valueFormatter: function (p) {
          return p.value == null ? "" : clock(p.value);
        },
      },
      { field: "label", headerName: "What happened", width: 160 },
      {
        field: "verdict",
        headerName: "Result",
        width: 140,
        cellRenderer: function (p) {
          if (!p.value) return el("span", "dash", "\u2013");
          return el(
            "span",
            "pill " + p.value,
            VERDICT_TEXT[p.value] || p.value,
          );
        },
      },
      {
        field: "summary",
        headerName: "Details",
        flex: 1,
        minWidth: 260,
        tooltipField: "summary",
      },
      {
        field: "total",
        headerName: "Amount",
        width: 110,
        type: "rightAligned",
        valueFormatter: function (p) {
          return money(p.value);
        },
      },
      {
        field: "risk",
        headerName: "Risk",
        width: 90,
        type: "rightAligned",
        cellRenderer: function (p) {
          if (p.value == null) return el("span", "dash", "\u2013");
          return el("span", "num-" + L.riskLevel(p.value), p.value.toFixed(2));
        },
      },
    ];
    if (enterprise) {
      cols[2].filter = "agSetColumnFilter";
      cols[2].enableRowGroup = true;
      cols[3].filter = "agSetColumnFilter";
      cols[3].enableRowGroup = true;
      cols[5].aggFunc = "sum";
    }
    return cols;
  }
  function productColumns() {
    return [
      {
        field: "image",
        headerName: "Image",
        width: 90,
        sortable: false,
        cellRenderer: thumb,
      },
      {
        field: "name",
        headerName: "Product",
        flex: 1,
        minWidth: 240,
        tooltipField: "name",
      },
      {
        field: "merchant",
        headerName: "Merchant",
        width: 220,
        cellRenderer: function (p) {
          var box = el("span", null, p.value);
          if (p.data.allowed === false)
            box.appendChild(el("span", "pill warn", "Not in mandate"));
          return box;
        },
      },
      {
        field: "price",
        headerName: "Price",
        width: 110,
        type: "rightAligned",
        valueFormatter: function (p) {
          return money(p.value);
        },
      },
      { field: "query", headerName: "Found by search", width: 160 },
      {
        field: "item_id",
        headerName: "Item id",
        width: 190,
        cellClass: "mono",
      },
    ];
  }
  function enterpriseOptions() {
    if (!enterprise) return {};
    return {
      sideBar: true,
      cellSelection: true,
      enableCharts: true,
      rowGroupPanelShow: "always",
      statusBar: {
        statusPanels: [
          { statusPanel: "agTotalAndFilteredRowCountComponent", align: "left" },
          { statusPanel: "agSelectedRowCountComponent" },
          { statusPanel: "agAggregationComponent" },
        ],
      },
    };
  }
  function createGrids() {
    var common = {
      defaultColDef: { sortable: true, resizable: true, suppressMovable: true },
      suppressCellFocus: true,
      animateRows: false,
      tooltipShowDelay: 400,
    };
    grid = agGrid.createGrid(
      $("grid"),
      Object.assign({}, common, enterpriseOptions(), {
        columnDefs: eventColumns(),
        rowData: [],
        getRowId: function (p) {
          return String(p.data.seq);
        },
        getRowClass: function (p) {
          var c = ["row-" + (p.data.verdict || "none")];
          if (state.fresh[p.data.seq]) c.push("row-fresh");
          return c;
        },
        rowSelection: {
          mode: "singleRow",
          checkboxes: false,
          enableClickSelection: true,
        },
        isExternalFilterPresent: function () {
          return state.filter !== "all";
        },
        doesExternalFilterPass: function (node) {
          return L.matchesFilter(node.data, state.filter);
        },
        onRowClicked: function (e) {
          if (e.data) openDetail(e.data.seq);
        },
        overlayNoRowsTemplate:
          '<span class="empty">No events yet. Start an agent run (python -m scripts.run_agent) and its actions appear here within two seconds.</span>',
      }),
    );
    pgrid = agGrid.createGrid(
      $("products-grid"),
      Object.assign({}, common, {
        columnDefs: productColumns(),
        rowData: [],
        rowHeight: 64,
        getRowId: function (p) {
          return p.data.item_id;
        },
        overlayNoRowsTemplate:
          '<span class="empty">No products yet. Items the agent finds appear here, with their pictures.</span>',
      }),
    );
  }
  function applyTheme() {
    var cls = isDark() ? "ag-theme-quartz-dark" : "ag-theme-quartz";
    $("grid").className = cls;
    $("products-grid").className = cls;
  }

  /* ---------- summary, products ---------- */
  function renderSummary() {
    var s = L.summarize(state.events);
    $("k-paid").textContent = money(s.paid);
    $("k-blocked").textContent = String(s.blocked);
    $("k-waiting").textContent = String(s.waiting);
    $("k-events").textContent = String(s.count);
    $("k-merchants").textContent = s.merchants.length
      ? "Allowed: " + s.merchants.join(", ")
      : "";
    if (s.limit) {
      $("k-limit").textContent =
        "of " + money(s.limit) + " " + s.currency + " limit";
      $("k-bar").hidden = false;
      $("k-bar-fill").style.width =
        Math.min(100, (s.paid / s.limit) * 100) + "%";
    } else {
      $("k-limit").textContent = "No limit recorded";
      $("k-bar").hidden = true;
    }
    $("risk-level").className = "level " + s.level;
    $("risk-level").textContent = RISK_TEXT[s.level];
    $("risk-value").textContent = s.riskNow.toFixed(2);
    $("marker").style.left =
      (Math.min(s.riskNow, L.SCALE) / L.SCALE) * 100 + "%";
    $("gate").setAttribute(
      "aria-label",
      "Risk " + s.riskNow.toFixed(2) + " of " + L.SCALE,
    );
    ["all", "paid", "waiting", "blocked", "errors"].forEach(function (f) {
      $("c-" + f).textContent = String(
        state.rows.filter(function (r) {
          return L.matchesFilter(r, f);
        }).length,
      );
    });
    renderProducts();
    renderCharts();
  }
  function renderProducts() {
    var list = L.products(state.events);
    $("c-products").textContent = String(list.length);
    var key = list
      .map(function (p) {
        return p.item_id + (p.allowed === false ? "!" : "");
      })
      .join("|");
    if (key === state.productKey) return;
    state.productKey = key;
    pgrid.setGridOption("rowData", list);
  }

  /* ---------- charts (AG Charts) ---------- */
  function chartOptions(kind, data) {
    var c = {
      accent: cssVar("--accent"),
      paid: cssVar("--paid"),
      wait: cssVar("--wait"),
      block: cssVar("--block"),
    };
    var base = {
      theme: isDark() ? "ag-default-dark" : "ag-default",
      background: { visible: false },
    };
    var line = function (key, name, color, dashed) {
      return {
        type: "line",
        xKey: "n",
        yKey: key,
        yName: name,
        stroke: color,
        strokeWidth: dashed ? 1.5 : 2,
        lineDash: dashed ? [5, 4] : undefined,
        marker: { enabled: !dashed, size: 5, fill: color, stroke: color },
      };
    };
    if (kind === "risk") {
      return Object.assign(
        {
          data: data.risk,
          series: [
            line("risk", "Risk", c.accent, false),
            line("warn", "Approvals required", c.wait, true),
            line("block", "Read-only", c.block, true),
          ],
          axes: {
            x: {
              type: "number",
              position: "bottom",
              title: { text: "Event number" },
            },
            y: {
              type: "number",
              position: "left",
              min: 0,
              max: L.SCALE,
              title: { text: "Risk" },
            },
          },
        },
        base,
      );
    }
    if (kind === "mix") {
      var bar = function (key, name, color) {
        return {
          type: "bar",
          xKey: "label",
          yKey: key,
          yName: name,
          fill: color,
          stroke: color,
        };
      };
      return Object.assign(
        {
          data: data.mix,
          series: [
            bar("allowed", "Allowed", c.paid),
            bar("waiting", "Needed approval", c.wait),
            bar("blocked", "Blocked", c.block),
          ],
          axes: {
            x: { type: "category", position: "bottom" },
            y: {
              type: "number",
              position: "left",
              min: 0,
              title: { text: "Decisions" },
              label: {
                formatter: function (p) {
                  return Math.floor(p.value) === p.value ? String(p.value) : "";
                },
              },
            },
          },
        },
        base,
      );
    }
    var series = [
      Object.assign(line("paid", "Paid out", c.paid, false), {
        interpolation: { type: "step" },
      }),
    ];
    if (data.hasLimit) series.push(line("limit", "Limit", c.block, true));
    return Object.assign(
      {
        data: data.spend,
        series: series,
        axes: {
          x: {
            type: "number",
            position: "bottom",
            title: { text: "Event number" },
          },
          y: {
            type: "number",
            position: "left",
            min: 0,
            title: { text: "Paid out" },
          },
        },
      },
      base,
    );
  }
  function renderCharts() {
    if (state.tab !== "charts") return;
    var note = $("chart-note");
    if (!Charts) {
      note.hidden = false;
      note.textContent = "The charts library could not be loaded.";
      return;
    }
    var data = L.chartData(state.events);
    Object.keys(CHART_IDS).forEach(function (k) {
      var key = JSON.stringify(data[k]) + (isDark() ? "d" : "l");
      if (chartKey[k] === key) return;
      chartKey[k] = key;
      try {
        var opts = chartOptions(k, data);
        if (charts[k])
          charts[k].update(Object.assign({ container: $(CHART_IDS[k]) }, opts));
        else
          charts[k] = Charts.AgCharts.create(
            Object.assign({ container: $(CHART_IDS[k]) }, opts),
          );
      } catch (err) {
        console.error(err);
        note.hidden = false;
        note.textContent = "A chart could not be drawn: " + err.message;
      }
    });
  }
  function resetCharts() {
    Object.keys(charts).forEach(function (k) {
      try {
        charts[k].destroy();
      } catch (e) {
        /* already gone */
      }
      charts[k] = null;
      chartKey[k] = null;
    });
    renderCharts();
  }

  /* ---------- detail drawer, tabs ---------- */
  function openDetail(seq) {
    var ev = state.events[seq];
    if (!ev) return;
    $("detail-title").textContent = L.toRow(ev).label;
    $("detail-json").textContent = JSON.stringify(
      {
        seq: ev.seq,
        time: ev.ts == null ? null : new Date(ev.ts * 1000).toISOString(),
        event: ev.event,
        hash: ev.hash,
        data: ev.data,
      },
      null,
      2,
    );
    $("detail").hidden = false;
    document.body.classList.add("detail-open");
  }
  function closeDetail() {
    $("detail").hidden = true;
    document.body.classList.remove("detail-open");
  }
  function showTab(name) {
    state.tab = name;
    ["activity", "products", "charts"].forEach(function (t) {
      $("tab-" + t).setAttribute(
        "aria-selected",
        t === name ? "true" : "false",
      );
      $("panel-" + t).hidden = t !== name;
    });
    if (name === "charts") renderCharts();
  }

  /* ---------- polling ---------- */
  function resetFor(file) {
    state.activeFile = file;
    state.after = -1;
    state.events = [];
    state.rows = [];
    state.fresh = {};
    state.productKey = null;
    grid.setGridOption("rowData", []);
    pgrid.setGridOption("rowData", []);
    closeDetail();
  }
  function ingest(events) {
    var firstLoad = state.rows.length === 0;
    var rows = events.map(L.toRow);
    events.forEach(function (e) {
      state.events[e.seq] = e;
    });
    state.rows = state.rows.concat(rows);
    state.after = events[events.length - 1].seq;
    grid.applyTransaction({ add: rows.slice().reverse(), addIndex: 0 });
    if (!firstLoad) {
      rows.forEach(function (r) {
        state.fresh[r.seq] = true;
      });
      setTimeout(function () {
        rows.forEach(function (r) {
          delete state.fresh[r.seq];
        });
        grid.redrawRows();
      }, 1800);
    }
    if (state.follow) grid.ensureIndexVisible(0);
  }
  function setLive(ok, text) {
    $("live").className = "live " + (state.paused ? "" : ok ? "on" : "off");
    $("live-text").textContent = text;
  }
  function renderSessions(files, active) {
    var sel = $("session");
    var have = Array.prototype.map
      .call(sel.options, function (o) {
        return o.value;
      })
      .slice(1)
      .join("|");
    if (have !== files.join("|")) {
      while (sel.options.length > 1) sel.remove(1);
      files.forEach(function (f) {
        var o = document.createElement("option");
        o.value = f;
        o.textContent = f;
        sel.appendChild(o);
      });
    }
    sel.value = state.file;
    sel.options[0].textContent = active ? "Newest session" : "No sessions yet";
  }
  function setChain(ok) {
    var b = $("chain");
    b.className =
      "badge " + (ok === true ? "ok" : ok === false ? "bad" : "neutral");
    b.textContent =
      ok === true
        ? "Log verified"
        : ok === false
          ? "Log was altered"
          : "Checking log";
  }
  function poll() {
    if (state.paused || state.inflight) return;
    state.inflight = true;
    var q = new URLSearchParams({
      file: state.file,
      for: state.activeFile || "",
      after: String(state.after),
    });
    fetch("/api/logs?" + q.toString(), { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(
        function (d) {
          try {
            if (d.file !== state.activeFile) resetFor(d.file);
            if (d.events.length) ingest(d.events);
            renderSessions(d.files, d.file);
            setChain(d.chain_ok);
            renderSummary();
            setLive(true, "Live, updated " + clock(Date.now()));
          } catch (err) {
            console.error(err);
            setLive(false, "Display error: " + err.message);
          }
        },
        function () {
          setLive(false, "Cannot reach the dashboard server");
        },
      )
      .then(function () {
        state.inflight = false;
      });
  }

  /* ---------- wiring ---------- */
  function init(cfg) {
    $("edition").textContent = enterprise
      ? cfg.license
        ? "AG Grid Enterprise"
        : "AG Grid Enterprise, no license key set"
      : "AG Grid Community";
    applyTheme();
    createGrids();
    if (window.matchMedia)
      window
        .matchMedia("(prefers-color-scheme: dark)")
        .addEventListener("change", function () {
          applyTheme();
          resetCharts();
        });
    ["activity", "products", "charts"].forEach(function (t) {
      $("tab-" + t).addEventListener("click", function () {
        showTab(t);
      });
    });
    Array.prototype.forEach.call(
      document.querySelectorAll(".chip"),
      function (chip) {
        chip.addEventListener("click", function () {
          state.filter = chip.getAttribute("data-filter");
          Array.prototype.forEach.call(
            document.querySelectorAll(".chip"),
            function (c) {
              c.setAttribute("aria-pressed", c === chip ? "true" : "false");
            },
          );
          grid.onFilterChanged();
        });
      },
    );
    $("search").addEventListener("input", function (e) {
      grid.setGridOption("quickFilterText", e.target.value);
    });
    $("follow").addEventListener("change", function (e) {
      state.follow = e.target.checked;
    });
    $("session").addEventListener("change", function (e) {
      state.file = e.target.value;
      poll();
    });
    $("detail-close").addEventListener("click", closeDetail);
    $("pause").addEventListener("click", function () {
      state.paused = !state.paused;
      $("pause").textContent = state.paused ? "Resume" : "Pause";
      setLive(true, state.paused ? "Paused" : "Resuming");
      if (!state.paused) poll();
    });
    poll();
    setInterval(poll, POLL_MS);
  }
  boot();
})();
