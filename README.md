# Forestall

**An AI agent that stops "item not received" disputes before buyers file them.**

Forestall watches a seller's PayPal orders after payment is captured. When a shipment looks like it's going wrong, it says why in plain words, proposes one fix, and runs that fix through PayPal as soon as the seller taps Approve. If a buyer opens a dispute anyway, Forestall drafts the evidence response from the order's full history.

Built for the Devpost PayPal AI Hackathon (2026).

---

## The problem

Small online sellers lose "item not received" (INR) disputes they could have won. It usually happens one of three ways:

- **Tracking never reached PayPal.** The parcel shipped, but the tracking number was never attached to the PayPal transaction, so the seller has no proof on record.
- **A shipment stalled and nobody noticed.** The carrier stopped updating for days, and the buyer noticed before the seller did.
- **A worried buyer got no reply.** "Where's my order?" sat unanswered until it turned into a dispute.

Each lost dispute costs the refund, the item, dispute fees, and account standing as the seller's dispute rate climbs. Chargeback tools step in **after** a dispute opens. Forestall works in the window **before** it opens.

## What it does

```
 PayPal capture ──► Order lands in Forestall
                         │
 Shipment events ──►  AI assesses risk ──► Flag + reason in plain words
 Buyer messages  ──►       │
                           ▼
                    AI recommends ONE fix
                    (attach tracking · message buyer · partial refund)
                           │
                    Seller: Approve / Edit / Dismiss
                           │  (nothing runs without approval)
                           ▼
                    Backend calls the PayPal API ──► result shown

 Dispute opens anyway ──► AI drafts evidence from the order timeline
                          ──► seller submits via the Disputes API
```

### Risk signals it watches (v1)

| Signal | Example | Usual fix |
|---|---|---|
| No tracking attached | Captured 48h ago, no tracking on the PayPal transaction | Attach tracking (`POST /v2/checkout/orders/{id}/track`) |
| Stalled shipment | No carrier update for 3+ days | Message the buyer with an update |
| Buyer concern | "Hi, any news on my order?" | Message the buyer, or a small partial refund |
| Delivered but "not received" | Carrier says delivered, buyer says no | Message the buyer and prepare evidence |

### Where the AI fits

The model does three narrow jobs, each returning structured JSON:

1. **Assess:** order + shipment events + buyer messages → risk level, signal, reason.
2. **Recommend:** assessment → one action with exact parameters and a one-line justification.
3. **Draft evidence:** full order timeline → dispute response text + attachments list.

The model never calls PayPal or holds credentials. It writes a *proposed* action; only the backend executes it, and only after the seller approves.

## How it looks

One screen, built around a single workflow. It's a work queue, not a dashboard of charts, and it works on a phone.

```
┌──────────────────────────────────────────────────────────────┐
│ Forestall                              ● 3 at risk  ○ 41 ok  │
├──────────────────────────────────────────────────────────────┤
│ RISK QUEUE                         [All] [High] [Medium]     │
│ ┌──────────┬──────────┬────────┬───────────────┬──────────┐  │
│ │ Order    │ Buyer    │ Risk   │ Why           │ Action   │  │
│ ├──────────┼──────────┼────────┼───────────────┼──────────┤  │
│ │ #8F21…   │ A. Mensah│ ● HIGH │ Paid 3 days   │ [Approve]│  │
│ │ $84.00   │          │        │ ago, no track-│          │  │
│ │          │          │        │ ing on PayPal │          │  │
│ │ #2C9A…   │ J. Ruiz  │ ● MED  │ Stuck in Mem- │ [Approve]│  │
│ │ $32.50   │          │        │ phis 4 days   │          │  │
│ └──────────┴──────────┴────────┴───────────────┴──────────┘  │
├──────────────────────────────────────────────────────────────┤
│ ORDER #8F21…                                                 │
│ Timeline  Paid → Label created → (no movement)               │
│                                                              │
│ Recommended fix: Attach tracking                             │
│   carrier: USPS   tracking: 9400 1000 0000 0000 0000 00      │
│   "Buyer protection depends on tracking being on the PayPal  │
│    transaction; this order has none after 72h."             │
│                                                              │
│   [ Approve ]   [ Edit ]   [ Dismiss ]                       │
│                                                              │
│ ✓ PayPal: tracking attached · 201 Created · 14:02            │
├──────────────────────────────────────────────────────────────┤
│ DISPUTES  1 open → evidence draft ready  [Review & submit]   │
│ INSIGHTS  Flags by signal: no tracking 12 · stalled 5 · …    │
└──────────────────────────────────────────────────────────────┘
```

