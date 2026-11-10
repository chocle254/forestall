# Forestall

**An AI agent that stops "item not received" disputes before buyers file them.**

Forestall watches a seller's PayPal orders after payment is captured. When a shipment looks like it's going wrong, it says why in plain words, picks one fix and runs it through PayPal. Fixes that move no money (like attaching missing tracking) run on **autopilot**; refunds and replacements wait for one tap from the seller, and **Approve all** clears the whole queue at once. If a buyer opens a dispute anyway, Forestall drafts the evidence response from the order's full history.

Built for the Devpost PayPal AI Hackathon (2026).

**Live demo:** https://forestall-gamma.vercel.app (PayPal sandbox, no login needed; see [Try it](#try-it-judges)).

### Check the AI is live

The AI runs on every check in the deployed app, not just in the docs:

- [`/api/health`](https://forestall-gamma.vercel.app/api/health) shows `"ai":"ok"`, the model (`openai/gpt-oss-120b`) and its fallback routes.
- [`/api/report`](https://forestall-gamma.vercel.app/api/report) counts the AI checks run, orders flagged and fixes executed on PayPal so far.
- In the app, the **Orders** risk queue (AG Grid) shows the AI's output in every row: **AI risk** (its risk level, confidence and when it last checked), **Why (AI)** (its reason) and **AI fix · PayPal call** (the fix it chose and the PayPal API it runs, with an inline **Approve**). Every order page shows the full **AI investigation** (tools it called, what it found, its next check time).

Where it lives in the code: `backend/app/risk.py` runs the check, `investigate.py` holds the agent's tools (carrier tracking, facility alerts, the live PayPal order, buyer messages, seller policy), `recommend.py` picks the fix, and `disputes.py` drafts dispute evidence. `ai.py` calls the model through an OpenAI-compatible API.

### Built with

- **PayPal APIs (sandbox):** Orders v2 (checkout, capture, read, add tracking), Payments v2 (refunds), Disputes v1 (read + provide evidence), Webhooks (`PAYMENT.CAPTURE.COMPLETED`, `CUSTOMER.DISPUTE.CREATED`, signature-verified).
- **AI:** `openai/gpt-oss-120b` on Groq with a Qwen fallback, through any OpenAI-compatible API. It does risk assessment, runs a tool-calling investigation agent and recommends fixes, and it drafts dispute evidence.
- FastAPI (Python), React + Vite, AG Grid, Neon Postgres, EasyPost (carrier tracking, test mode), Vercel.

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
          Moves no money? ── yes ──► Autopilot runs it
                           │ no (refund / replacement)
                    Seller: Approve / Approve all / Edit / Dismiss
                           ▼
                    Backend calls the PayPal API ──► result shown

 Dispute opens anyway ──► AI drafts evidence from the order timeline
                          ──► seller submits via the Disputes API
```

### Risks the AI looks for

There are no fixed "48 hours" or "3 days" rules. On every check the AI agent investigates with its tools and judges the order against the carrier's own expected delivery date, facility alerts, what PayPal holds and what the buyer said. The table shows typical outcomes, not triggers.

| Signal | Example | Usual fix |
|---|---|---|
| No tracking attached | Parcel is moving, but its tracking isn't on the PayPal transaction | Attach tracking (`POST /v2/checkout/orders/{id}/track`) |
| Stalled shipment | No scans since Memphis, the hub has a backlog alert, and the expected date has passed | Message the buyer with an update |
| Buyer concern | "Hi, any news on my order?" | Message the buyer, or a small partial refund |
| Delivered but "not received" | Carrier says delivered, buyer says no | Message the buyer and prepare evidence |

### Where the AI fits

The model does three jobs, each returning structured JSON:

1. **Investigate and decide (agent with tools):** on every check it calls carrier tracking, facility alerts, the live PayPal order, buyer messages and the seller policy as it sees fit. It then decides the risk and why, the buyer's tone and needs, when to look again, and whether the order is safe to close.
2. **Recommend:** investigation → one fix the AI chooses and writes, with exact parameters the backend fills and checks.
3. **Draft evidence:** full order timeline → dispute response text + attachments list.

The model never calls PayPal or holds credentials. It writes a *proposed* action; only the backend executes it. Autopilot runs only fixes that move no money (attach tracking, an information-only email); anything that moves money waits for the seller's approval. The seller can switch autopilot off on the Overview.

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
│ │ #8F21…   │ S. Carter│ ● HIGH │ Paid 3 days   │ [Approve]│  │
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

- **Risk queue (AG Grid Community 36):** checkbox selection to approve several fixes at once (with the refund total shown first), a pinned totals row that follows every filter (orders at risk, order value at risk, fixes waiting), CSV export, cells that flash when the AI changes a risk call (the queue re-reads every 30 s), and every order ranked by risk (open risk, then pending approvals, then watched orders). Tabs (Needs attention, Watching, Not shipped, Protected, All), a search box (buyer, order or reason), per-column filters (text for buyer, problem and the AI's reason; number for amount; date for paid), pagination, and an **Approve** button in the row that executes the fix on PayPal. Tapping a row opens the full order card. Themed from the app's own color tokens, so it follows light and dark mode.
- **Order panel:** the timeline, the AI's reason, the proposed PayPal call with its exact parameters, and the PayPal response once it runs.
- **Disputes:** the evidence draft, editable before submitting.
- **Insights:** one small panel counting flags by type, so recurring problems stand out (for example, "you forget tracking on weekend orders").
- **Demo controls:** a "trigger scenario" button drives the delivery simulator, because the PayPal sandbox has no carrier data.

## Effectiveness

**Why it works.** PayPal decides INR disputes on evidence: was the item shipped, and is there tracking showing delivery to the buyer's address on the transaction? Most avoidable losses come from that evidence missing or arriving too late. Forestall goes after those exact gaps:

| Failure mode | What Forestall does | Effect |
|---|---|---|
| Tracking never attached | Flags it and attaches it through the API on autopilot | The proof is on the transaction **before** any dispute |
| Stalled parcel | Flags it when the carrier's expected date, scans and facility alerts say it's late | The seller reaches out first, so the buyer has less reason to dispute |
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

**No account, login or credentials needed.** Everything runs in the PayPal **sandbox**, so no real money moves. Open **https://forestall-gamma.vercel.app** on a computer or phone. The whole walkthrough takes about 10 minutes.

**Before you start**
- This is **one shared demo shop**, so you'll also see orders other testers made. Follow the order you create.
- One **Advance 1 day** tap is one simulated day. The AI checks the order each time, which takes a few seconds (the button shows *AI is checking…*). If the AI providers are busy, the page says so and retries by itself.
- **Autopilot** (switch on the Overview page) is on: fixes that move no money run by themselves. Refunds and replacements always wait for your **Approve**.

### Test 1: missing tracking, fixed on autopilot (3 min)
1. On **Overview**, tap **Quick test order**. PayPal's sandbox captures a payment with its test card, and the new order opens.
2. In the delivery story list, pick **Seller forgets to add tracking to PayPal**, then tap **Mark shipped**.
3. Tap **Advance 1 day** once or twice.
4. **What you should see:** the AI flags the order and explains, in plain words, that the tracking isn't on the PayPal transaction. Autopilot attaches it through PayPal's Orders API, and the order shows PayPal's response. Scroll to **Timeline** to see each step, including PayPal's confirmation.

### Test 2: worried buyer, refund waits for you (3 min)
1. Go back to **Overview** and tap **Quick test order** again.
2. Pick **Buyer gets worried before delivery**, tap **Mark shipped**, then tap **Advance 1 day** until the order is flagged (usually 1–2 taps).
3. **What you should see:** the AI connects the buyer's message with the hub delay and proposes one fix, often a small partial refund (never more than 25%) with a message to the buyer. It waits for you.
4. Tap **Approve**. Forestall runs the refund through PayPal's Payments API and shows the refund ID PayPal returns. You can also **Edit** or **Dismiss** it.
5. If the fix offered the buyer a choice, use **Sandbox: reply as the buyer** to answer. The AI reads the reply and queues what the buyer chose for your approval.

### Test 3: "delivered" but not received (2 min)
1. Create another order, pick **Carrier says delivered, buyer says no**, tap **Mark shipped**, and advance 2–3 days.
2. **What you should see:** the AI compares the carrier's GPS delivery proof with the PayPal shipping address, finds the parcel was left 380 m away, rates it high risk, and drafts a message to the buyer before they open a dispute.

The other stories, **Parcel gets stuck at a hub** and **On track**, work the same way. The order page also has **Real carrier API: EasyPost test parcels**, which create real EasyPost test trackers instead of the simulator.

### Test 4: the risk queue and Approve all (1 min)
1. Open **Orders**. Every order is ranked by the AI's risk call. **AI risk** shows the level, the AI's confidence and when it last checked; **Why (AI)** is its reason; **AI fix · PayPal call** is the fix it proposed and the PayPal endpoint that fix calls. Every column can be sorted and filtered (for example, type `high` in the AI risk filter).
2. Fixes waiting for approval have an **Approve** button in the row. **Approve all** (on Overview and Orders) first shows what will run, including the refund total, and runs the whole queue after you confirm.

### Test 5: a dispute and its AI evidence (2 min)
1. Open **Disputes**. It shows disputes PayPal reported through its webhook. A real sandbox "item not received" dispute is there, filed by a sandbox buyer.
2. **What you should see:** **Evidence drafted by AI**, written from the order's recorded history (payment, tracking on PayPal, carrier scans, refunds), with a strength rating and the facts it relies on. Tap **Evidence report (PDF)** to see the document Forestall attaches.
3. You can **Edit** or **Redraft** it. **Approve and submit to PayPal** sends it through the Disputes API, using whatever PayPal allows at that stage (evidence, supporting info or a message). Submitting is final for that dispute, so the next judge will see it as already submitted.

### Test 6: the store connection (optional, 1 min)
Open **Connect** and tap **Send a test fulfilment**. It sends a signed, WooCommerce-shaped "order completed" webhook through the same code a real store would use: Forestall matches it to a PayPal order, marks it shipped, attaches the tracking to PayPal and starts watching.

### Check it's live
- [`/api/health`](https://forestall-gamma.vercel.app/api/health): PayPal (`sandbox`), the AI model, the database and EasyPost are connected.
- [`/api/report`](https://forestall-gamma.vercel.app/api/report): totals so far, including AI checks run, risks caught, fixes PayPal confirmed and the share of shipped orders with proof on PayPal.

**What's simulated:** the PayPal sandbox has no carrier data, so the delivery stories use a simulated carrier (they're labelled that way in the app). Payments, tracking, refunds, disputes and webhooks are all real PayPal sandbox API calls.

## How Forestall follows an order

- **Watch starts at "shipped".** From then on the order is checked whenever something happens (a carrier scan, a buyer message, a seller action) **and** whenever the AI asked to look again. Each check is a full AI investigation that ends with the AI choosing its next check time and saying why (for example "just after the carrier's expected delivery on Thursday"). That time is stored as `next_check_at`, so the scheduler only touches orders that are due.
- **Safety rails, not rules:** code never lets more than 24h pass between checks, caps refunds at 25%, fills every PayPal parameter itself, sends the AI back to choose again if it skips attaching missing tracking, and only closes an order when the AI says it's safe **and** PayPal confirms the tracking **and** the buyer has had a day since delivery to report a problem.
- **Simulated demo orders** move only when someone presses *Advance 1 day*, so the background sweep skips them and saves the AI budget for real orders.
- **Scheduler, with no secrets and no setup:** `/api/watch/tick` is public but throttled in the database (one sweep per 5 minutes, only due orders), so calling it often costs nothing. It runs daily via Vercel Cron, every time the app is opened, and optionally every 15 minutes via GitHub Actions (`.github/workflows/watch.yml`). Sellers never deal with any of this.
- **Protected** means the AI judged the order safe: delivered, no open risk after a quiet day, and tracking **read back from PayPal** (`GET /v2/checkout/orders/{id}` shows the tracker on the transaction). That proof is what wins an INR dispute.
- **Audit trail:** every payment, scan, message, AI check (with its reason), flag and PayPal response is written to `order_timeline`. `/api/report` sums it up: orders watched, checks run, risks caught, PayPal confirmations, and the % of shipped orders with proof on PayPal.
- **Limit:** in the sandbox this proves the mechanism (every order followed, every risk caught, every fix confirmed by PayPal). Measuring real dispute reduction needs live sellers.

## Carrier data

- **EasyPost (real carrier API, test mode).** Ship an order with an EasyPost test parcel (`EZ1000000001`…`EZ7000000007`: pre-transit, in transit, out for delivery, delivered, returned, failure, unknown). Forestall creates a real EasyPost tracker, stores the carrier scans, and EasyPost pushes later updates to `/api/webhooks/easypost` (HMAC-signed and verified; every delivery is logged in `webhook_log`). The investigating agent reads the live tracker. EasyPost's test trackers carry fixed historical dates, so in test mode only, Forestall shifts them (same order and spacing) to start just after payment.
- **Simulated delivery stories** for situations EasyPost's canned test data can't show: a hub backlog, a misdelivery with GPS proof 380 m from the PayPal address, a buyer with a deadline. These are labelled "simulated" in the app.
- In production, the agent's carrier tool calls EasyPost live (USPS, UPS, FedEx, DHL…) and the simulator goes away. The agent's code does not change.

## AI

- **Model:** `openai/gpt-oss-120b` on Groq (free tier), with the same model on Cerebras as backup and smaller models (`gpt-oss-20b`, Qwen) as a last resort. Any OpenAI-compatible API works via `AI_BASE_URL` / `AI_MODEL` / `AI_API_KEY`.
- **Every check is an investigation (agent with tools).** The model gets the order's facts and decides which tools to call: carrier tracking (scans, exceptions, expected delivery, proof of delivery with GPS distance), carrier facility alerts, the **live PayPal order** (buyer's shipping address and trackers on the transaction), buyer messages, and the seller's policy. It returns its reasoning steps (what it checked, what it learned), a risk level and reason, every fact tagged by source (carrier / PayPal / buyer), the buyer's tone and needs, its next check time with a reason, and whether to close the order. The full tool-call trace is stored (`investigations`) and shown on the order page as **AI investigation**. In the sandbox the carrier tools read a simulator; in production they'd call a carrier or tracking API.
- **Recommend:** for each flagged order the model picks one fix (`attach_tracking`, `message_buyer` or `partial_refund`) and writes the words. The backend fills every PayPal parameter from real order data and enforces limits: only the order's own tracking number, refunds capped at 25%, messages that lead with what Forestall found and give the buyer a concrete resolution today (for example a same-day replacement or a refund, buyer's choice), never "I'll get back to you".
- **Built for many judges at once on a free plan:** when Groq's `gpt-oss-120b` is busy, the same model on a second provider (`AI2_*`, e.g. Cerebras's free tier) takes over at once; smaller models (`gpt-oss-20b`, then Qwen) come last, because in testing they misread evidence more often. A provider that's out of quota is skipped until it recovers. If all are busy the page says so and retries by itself. Tested with four people running demo stories at once: every check completed.
- **What the model returns** (validated with Pydantic in `risk.py` and `recommend.py`; anything off-schema is rejected and retried):

  ```jsonc
  // Check (risk.py → Assessment)
  { "steps": [{ "source": "carrier", "checked": "Carrier tracking", "learned": "No scans for 3 days at the Memphis hub" }],
    "risk_level": "high",            // low | medium | high
    "signal": "stalled",             // no_tracking | stalled | buyer_concern | delivered_not_received | carrier_exception | none
    "reason": "…", "summary": "…",
    "buyer_mood": "worried", "confidence": "high",
    "next_check_in_hours": 12, "next_check_reason": "…", "safe_to_close": false }

  // Fix (recommend.py → Recommendation)
  { "type": "partial_refund",        // attach_tracking | message_buyer | partial_refund
    "justification": "…", "buyer_message": "…", "refund_amount": 6.00,
    "offers": ["replacement", "full_refund"] }
  ```

  How the fix is chosen: the model reads its own investigation (what's wrong, the buyer's mood and needs) and picks the one fix that removes the dispute risk soonest. Code then decides what the buyer may be offered under the seller's policy (`allowed_offers` in `recommend.py`: a replacement or full refund only when the facts are serious) and sends the model back if it offers more. Missing tracking always gets `attach_tracking` first. The backend then fills the PayPal call itself (`POST /v2/checkout/orders/{id}/track` or `POST /v2/payments/captures/{id}/refund`), caps refunds at 25% and checks the tracking number is the order's own.
- Every assessment is stored with its input and model (`assessments` table): an audit trail and a cache, so unchanged orders don't spend another AI call.
- The AI never calls PayPal. It only proposes; the backend acts, by itself for no-money fixes (autopilot) and after the seller approves for refunds and replacements.

## Store connection, buyer email and tracking page

- **Store connection (WooCommerce).** When the seller completes an order with a tracking number, WooCommerce sends a signed webhook to `/api/webhooks/woocommerce`. Forestall checks the signature, matches the store order to the PayPal order (by the PayPal order or capture ID that WooCommerce PayPal Payments stores, then buyer email and amount), marks it shipped with the store's tracking number, attaches the tracking to PayPal and starts watching. Set-up steps and the webhook URLs are on the **Connect** page, which also has a **Send a test fulfilment** button that runs a store-shaped, signed payload through the same code.
- **Buyer email (SMTP).** Buyers get an automatic shipping confirmation, and any buyer message the seller approves is emailed from the shop's address. Set the `SMTP_*` variables in `backend/.env.example`. In the sandbox, set `EMAIL_REDIRECT_TO` to your own inbox. Email is best effort: a failure is logged on the order's timeline and never blocks a fix.
- **Buyer tracking page.** Every order has a private page at `/track/<code>` (linked in the emails) with the parcel's progress, scans and the conversation. The buyer can reply there, or tap one of the options the seller offered. The AI reads the reply, and any refund or replacement still waits for the seller's approval.

## Run and deploy

1. Import this repo into Vercel at the repo root. `vercel.json` defines both services.
2. In the project's **Storage** tab, add a **Neon** Postgres database. That sets `DATABASE_URL`, and the backend creates its tables on first request.
3. Add the variables from `backend/.env.example`: PayPal sandbox app credentials (developer.paypal.com), an AI key (`AI_API_KEY`, a free Groq key works), optionally a backup AI key (`AI2_API_KEY`, free at cloud.cerebras.ai) and an EasyPost test key. SMTP is optional.
4. Open the deployment: the status card should show API, Database and PayPal as connected.

### Run it locally (about 10 minutes)

You need Python 3.12+, Node 22+, a Postgres database, a PayPal sandbox app and a free AI key.

1. **Get the keys (about 5 minutes, all free):**
   - **PayPal sandbox:** sign in at [developer.paypal.com](https://developer.paypal.com) → *Apps & Credentials* → *Sandbox* → *Create App*. Copy the **Client ID** and **Secret**.
   - **AI:** create a key at [console.groq.com](https://console.groq.com/keys).
   - **Database:** a free [Neon](https://neon.tech) project (copy its connection string), or a local Postgres: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pg postgres:16`, then use `postgresql://postgres:pg@localhost:5432/postgres?sslmode=disable`.
2. **Backend** (terminal 1):
   ```bash
   cd backend
   cp .env.example .env        # fill in DATABASE_URL, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, AI_API_KEY
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```
   Tables are created on the first request. Check http://localhost:8000/api/health: `database`, `paypal` and `ai` should say `ok`.
3. **Frontend** (terminal 2):
   ```bash
   cd frontend
   npm install
   VITE_API_URL=http://localhost:8000 npm run dev
   ```
   Open http://localhost:5173 and follow [Try it](#try-it-judges) from Test 1. Everything except incoming webhooks works locally.
4. **Webhooks (optional):** PayPal can't reach `localhost`. To receive them, expose port 8000 with a tunnel (for example `ngrok http 8000`), add a webhook in your sandbox app pointing at `https://<tunnel>/api/webhooks/paypal` with `PAYMENT.CAPTURE.COMPLETED` and `CUSTOMER.DISPUTE.CREATED`, and set `PAYPAL_WEBHOOK_ID`. Without them, test orders are still captured directly by the app.

### Environment variables (`backend/.env`)

| Variable | Needed? | What it's for |
|---|---|---|
| `DATABASE_URL` | **Required** | Postgres connection string. Add `?sslmode=disable` for a local database without TLS. |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | **Required** | Your PayPal **sandbox** app. All calls go to `api-m.sandbox.paypal.com`. |
| `AI_API_KEY` | **Required** | Key for the main AI provider (Groq by default). |
| `AI_BASE_URL`, `AI_MODEL`, `AI_FALLBACK_MODEL` | Optional | Any OpenAI-compatible API and model. Defaults: Groq, `openai/gpt-oss-120b`, Qwen fallback. |
| `AI2_API_KEY`, `AI2_BASE_URL`, `AI2_MODELS`, `AI2_NAME` | Optional | Backup AI provider used when the main one is busy (Cerebras free tier by default). |
| `PAYPAL_WEBHOOK_ID` | Optional | Verifies PayPal webhook signatures. Needed only to receive webhooks. |
| `EASYPOST_API_KEY` | Optional | EasyPost **test** key (`EZTK…`) for real carrier test parcels. Without it, the simulated delivery stories still work. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional | Buyer emails. Without them, the seller sends messages and Forestall records them. |
| `EMAIL_REDIRECT_TO` | Optional | Sandbox: send every buyer email to your own inbox. |
| `STORE_NAME` | Optional | Shop name used in emails and the tracking page. |
| `WOOCOMMERCE_WEBHOOK_SECRET` | Optional | Signing secret for the WooCommerce store connection. |
| `CORS_ORIGINS` | Optional | Origins allowed to call the API. Default `http://localhost:5173`. |
| `PUBLIC_URL` | Optional | Base URL for links in emails. Default: the URL the request came in on. |

Frontend (`frontend/.env`): `VITE_API_URL` points the app at the API. Leave it empty when both run under one domain (as on Vercel).

## Status

- [x] 1. Skeleton: API, frontend, schema, health check — live at https://forestall-gamma.vercel.app
- [x] 2. PayPal sandbox auth + test order (PayPal checkout, or one-tap test-card order)
- [x] 3. PayPal webhooks (verified with PayPal): captures arrive automatically
- [x] 4. Delivery simulator + watcher (scripted stories over simulated days; scheduled sweep)
- [x] 5. AI check on every watch: tool-using agent decides risk, next check and closing (Groq `openai/gpt-oss-120b`, Qwen fallback)
- [x] 6. AI recommend (one fix per flag; PayPal parameters filled and validated by the backend)
- [x] 7. Approve / edit / dismiss; execute on PayPal; buyer reply -> follow-up fix
- [x] 8. AG Grid risk queue (ranked by risk, filter chips, quick search, column filters, inline Approve)
- [x] 9. Disputes: webhook + sync, AI evidence draft with PDF report, seller approves, submitted via Disputes API
- [ ] 10. Insights panel
- [ ] 11. Final polish

## License

[MIT](LICENSE)
