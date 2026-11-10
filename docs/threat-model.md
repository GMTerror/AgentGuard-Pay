# Threat model

## Scope and assumptions

**Protected:** the user's money and intent. The agent may only spend what the user's signed mandate allows, with the user's consent where required.

**Attacker can:**
- put arbitrary text in anything the agent reads (product names and descriptions, tool results);
- observe and replay traffic and signed mandates they obtain;
- try to forge or edit a mandate;
- influence the agent over many turns.

**Attacker cannot (assumed):** read the user's private key, run code inside the gateway process, or alter the PayPal sandbox itself.

**The LLM is untrusted.** If the attacker controls what it reads, they control what it asks for. Security must hold when the model does exactly what the attacker wants.

## Threats and mitigations

| # | Threat | Mitigation | Covered by | Residual risk |
|---|---|---|---|---|
| T1 | Indirect prompt injection makes the agent buy from the attacker | Agent supplies ids and quantities only; merchant allow-list, price and total recomputed by the gate | `test_policy`, `test_gateway`, demo 1 | A malicious merchant *inside* the allow-list can still sell the user something they did not want, up to the limit |
| T2 | Agent over-spends or miscalculates | Total computed by the gate; per-mandate cumulative cap; capture re-checks the limit | `test_policy` (cumulative, over-limit) | None beyond the limit itself |
| T3 | Mandate edited (higher limit) | Ed25519 signature over canonical JSON | `test_mandate`, demo 2 | Key compromise (out of scope) |
| T4 | Mandate replayed | Single-use nonce, session binding, expiry | `test_mandate`, demo 2 | Nonce store is in memory: replay after a gateway restart works until the mandate expires |
| T5 | Mandate forged or issued for an unknown user | Only registered public keys are accepted | `test_mandate`, demo 2 | Key registration flow is not implemented |
| T6 | Order total changed between create and capture | Capture re-validates against the total PayPal reports | `test_gateway` (tampered total) | None known |
| T7 | Capture before the buyer approved, or a double capture | Requires PayPal status `APPROVED`; policy rejects already-captured orders; stable capture request id | `test_gateway`, `test_policy` | None known |
| T8 | Currency swap | Currency checked at create (catalog) and capture (PayPal) | `test_gateway` | None known |
| T9 | Agent approves its own step-up | No agent tool reaches approve/deny; decisions are single-use and expire | `test_stepup`, `test_gateway` | In this prototype everything is one process, so a code-execution compromise bypasses it (see T14) |
| T10 | Slow multi-turn manipulation, each turn mild | Cumulative score with decay escalates to approval or read-only | `test_risk`, demo 3 | Detection depends on a fixed phrase list (T11); scripted attack, not yet evaluated on real sessions |
| T11 | Injection phrased to evade the detector | The deterministic gate does not depend on detection | `test_gateway` | Paraphrase defeats the regex. Detection quality is an open research question |
| T12 | Audit log edited or truncated | Hash chain; `verify_log` fails on edits or deletions | `test_audit` | Whoever can write the file can rewrite the whole chain; needs external anchoring |
| T13 | PayPal error text leaks internals to the agent | Agent receives a generic "payment provider error"; details go to the audit log only | `test_gateway` | None known |
| T26 | **Auto-pay removes the buyer from every purchase.** A hijacked agent can spend inside the mandate without any PayPal page. | Mandate limits and merchant allow-list, step-up above 80% of the limit and at risk `warn`, and the gate running before any charge | `test_autopay` | Inside the limit, a manipulated agent can still buy the wrong item from an allowed merchant. Lower per-order caps would shrink this |
| T27 | The saved-payment token is a standing credential (with the API secret it can charge the buyer). | Held only by the gateway, never given to the agent, never written to the audit log | `test_autopay` | The prototype keeps it in `.env`; production needs an encrypted store and, ideally, a TEE |
| T28 | A charge that does not match the order (wrong total or currency, or not `COMPLETED`). | PayPal's response is checked after the charge; actual spend is booked; mismatches are logged | `test_autopay` | The money has already moved by the time it is detected; refunds are not implemented |
| T29 | Wrong item inside the mandate (misunderstanding or hijack). | Semantic intent check against the user's own messages escalates mismatches to approval | `test_semantic`, demo 4 | Item titles are merchant-written, so an attacker can keyword-stuff a title to look similar; the floor needs tuning per model; it only escalates, never blocks |
| T30 | The dashboard exposes the audit log (prompts, order ids, amounts) to anyone who can reach it. | Read-only, binds to 127.0.0.1 only, path traversal blocked, log text rendered as text | `test_dashboard_server` | No authentication. Do not expose the port beyond localhost |
| T31 | Product images are loaded by the browser from third-party hosts (the catalog's URLs), on the dashboard and on the step-up page. | Only `https://` URLs are used, `referrerpolicy=no-referrer`, images are plain `<img>` elements; non-https and non-URL values become a placeholder. The step-up page also sends a Content-Security-Policy that allows images only over https and forbids framing. | `logic.test.js`, `test_stepup_details` | The image hosts can see the viewer's IP address. Catalog images are untrusted content, and a picture can mislead (see T16). |
| T32 | The AG Grid Enterprise bundle and license key. | Not committed (git-ignored, downloaded by the user with an integrity check against the npm registry hash); the key lives in `.env` and is served only to localhost | `test_get_enterprise`, `test_dashboard_server` | A trial key expires; the dashboard falls back to Community when the bundle is absent |
| T33 | The AG Studio app is a large third-party bundle (about 7 MB) that renders log text, image URLs and approval links. | Custom widgets render text via React (escaped), images only from https URLs, approval links only for 32-hex request ids; the Studio package is installed from npm, not committed | `studio` build, manual check | Supply chain: the dependency tree is only as trusted as npm. The approval queue links to a localhost port that is assumed to be the agent's approval page |
| T34 | The Studio chat agent is a second LLM that reads the audit log, which contains untrusted product text and user prompts. | The auditor has read tools plus widget creation only: no approval, payment or mandate tool exists in its tool set, and its instructions mark table text as data. The approval page is still reached only by a link the user clicks. | test_llm_proxy (relay limits); tool list reviewed by hand | An injected product name could still make the agent write a misleading widget or answer. It cannot move money or approve anything. |
| T35 | The /api/llm relay adds a provider key and forwards browser requests, and sends audit data (prompts, products, amounts) to the model provider. | Off unless LLM_API_KEY is set; localhost bind; Host and Origin must match; only /responses and /chat/completions; 2 MB body cap; key added server-side and never returned; upstream headers are not passed back. | test_llm_proxy | No authentication beyond localhost (same as T30). Everything the agent reads leaves the machine (extends T25). The "read-only server" claim in T30 now excludes this one POST route. |

*T26 to T35 are listed here with the other mitigated threats. Their ids are kept so references to them stay valid.*

## Not covered (known gaps)

| # | Gap | Why it matters | Direction |
|---|---|---|---|
| T14 | **Gateway and credentials share a process with the agent code.** A bug that gives the model code execution exposes the PayPal secret. | The core claim "keys are not addressable by the model" is only true with isolation. | TEE with remote attestation; at minimum a separate process or container |
| T15 | **The catalog is trusted for price and merchant.** With Channel3 that means trusting a third party's data. | The gate recomputes from the snapshot, so it inherits the data's honesty. Snapshots expire after 15 minutes, which limits drift but does not detect a wrong price. | Verify against the merchant's own checkout; signed catalog data |
| T16 | **The step-up page shows items, merchant, price and total, but the names and pictures are merchant-written, and approval has no strong authentication.** | The user can see what they are approving, but a misleading title or picture (a gift card listed as a mug) can still fool a hurried user, and anyone who can reach the page URL can click Approve (T17). | WebAuthn confirmation; show the merchant's own checkout page |
| T17 | Step-up page is localhost-only with an unguessable id and no further authentication. | Not suitable beyond a demo. | Authenticated push channel |
| T18 | Risk state is per gateway instance and per session; a fresh session starts at zero. | A patient attacker can restart the session. | Per-user, persisted risk history |
| T19 | Uncaptured orders do not count toward the spend limit. | Many open orders can pile up, though captures stay capped. | Count open orders against the limit, or cap their number |
| T20 | Identical orders in one session reuse the same PayPal request id. | A deliberate second identical purchase returns the first order. | Add a per-order counter when that behaviour is wanted |
| T21 | No rate limiting or cost controls on tool calls or model usage. | Denial of service, runaway cost. | Per-session budgets |
| T22 | Regulatory compliance (e.g. SCA) is not assessed. | The step-up flow resembles SCA but has not been reviewed against any standard. | Out of scope for the prototype |
| T23 | Merchant allow-list depends on the domain string Channel3 reports (lowercased, `www.` removed). A mandate written with a different spelling will not match. | Over-blocking (safe), but confusing. A lookalike domain a user allows by mistake is allowed. | Show the user the exact domains when they sign the mandate |
| T24 | The PayPal sandbox charge is priced from a real offer but no merchant fulfils it. | The demo shows the payment control, not real purchase fulfilment. | Real merchant checkout integration is out of scope |
| T25 | User messages, product text, order ids and PayPal approval links are sent to the model provider (Groq) and the catalog provider (Channel3). | Privacy exposure to third parties; the approval link only works for the sandbox buyer who logs in. | Data minimisation; avoid sending personal data to the model |

## What this design is and is not

It is a way to make the *consequences* of a hijacked agent bounded and visible: capped, approved where it matters, and logged. It is not a way to stop the model from being hijacked, and it does not claim to.