- **Risk queue (AG Grid Community 36):** every order ranked by risk (open risk, then pending approvals, then watched orders). Filter chips (Needs attention, Needs approval, At risk, Watching, Not shipped, Protected/resolved), quick search, per-column text/number/date filters, pagination, and an **Approve** button in the row that executes the fix on PayPal. Tapping a row opens the full order card. Themed from the app's own color tokens, so it follows light and dark mode.
- **Order panel:** the timeline, the AI's reason, the proposed PayPal call with its exact parameters, and the PayPal response once it runs.
- **Disputes:** the evidence draft, editable before submitting.
- **Insights:** one small panel counting flags by type, so recurring problems stand out (for example, "you forget tracking on weekend orders").
- **Demo controls:** a "trigger scenario" button drives the delivery simulator, because the PayPal sandbox has no carrier data.

## Effectiveness

**Why it works.** PayPal decides INR disputes on evidence: was the item shipped, and is there tracking showing delivery to the buyer's address on the transaction? Most avoidable losses come from that evidence missing or arriving too late. Forestall goes after those exact gaps:

| Failure mode | What Forestall does | Effect |
|---|---|---|
| Tracking never attached | Flags it within 48h and attaches it through the API on approval | The proof is on the transaction **before** any dispute |
| Stalled parcel | Flags it after 3 days without carrier movement | The seller reaches out first, so the buyer has less reason to dispute |
| Anxious buyer | Spots concern in messages and drafts a reply | The question gets answered instead of escalating |
| Dispute opens anyway | Drafts evidence from the stored timeline | A complete response in minutes, not hours of digging |

**How it's measured.** Every flag, proposed action, approval and PayPal response is stored, so effectiveness can be read straight from the data:

- **Coverage:** share of captured orders that have tracking on PayPal within 48h.
- **Time to fix:** median time from flag to executed action.
- **Approval rate:** share of AI proposals the seller approves without edits, which shows whether the recommendations are trusted.
- **Prevention:** disputes opened on flagged-and-fixed orders compared with flagged-and-ignored ones.
- **Evidence turnaround:** time from dispute opened to evidence submitted.

**Limits, stated plainly.** The demo runs on the PayPal sandbox with simulated carrier data. It covers one seller, and it does not reship items or integrate real carriers. The prevention numbers above are what the data model is built to measure. They are not results from live sellers yet.

## Architecture

| Path | What | Hosted on |
|---|---|---|
| `frontend/` | React (Vite) seller app | Vercel (service `frontend`) |
| `backend/` | FastAPI: PayPal + EasyPost integration, AI agent, watcher, delivery simulator | Vercel (service `backend`, serves `/api/*`) |
| `backend/migrations/` | Postgres schema, applied automatically on first request | Neon (via Vercel Storage) |

