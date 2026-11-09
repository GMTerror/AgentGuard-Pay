/* Pure functions: audit events -> grid rows and summary numbers. No DOM, so it is unit-tested with node. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.AGLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var WARN = 1.0,
    BLOCK = 2.5,
    SCALE = 3.5;
  var ACTION = {
    create_order: "Order",
    capture: "Capture",
    charge_saved: "Charge",
  };

  function riskLevel(r) {
    return r >= BLOCK ? "block" : r >= WARN ? "warn" : "ok";
  }
  function num(v) {
    var n = parseFloat(v);
    return isFinite(n) ? n : null;
  }
  function short(v, n) {
    var s = typeof v === "string" ? v : JSON.stringify(v);
    if (s === undefined) s = "";
    return s.length > n ? s.slice(0, n - 1) + "\u2026" : s;
  }
  function id8(v) {
    return String(v || "").slice(0, 8);
  }
  function itemsText(items) {
    if (!Array.isArray(items)) return "";
    return items
      .map(function (i) {
        return Array.isArray(i) ? i[0] + " x" + i[1] : String(i);
      })
      .join(", ");
  }

  function toRow(ev) {
    var d = ev.data || {};
    var row = {
      seq: ev.seq,
      time: ev.ts == null ? null : ev.ts * 1000,
      event: ev.event,
      label: String(ev.event),
      verdict: "",
      summary: "",
      total: num(d.total),
      risk: typeof d.risk === "number" ? d.risk : null,
    };
    switch (ev.event) {
      case "session_start":
        row.label = "Session opened";
        row.summary =
          "Mandate: up to " +
          d.limit +
          " " +
          (d.currency || "") +
          " at " +
          (d.merchants || []).join(", ") +
          (d.auto ? " (auto-pay)" : "");
        break;
      case "user_message":
        row.label = "You asked";
        row.summary = short(d.text, 200);
        break;
      case "search_results":
        row.label = "Products found";
        row.summary =
          (Array.isArray(d.items) ? d.items.length : 0) +
          ' offers for "' +
          short(d.query, 60) +
          '"';
        break;
      case "tool_call":
        row.label = "Agent called";
        row.summary = String(d.tool) + "(" + short(d.args, 90) + ")";
        break;
      case "decision":
        row.label = "Gate decision";
        row.verdict = d.verdict || "";
        row.summary =
          (ACTION[d.action] || d.action) +
          ": " +
          d.reason +
          (d.items ? " (" + itemsText(d.items) + ")" : "") +
          (d.semantic_drift > 0 ? ", intent drift " + d.semantic_drift : "");
        break;
      case "step_up_requested":
        row.label = "Approval needed";
        row.verdict = "step_up";
        row.summary =
          "Waiting for you to approve (request " + id8(d.request_id) + ")";
        break;
      case "step_up_resolved":
        row.label = "Approval answered";
        row.verdict = d.status || "";
        row.summary = "Request " + id8(d.request_id) + ": " + d.status;
        break;
      case "captured":
        row.label = "Payment made";
        row.verdict = "paid";
        row.summary =
          "Order " +
          d.order_id +
          ", capture " +
          d.capture_id +
          (d.auto ? " (auto-pay)" : "");
        break;
      case "paypal_error":
        row.label = "Provider error";
        row.verdict = "error";
        row.summary = d.action + " failed: " + short(d.detail || d.status, 160);
        break;
      case "catalog_error":
        row.label = "Provider error";
        row.verdict = "error";
        row.summary =
          "Catalog search failed: " + short(d.detail || d.status, 160);
        break;
      case "charge_mismatch":
        row.label = "Charge mismatch";
        row.verdict = "error";
        row.summary = "Expected " + d.expected + ", charged " + d.got;
        break;
      case "mandate_accepted":
        row.label = "Mandate check";
        row.verdict = "allow";
        row.summary = short(d.attempt, 120);
        break;
      case "mandate_rejected":
        row.label = "Mandate check";
        row.verdict = "deny";
        row.summary = short(d.attempt, 80) + ": " + d.reason;
        break;
      default:
        row.summary = short(d, 120);
    }
    return row;
  }

  function matchesFilter(row, f) {
    switch (f) {
      case "paid":
        return row.verdict === "paid";
      case "waiting":
        return row.verdict === "step_up";
      case "blocked":
        return row.verdict === "deny" || row.verdict === "denied";
      case "errors":
        return row.verdict === "error";
      default:
        return true;
    }
  }

  function summarize(events) {
    var s = {
      paid: 0,
      paidCount: 0,
      blocked: 0,
      waiting: 0,
      count: events.length,
      mix: { allow: 0, step_up: 0, deny: 0 },
      spark: [],
      riskNow: 0,
      level: "ok",
      limit: null,
      currency: "",
      merchants: [],
    };
    var requested = {},
      resolved = {};
    events.forEach(function (ev) {
      var d = ev.data || {};
      if (ev.event === "session_start") {
        s.limit = num(d.limit);
        s.currency = d.currency || "";
        s.merchants = d.merchants || [];
      } else if (ev.event === "captured") {
        s.paid += num(d.total) || 0;
        s.paidCount += 1;
      } else if (ev.event === "decision") {
        if (d.verdict in s.mix) s.mix[d.verdict] += 1;
        if (d.verdict === "deny") s.blocked += 1;
      } else if (ev.event === "step_up_requested") {
        requested[d.request_id] = true;
      } else if (ev.event === "step_up_resolved") {
        resolved[d.request_id] = true;
      }
      if (typeof d.risk === "number") {
        s.spark.push(d.risk);
        s.riskNow = d.risk;
      }
    });
    s.waiting = Object.keys(requested).filter(function (k) {
      return !resolved[k];
    }).length;
    s.level = riskLevel(s.riskNow);
    return s;
  }

  function safeUrl(u) {
    return typeof u === "string" && u.indexOf("https://") === 0 ? u : null;
  }

  function products(events) {
    var merchants = null,
      seen = {},
      list = [];
    events.forEach(function (ev) {
      var d = ev.data || {};
      if (ev.event === "session_start") merchants = d.merchants || null;
      if (ev.event === "search_results" && Array.isArray(d.items)) {
        d.items.forEach(function (it) {
          if (!it || seen[it.item_id]) return;
          seen[it.item_id] = true;
          list.push({
            item_id: it.item_id,
            name: it.name || "",
            merchant: it.merchant || "",
            price: num(it.price),
            image: safeUrl(it.image),
            query: d.query || "",
            seq: ev.seq,
            allowed: null,
          });
        });
      }
    });
    list.forEach(function (p) {
      p.allowed = merchants ? merchants.indexOf(p.merchant) >= 0 : null;
    });
    return list.reverse();
  }

  function chartData(events) {
    var risk = [],
      mix = { allowed: 0, waiting: 0, blocked: 0 },
      spend = [],
      paid = 0,
      limit = null;
    events.forEach(function (ev) {
      var d = ev.data || {};
      if (ev.event === "session_start") limit = num(d.limit);
      if (typeof d.risk === "number")
        risk.push({ n: ev.seq, risk: d.risk, warn: WARN, block: BLOCK });
      if (ev.event === "decision") {
        if (d.verdict === "allow") mix.allowed += 1;
        else if (d.verdict === "step_up") mix.waiting += 1;
        else if (d.verdict === "deny") mix.blocked += 1;
      }
      if (ev.event === "captured") {
        paid += num(d.total) || 0;
        spend.push({
          n: ev.seq,
          paid: Math.round(paid * 100) / 100,
          limit: limit,
        });
      }
    });
    return {
      risk: risk,
      mix: [
        {
          label: "Gate decisions",
          allowed: mix.allowed,
          waiting: mix.waiting,
          blocked: mix.blocked,
        },
      ],
      spend: spend,
      hasLimit: limit != null,
    };
  }

  return {
    safeUrl: safeUrl,
    products: products,
    chartData: chartData,
    WARN: WARN,
    BLOCK: BLOCK,
    SCALE: SCALE,
    riskLevel: riskLevel,
    toRow: toRow,
    matchesFilter: matchesFilter,
    summarize: summarize,
  };
});
