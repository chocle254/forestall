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

- **Risk queue:** an AG Grid table, filterable by risk level and signal, with Approve inline.
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
| `frontend/` | React (Vite) seller app | Vercel |
| `backend/` | FastAPI: PayPal webhooks, AI calls, action execution, delivery simulator | Railway |
| `backend/migrations/` | Postgres schema, applied automatically on boot | Neon (via Vercel Storage) |

PayPal surface: webhooks `PAYMENT.CAPTURE.COMPLETED` and `CUSTOMER.DISPUTE.CREATED`; Orders v2 (read, track), Payments v2 (refund), Disputes v1 (read, provide-evidence).

## Run and deploy

1. **Database:** in Vercel → Storage, create a Neon Postgres database and copy its `DATABASE_URL`. The backend creates the tables on first start.
2. **Backend (Railway):** New Project → Deploy from GitHub → this repo, root directory `backend`. Set the variables from `backend/.env.example`.
3. **Frontend (Vercel):** import this repo with root directory `frontend`. Set `VITE_API_URL` to the Railway URL.
4. Add the Vercel URL to the backend's `CORS_ORIGINS`.
5. Open the frontend: the status card should show API, Database and PayPal as connected.

Local backend: `cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload`
Local frontend: `cd frontend && npm install && npm run dev`

## Status

- [ ] 1. Skeleton: API, frontend, schema, health check (code done, deploy pending)
- [ ] 2. PayPal sandbox auth + test order
- [ ] 3. Capture webhook intake
- [ ] 4. Delivery simulator
- [ ] 5. AI assess
- [ ] 6. AI recommend
- [ ] 7. Approve and execute
- [ ] 8. AG Grid risk queue
- [ ] 9. Disputes + evidence
- [ ] 10. Insights panel
- [ ] 11. Final polish

## License

[MIT](LICENSE)
