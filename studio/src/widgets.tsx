// Custom widgets: the risk gate, a product gallery with images, and the approval queue.
// They read the live store directly, so they update on every poll like the built-in widgets.
import { createWidgets } from "ag-studio-react";
import { SCALE, WARN, BLOCK, type Level } from "./tables";
import { useTables } from "./hooks";

const LEVEL_TEXT: Record<Level, string> = {
  ok: "Normal",
  warn: "Approvals required",
  block: "Read-only",
};
const LEVEL_NOTE: Record<Level, string> = {
  ok: "Purchases inside the mandate go through.",
  warn: "Every purchase now waits for your approval.",
  block: "The agent can search but not buy.",
};
const APPROVAL_BASE = (
  new URLSearchParams(location.search).get("approvals") ??
  "http://127.0.0.1:8765"
).replace(/\/$/, "");

export function RiskGate() {
  const t = useTables();
  const pct = (Math.min(t.riskNow, SCALE) / SCALE) * 100;
  return (
    <div className="ag-w ag-risk">
      <div className="ag-risk-head">
        <span className={`ag-level ${t.level}`}>{LEVEL_TEXT[t.level]}</span>
        <strong className="ag-risk-num">{t.riskNow.toFixed(2)}</strong>
      </div>
      <div
        className="ag-gate"
        role="img"
        aria-label={`Risk ${t.riskNow.toFixed(2)} of ${SCALE}`}
      >
        <i className="z ok" style={{ flex: WARN }} />
        <i className="z warn" style={{ flex: BLOCK - WARN }} />
        <i className="z block" style={{ flex: SCALE - BLOCK }} />
        <b className="ag-marker" style={{ left: `${pct}%` }} />
      </div>
      <div className="ag-gate-labels">
        <span style={{ flex: WARN }}>Normal</span>
        <span style={{ flex: BLOCK - WARN }}>Approvals required</span>
        <span style={{ flex: SCALE - BLOCK, textAlign: "right" }}>
          Read-only
        </span>
      </div>
      <p className="ag-note">{LEVEL_NOTE[t.level]}</p>
    </div>
  );
}

export function ProductGallery() {
  const t = useTables();
  if (!t.products.length)
    return (
      <div className="ag-w ag-empty">
        No products yet. Items the agent finds appear here with their pictures.
      </div>
    );
  return (
    <div className="ag-w ag-gallery">
      {t.products.map((p) => (
        <figure
          key={p.item_id}
          className={`ag-card${p.in_mandate === "No" ? " off" : ""}`}
        >
          <div className="ag-thumb">
            {p.image ? (
              <img
                src={p.image}
                alt={p.name}
                loading="lazy"
                decoding="async"
                referrerPolicy="no-referrer"
                onError={(e) => {
                  e.currentTarget.replaceWith(
                    Object.assign(document.createElement("span"), {
                      textContent: "No image",
                    }),
                  );
                }}
              />
            ) : (
              <span>No image</span>
            )}
          </div>
          <figcaption>
            <span className="ag-card-name" title={p.name}>
              {p.name}
            </span>
            <span className="ag-card-meta">{p.merchant}</span>
            <span className="ag-card-price">{p.price.toFixed(2)}</span>
            {p.in_mandate === "No" && (
              <span className="ag-tag">Not in mandate</span>
            )}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

export function ApprovalQueue() {
  const t = useTables();
  if (!t.pending.length)
    return <div className="ag-w ag-empty">Nothing is waiting for you.</div>;
  return (
    <ul className="ag-w ag-approvals">
      {t.pending.map((p) => (
        <li key={p.request_id}>
          <span>Request {p.request_id.slice(0, 8)}</span>
          {/^[0-9a-f]{32}$/.test(p.request_id) ? (
            <a
              href={`${APPROVAL_BASE}/r/${p.request_id}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open approval page
            </a>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

const form = (p: any) => p.createDefaults({});
const defs = [
  {
    id: "riskGate",
    label: "Risk gate",
    comp: RiskGate,
    form,
    defaultSize: { xSpan: 8, ySpan: 15 },
    minSize: { xSpan: 5, ySpan: 8 },
  },
  {
    id: "productGallery",
    label: "Product gallery",
    comp: ProductGallery,
    form,
    defaultSize: { xSpan: 24, ySpan: 18 },
    minSize: { xSpan: 6, ySpan: 8 },
  },
  {
    id: "approvalQueue",
    label: "Approval queue",
    comp: ApprovalQueue,
    form,
    defaultSize: { xSpan: 7, ySpan: 12 },
    minSize: { xSpan: 4, ySpan: 6 },
  },
] as const;

export const widgets = (cfg: any) =>
  createWidgets<any>({
    additionalTypes: defs as any,
    menu: [
      ...cfg.menu,
      { label: "AgentGuard", widgetIds: defs.map((d) => d.id) },
    ],
  });
