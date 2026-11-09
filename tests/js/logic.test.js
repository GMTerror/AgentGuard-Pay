const test = require("node:test");
const assert = require("node:assert");
const L = require("../../dashboard/static/logic.js");

const ev = (seq, event, data, ts = 100) => ({ seq, ts, event, data });

test("risk levels follow the gateway thresholds", () => {
  assert.equal(L.riskLevel(0.99), "ok");
  assert.equal(L.riskLevel(1.0), "warn");
  assert.equal(L.riskLevel(2.49), "warn");
  assert.equal(L.riskLevel(2.5), "block");
});

test("rows are readable for every event type", () => {
  const dec = L.toRow(
    ev(1, "decision", {
      action: "create_order",
      verdict: "deny",
      reason: "exceeds mandate limit",
      total: "140.00",
      risk: 1.5,
      items: [["desk", 2]],
    }),
  );
  assert.equal(dec.verdict, "deny");
  assert.equal(dec.total, 140);
  assert.equal(dec.risk, 1.5);
  assert.match(dec.summary, /Order: exceeds mandate limit \(desk x2\)/);
  assert.equal(
    L.toRow(
      ev(2, "captured", {
        order_id: "O1",
        capture_id: "C1",
        total: "9.50",
        auto: true,
      }),
    ).verdict,
    "paid",
  );
  assert.equal(
    L.toRow(ev(3, "step_up_requested", { request_id: "abcdef1234" })).summary,
    "Waiting for you to approve (request abcdef12)",
  );
  assert.equal(
    L.toRow(
      ev(4, "step_up_resolved", { request_id: "abcdef1234", status: "denied" }),
    ).verdict,
    "denied",
  );
  assert.equal(
    L.toRow(
      ev(5, "paypal_error", {
        action: "charge_saved",
        detail: "PayPal 422: bad",
      }),
    ).verdict,
    "error",
  );
  assert.equal(
    L.toRow(ev(6, "user_message", { text: "buy a mug" })).label,
    "You asked",
  );
  assert.equal(L.toRow(ev(7, "mystery", { a: 1 })).label, "mystery");
  assert.equal(
    L.toRow(
      ev(8, "decision", {
        action: "capture",
        verdict: "allow",
        reason: "ok",
        semantic_drift: 0.5,
      }),
    ).summary.includes("intent drift 0.5"),
    true,
  );
});

test("untrusted text stays plain text and long values are cut", () => {
  const r = L.toRow(
    ev(1, "user_message", {
      text: "<img src=x onerror=alert(1)>" + "a".repeat(400),
    }),
  );
  assert.ok(r.summary.startsWith("<img"));
  assert.ok(r.summary.length <= 200);
});

test("filters", () => {
  const rows = [
    { verdict: "paid" },
    { verdict: "step_up" },
    { verdict: "deny" },
    { verdict: "denied" },
    { verdict: "error" },
    { verdict: "" },
  ];
  const n = (f) => rows.filter((r) => L.matchesFilter(r, f)).length;
  assert.deepEqual(
    [n("all"), n("paid"), n("waiting"), n("blocked"), n("errors")],
    [6, 1, 1, 2, 1],
  );
});

test("summary: spend, blocks, pending approvals, risk series, limit", () => {
  const s = L.summarize([
    ev(0, "session_start", {
      limit: "50.00",
      currency: "USD",
      merchants: ["a.com", "b.com"],
    }),
    ev(1, "decision", { verdict: "allow", risk: 0 }),
    ev(2, "captured", { total: "12.50" }),
    ev(3, "decision", { verdict: "deny", risk: 1.3 }),
    ev(4, "decision", { verdict: "step_up", risk: 1.6 }),
    ev(5, "step_up_requested", { request_id: "r1" }),
    ev(6, "step_up_requested", { request_id: "r2" }),
    ev(7, "step_up_resolved", { request_id: "r1", status: "approved" }),
    ev(8, "captured", { total: "7.5", risk: 1.6 }),
  ]);
  assert.equal(s.paid, 20);
  assert.equal(s.paidCount, 2);
  assert.equal(s.blocked, 1);
  assert.equal(s.waiting, 1);
  assert.deepEqual(s.mix, { allow: 1, step_up: 1, deny: 1 });
  assert.deepEqual(s.spark, [0, 1.3, 1.6, 1.6]);
  assert.equal(s.level, "warn");
  assert.equal(s.limit, 50);
  assert.deepEqual(s.merchants, ["a.com", "b.com"]);
});

test("empty log is safe", () => {
  const s = L.summarize([]);
  assert.equal(s.count, 0);
  assert.equal(s.level, "ok");
  assert.equal(s.limit, null);
});

test("products: newest first, https-only images, mandate flag", () => {
  const ps = L.products([
    ev(0, "session_start", { merchants: ["a.com"] }),
    ev(1, "search_results", {
      query: "mug",
      items: [
        {
          item_id: "p1@a.com",
          name: "Mug",
          merchant: "a.com",
          price: "9.5",
          image: "https://cdn/x.png",
        },
        {
          item_id: "p2@evil.com",
          name: "Bad",
          merchant: "evil.com",
          price: "1",
          image: "javascript:alert(1)",
        },
      ],
    }),
    ev(2, "search_results", {
      query: "cup",
      items: [
        { item_id: "p1@a.com", name: "Mug", merchant: "a.com", price: "9.5" },
        {
          item_id: "p3@a.com",
          name: "Cup",
          merchant: "a.com",
          price: "4",
          image: "http://insecure/x.png",
        },
      ],
    }),
  ]);
  assert.deepEqual(
    ps.map((p) => p.item_id),
    ["p3@a.com", "p2@evil.com", "p1@a.com"],
  );
  const by = Object.fromEntries(ps.map((p) => [p.item_id, p]));
  assert.equal(by["p1@a.com"].image, "https://cdn/x.png");
  assert.equal(by["p2@evil.com"].image, null);
  assert.equal(by["p3@a.com"].image, null);
  assert.equal(by["p1@a.com"].price, 9.5);
  assert.equal(by["p1@a.com"].allowed, true);
  assert.equal(by["p2@evil.com"].allowed, false);
  assert.equal(
    L.products([
      ev(0, "search_results", { items: [{ item_id: "x", merchant: "m" }] }),
    ])[0].allowed,
    null,
  );
});

test("search_results row summary", () => {
  assert.equal(
    L.toRow(ev(1, "search_results", { query: "mug", items: [{}, {}] })).summary,
    '2 offers for "mug"',
  );
});

test("chart data: risk series, decision counts, cumulative spend with limit", () => {
  const c = L.chartData([
    ev(0, "session_start", { limit: "50.00" }),
    ev(1, "decision", { verdict: "allow", risk: 0 }),
    ev(2, "captured", { total: "12.50", risk: 0 }),
    ev(3, "decision", { verdict: "deny", risk: 1.3 }),
    ev(4, "decision", { verdict: "step_up", risk: 1.6 }),
    ev(5, "captured", { total: "7.25" }),
  ]);
  assert.deepEqual(
    c.risk.map((r) => r.risk),
    [0, 0, 1.3, 1.6],
  );
  assert.deepEqual(c.mix, [
    { label: "Gate decisions", allowed: 1, waiting: 1, blocked: 1 },
  ]);
  assert.deepEqual(c.spend, [
    { n: 2, paid: 12.5, limit: 50 },
    { n: 5, paid: 19.75, limit: 50 },
  ]);
  assert.equal(c.hasLimit, true);
  assert.equal(L.chartData([]).hasLimit, false);
});