Both run in one Vercel project ([Vercel Services](https://vercel.com/docs/services), see `vercel.json`), so they deploy together on every push and share one URL.

PayPal surface: webhooks `PAYMENT.CAPTURE.COMPLETED` and `CUSTOMER.DISPUTE.CREATED`; Orders v2 (read, track), Payments v2 (refund), Disputes v1 (read, provide-evidence).

## Try it (judges)

1. Open https://forestall-gamma.vercel.app
2. Tap **Quick test order**. PayPal (sandbox) captures a payment with its test card, and no login is needed.
3. On the new order, pick a delivery story (for example *Seller forgets to add tracking to PayPal*) and tap **Mark shipped**.
4. Tap **Advance 1 day** a few times. Forestall re-checks the order each day and flags it the moment a risk appears, with a plain-language reason.
5. Tap **Show timeline** to see every step Forestall took, including PayPal's own confirmations.

## How Forestall follows an order

- **Watch starts at "shipped".** From then on the order is checked whenever something happens (a carrier scan, a buyer message, a seller action) **and** whenever time alone creates risk: 48h without tracking on PayPal, 72h without a carrier scan, and 24h after delivery. Each check stores when the next threshold is crossed (`next_check_at`), so the scheduler only touches orders that are due.
- **Scheduler, with no secrets and no setup:** `/api/watch/tick` is public but throttled in the database (one sweep per 5 minutes, only due orders), so calling it often costs nothing. It runs daily via Vercel Cron, every time the app is opened, and optionally every 15 minutes via GitHub Actions (`.github/workflows/watch.yml`). Sellers never deal with any of this.
- **Protected** means delivered, no open risk after a quiet day, and tracking **read back from PayPal** (`GET /v2/checkout/orders/{id}` shows the tracker on the transaction). That proof is what wins an INR dispute.
- **Audit trail:** every payment, scan, message, AI check (with its reason), flag and PayPal response is written to `order_timeline`. `/api/report` sums it up: orders watched, checks run, risks caught, PayPal confirmations, and the % of shipped orders with proof on PayPal.
- **Limit:** in the sandbox this proves the mechanism (every order followed, every risk caught, every fix confirmed by PayPal). Measuring real dispute reduction needs live sellers.

## Carrier data

- **EasyPost (real carrier API, test mode).** Ship an order with an EasyPost test parcel (`EZ1000000001`…`EZ7000000007`: pre-transit, in transit, out for delivery, delivered, returned, failure, unknown). Forestall creates a real EasyPost tracker, stores the carrier scans, and EasyPost pushes later updates to `/api/webhooks/easypost` (HMAC-signed and verified; every delivery is logged in `webhook_log`). The investigating agent reads the live tracker. EasyPost's test trackers carry fixed historical dates, so in test mode only, Forestall shifts them (same order and spacing) to start just after payment.
- **Simulated delivery stories** for situations EasyPost's canned test data can't show: a hub backlog, a misdelivery with GPS proof 380 m from the PayPal address, a buyer with a deadline. These are labelled "simulated" in the app.
- In production, the agent's carrier tool calls EasyPost live (USPS, UPS, FedEx, DHL…) and the simulator goes away. The agent's code does not change.

## AI

- **Model:** `openai/gpt-oss-120b` on Groq (free tier), with `qwen/qwen3.8-27b` as automatic fallback. Any OpenAI-compatible API works via `AI_BASE_URL` / `AI_MODEL` / `AI_API_KEY`.
- **Assess:** order facts (hours since payment, carrier scans, whether tracking is on PayPal, buyer messages) go in, and the model returns a risk level, signal, reason and the facts it used, validated against a JSON schema.
- **Investigate (agent with tools):** before anyone contacts the buyer, the model investigates by calling tools itself: carrier tracking (scans, exceptions, expected delivery, proof of delivery with GPS distance), carrier facility alerts, the **live PayPal order** (buyer's shipping address and trackers on the transaction), buyer messages, and the seller's policy. It reports findings with every fact tagged by source (carrier / PayPal / buyer). The full tool-call trace is stored (`investigations`). In the sandbox the carrier tools read a simulator; in production they'd call a carrier or tracking API.
- **Recommend:** for each flagged order the model picks one fix (`attach_tracking`, `message_buyer` or `partial_refund`) and writes the words. The backend fills every PayPal parameter from real order data and enforces limits: only the order's own tracking number, refunds capped at 25%, messages that lead with what Forestall found and give the buyer a concrete resolution today (for example a same-day replacement or a refund, buyer's choice), never "I'll get back to you".
- Every assessment is stored with its input and model (`assessments` table): an audit trail and a cache, so unchanged orders don't spend another AI call.
- The AI never calls PayPal. It only proposes; the backend acts after the seller approves.

## Run and deploy

1. Import this repo into Vercel at the repo root. `vercel.json` defines both services.
2. In the project's **Storage** tab, add a **Neon** Postgres database. That sets `DATABASE_URL`, and the backend creates its tables on first request.
3. Add the PayPal sandbox variables from `backend/.env.example`.
4. Open the deployment: the status card should show API, Database and PayPal as connected.

Local backend: `cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload`
Local frontend: `cd frontend && npm install && VITE_API_URL=http://localhost:8000 npm run dev`

## Status

- [x] 1. Skeleton: API, frontend, schema, health check — live at https://forestall-gamma.vercel.app
- [x] 2. PayPal sandbox auth + test order (PayPal checkout, or one-tap test-card order)
- [ ] 3. Capture webhook intake
- [x] 4. Delivery simulator + watcher (scripted stories over simulated days; scheduled sweep)
- [x] 5. AI assess (Groq `openai/gpt-oss-120b`, Qwen fallback)
- [x] 6. AI recommend (one fix per flag; PayPal parameters filled and validated by the backend)
- [x] 7. Approve / edit / dismiss; execute on PayPal; buyer reply -> follow-up fix
- [x] 8. AG Grid risk queue (ranked by risk, filter chips, quick search, column filters, inline Approve)
- [ ] 9. Disputes + evidence
- [ ] 10. Insights panel
- [ ] 11. Final polish

## License

[MIT](LICENSE)
