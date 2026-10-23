"""Slice 9: when a dispute opens anyway, draft the response from the order's history and submit it.

AI job 4 of 4: the model writes the evidence notes from facts Forestall recorded; tracking
details and documents are filled in by code. Nothing is submitted without the seller's approval.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db, paypal, pdf, watcher

REASON_LABEL = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "Item not received",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "Not as described",
    "UNAUTHORISED": "Unauthorised payment",
}


class EvidenceDraft(BaseModel):
    notes: str = Field(description="The seller's response to PayPal, under 1500 characters: factual, chronological, "
                                   "citing tracking, carrier scans, PayPal records and what the seller did.")
    key_facts: list[str] = Field(description="3 to 6 short facts that decide this dispute.")
    strength: Literal["strong", "moderate", "weak"]
    strength_reason: str
    recommended_response: Literal["provide_evidence", "accept_claim"] = Field(
        description="accept_claim only if the seller already refunded in full or truly can't show delivery.")


SYSTEM = """You prepare a small online seller's response to a PayPal dispute, using ONLY the facts recorded for
this order (the order history, PayPal records, carrier data, messages, refunds, replacements).
Forestall is the seller's own order-monitoring tool: never present Forestall's records as PayPal's, and don't
mention Forestall, risk checks or internal flags. Attribute each fact to its real source (PayPal, the carrier,
the buyer, the seller). Write plain English: never include field names or raw data keys.
Write the notes PayPal's reviewer will read: calm, factual, chronological, specific (dates, tracking numbers,
carrier statuses, what PayPal itself recorded). Never invent facts. If the facts show the buyer was already
refunded, say so plainly. If the evidence is weak, say so in strength_reason so the seller can decide."""


# Only facts go into evidence: what was paid, shipped, scanned, confirmed by PayPal, said and refunded.
# Forestall's own working notes (risk checks, flags, proposals) stay out of anything PayPal sees.
EVIDENCE_KINDS = {"paid", "shipped", "scan", "delivered", "paypal", "buyer_message", "action"}
EVIDENCE_SKIP_PREFIXES = ("Fix proposed", "Fix not proposed", "To do", "Seller dismissed", "Seller edited",
                          "Seller approved", "Buyer reply needs", "PayPal did not", "PayPal rejected")


def _is_evidence(kind: str, title: str) -> bool:
    return kind in EVIDENCE_KINDS and not title.startswith(EVIDENCE_SKIP_PREFIXES)


async def _order_now(order_id) -> datetime:
    pool = await db.get_pool()
    assert pool
    shift = await pool.fetchval("select demo_shift_hours from orders where id=$1", order_id) or 0
    return datetime.now(timezone.utc) + timedelta(hours=shift)


def _f(row) -> dict:
    d = dict(row)
    for k in ("messages", "allowed_actions", "raw", "evidence", "paypal_response"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return {k: (str(v) if k in ("id", "order_id") and v is not None else v) for k, v in d.items()}


# --- Sync from PayPal ------------------------------------------------------------

async def upsert_from_paypal(d: dict) -> dict:
    """Store a PayPal dispute, link it to its order, and draft evidence when it is new."""
    pool = await db.get_pool()
    assert pool
    capture_ids = [t.get("seller_transaction_id") for t in d.get("disputed_transactions", []) if t.get("seller_transaction_id")]
    order = await pool.fetchrow("select * from orders where capture_id = any($1::text[])", capture_ids) if capture_ids else None
    allowed = sorted({l["rel"] for l in d.get("links", []) if l.get("rel") not in ("self",)})
    amount = (d.get("dispute_amount") or {})
    due = d.get("seller_response_due_date")
    row = await pool.fetchrow(
        """
        insert into disputes (paypal_dispute_id, order_id, reason, status, stage, amount, currency, messages,
                              allowed_actions, due_at, outcome, raw, updated_at)
        values ($1,$2,$3,$4,$5,$6::numeric,$7,$8,$9,$10,$11,$12, now())
        on conflict (paypal_dispute_id) do update set
          order_id = coalesce(excluded.order_id, disputes.order_id), reason = excluded.reason,
          status = excluded.status, stage = excluded.stage, amount = excluded.amount, messages = excluded.messages,
          allowed_actions = excluded.allowed_actions, due_at = excluded.due_at, outcome = excluded.outcome,
          raw = excluded.raw, updated_at = now()
        returning *, (xmax = 0) as inserted
        """,
        d["dispute_id"], order["id"] if order else None, d.get("reason"), d.get("status"),
        d.get("dispute_life_cycle_stage"), amount.get("value"), amount.get("currency_code"),
        json.dumps(d.get("messages") or []), json.dumps(allowed),
        datetime.fromisoformat(due.replace("Z", "+00:00")) if due else None,
        (d.get("dispute_outcome") or {}).get("outcome_code"), json.dumps(d))
    out = _f(row)
    if order and row["inserted"]:
        async with pool.acquire() as conn:
            await watcher.log(conn, order["id"], "dispute",
                              f"Buyer opened a PayPal dispute: {REASON_LABEL.get(d.get('reason'), d.get('reason'))}",
                              {"dispute_id": d["dispute_id"], "stage": d.get("dispute_life_cycle_stage"),
                               "status": d.get("status"), "amount": amount.get("value")}, await _order_now(order["id"]))
        try:
            out = await draft_evidence(d["dispute_id"])
        except ai.AIUnavailable:
            pass  # the seller can ask for a draft again from the app
    elif order and out["status"] == "RESOLVED" and out.get("outcome"):
        async with pool.acquire() as conn:
            already = await conn.fetchval("select 1 from order_timeline where order_id=$1 and kind='dispute'"
                                          " and detail->>'outcome' = $2", order["id"], out["outcome"])
            if not already:
                await watcher.log(conn, order["id"], "dispute", f"Dispute resolved: {out['outcome']}",
                                  {"dispute_id": d["dispute_id"], "outcome": out["outcome"]}, await _order_now(order["id"]))
    return out


async def sync() -> dict:
    """Pull disputes from PayPal (backup for missed webhooks)."""
    items = await paypal.list_disputes()
    synced = []
    for it in items:
        full = await paypal.get_dispute(it["dispute_id"])
        synced.append((await upsert_from_paypal(full))["paypal_dispute_id"])
    return {"synced": synced}


# --- Evidence -------------------------------------------------------------------------

async def _facts(order_id) -> tuple[dict, list[dict]]:
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id=$1", order_id)
    tl = await pool.fetch("select kind, title, detail, occurred_at from order_timeline where order_id=$1"
                          " order by occurred_at, recorded_at", order_id)
    timeline = []
    for t in tl:
        if not _is_evidence(t["kind"], t["title"]):
            continue
        d = json.loads(t["detail"]) if isinstance(t["detail"], str) else t["detail"]
        d.pop("paypal_response", None)  # keep the prompt small; the key facts are in the titles
        timeline.append({"when": t["occurred_at"].strftime("%a %d %b %Y %H:%M UTC"), "what": t["title"],
                         "kind": t["kind"], "detail": d})
    paypal_trackers = []
    try:
        paypal_trackers = paypal.trackers_on(await paypal.get_order(o["paypal_order_id"]))
    except paypal.PayPalError:
        pass
    facts = {
        "order": {"paypal_order_id": o["paypal_order_id"], "capture_id": o["capture_id"], "buyer": o["buyer_name"],
                  "amount": f"{o['amount']} {o['currency']}", "paid": o["captured_at"].isoformat(),
                  "ship_to": o["ship_to"] if isinstance(o["ship_to"], dict) else json.loads(o["ship_to"] or "null"),
                  "carrier": o["carrier"], "tracking_number": o["tracking_number"],
                  "refunded_amount": str(o["refunded_amount"]), "resolution": o["resolution"]},
        "trackers_on_paypal_transaction (live)": paypal_trackers,
    }
    return facts, timeline


def _evidence_pdf(dispute: dict, facts: dict, timeline: list[dict], draft: EvidenceDraft) -> bytes:
    o = facts["order"]
    lines = [
        f"PayPal order {o['paypal_order_id']} · capture {o['capture_id']} · {o['amount']} · buyer {o['buyer']}",
        f"Dispute {dispute['paypal_dispute_id']} · {REASON_LABEL.get(dispute['reason'], dispute['reason'])}",
        "# Seller's statement",
        draft.notes,
        "# Key facts",
        *[f"- {k}" for k in draft.key_facts],
        "# Tracking on the PayPal transaction (read live from PayPal)",
        *([f"- {t['id']} ({t['status']})" for t in facts["trackers_on_paypal_transaction (live)"]] or ["- none"]),
        "# Order history (recorded at the time of each event)",
        *[f"{t['when']}  {t['what']}" for t in timeline],
        "",
        "Each entry was recorded when it happened. PayPal entries are PayPal's own API responses; carrier entries "
        "come from the carrier's tracking data.",
    ]
    return pdf.render(f"Evidence report: PayPal order {o['paypal_order_id']}", lines)


async def draft_evidence(dispute_id: str) -> dict:
    pool = await db.get_pool()
    assert pool
    row = await pool.fetchrow("select * from disputes where paypal_dispute_id=$1", dispute_id)
    if not row:
        raise LookupError("dispute not found")
    if not row["order_id"]:
        raise ValueError("this dispute isn't linked to a Forestall order")
    dispute = _f(row)
    facts, timeline = await _facts(row["order_id"])
    prompt = json.dumps({"dispute": {"reason": row["reason"], "stage": row["stage"], "amount": str(row["amount"]),
                                     "buyer_messages": [m.get("content") for m in dispute["messages"]]},
                         **facts, "timeline": timeline}, default=str)
    draft = await ai.chat_json(SYSTEM, "Draft the response to this dispute:\n" + prompt[:14000], EvidenceDraft)

    o = facts["order"]
    evidences = []
    if o["tracking_number"] and facts["trackers_on_paypal_transaction (live)"]:
        evidences.append({"evidence_type": "PROOF_OF_FULFILLMENT",
                          "evidence_info": {"tracking_info": [{"carrier_name": (o["carrier"] or "USPS").upper(),
                                                               "tracking_number": o["tracking_number"]}]},
                          "notes": draft.notes[:2000]})
    elif float(o["refunded_amount"] or 0) > 0:
        evidences.append({"evidence_type": "PROOF_OF_REFUND", "notes": draft.notes[:2000]})
    else:
        evidences.append({"evidence_type": "OTHER", "notes": draft.notes[:2000]})
    evidence = {**draft.model_dump(), "evidences": evidences, "document": "forestall-evidence.pdf",
                "model": ai.last_model_used or ai.model_name()}
    row = await pool.fetchrow(
        "update disputes set evidence=$2, evidence_draft=$3, evidence_status='drafted', updated_at=now()"
        " where paypal_dispute_id=$1 returning *", dispute_id, json.dumps(evidence), draft.notes)
    async with pool.acquire() as conn:
        await watcher.log(conn, row["order_id"], "dispute", f"Evidence drafted ({draft.strength} case)",
                          {"dispute_id": dispute_id, "key_facts": draft.key_facts,
                           "recommended": draft.recommended_response}, await _order_now(row["order_id"]))
    return _f(row)


async def evidence_document(dispute_id: str) -> bytes:
    pool = await db.get_pool()
    assert pool
    row = await pool.fetchrow("select * from disputes where paypal_dispute_id=$1", dispute_id)
    if not row or not row["evidence"]:
        raise LookupError("no evidence drafted yet")
    facts, timeline = await _facts(row["order_id"])
    ev = json.loads(row["evidence"]) if isinstance(row["evidence"], str) else row["evidence"]
    return _evidence_pdf(_f(row), facts, timeline, EvidenceDraft(**{k: ev[k] for k in EvidenceDraft.model_fields}))


async def edit_notes(dispute_id: str, notes: str) -> dict:
    pool = await db.get_pool()
    assert pool
    row = await pool.fetchrow("select * from disputes where paypal_dispute_id=$1", dispute_id)
    if not row or not row["evidence"]:
        raise LookupError("no evidence drafted yet")
    if row["evidence_status"] == "submitted":
        raise ValueError("evidence was already submitted")
    ev = json.loads(row["evidence"]) if isinstance(row["evidence"], str) else row["evidence"]
    ev["notes"] = notes.strip()[:2000]
    for e in ev["evidences"]:
        e["notes"] = ev["notes"]
    row = await pool.fetchrow("update disputes set evidence=$2, evidence_draft=$3, updated_at=now()"
                              " where paypal_dispute_id=$1 returning *", dispute_id, json.dumps(ev), ev["notes"])
    return _f(row)


async def submit(dispute_id: str) -> dict:
    """Seller approved: send the response to PayPal using whatever PayPal allows for this dispute right now."""
    pool = await db.get_pool()
    assert pool
    row = await pool.fetchrow("select * from disputes where paypal_dispute_id=$1", dispute_id)
    if not row or not row["evidence"]:
        raise LookupError("no evidence drafted yet")
    ev = json.loads(row["evidence"]) if isinstance(row["evidence"], str) else row["evidence"]
    # Re-read the dispute: allowed actions change as it moves through its stages.
    live = await paypal.get_dispute(dispute_id)
    allowed = {l["rel"] for l in live.get("links", [])}
    now = await _order_now(row["order_id"])
    try:
        if ev.get("recommended_response") == "accept_claim" and "accept_claim" in allowed:
            res, how = await paypal.accept_claim(dispute_id, ev["notes"]), "accepted the claim"
        elif "provide_evidence" in allowed:
            doc = await evidence_document(dispute_id)
            res = await paypal.provide_evidence(dispute_id, ev["evidences"], [("forestall-evidence.pdf", doc, "application/pdf")])
            how = "submitted evidence (with the Forestall evidence report PDF)"
        elif "send_message" in allowed:
            res, how = await paypal.send_dispute_message(dispute_id, ev["notes"]), "sent the response as a dispute message"
        else:
            raise ValueError(f"PayPal allows none of our responses right now (allowed: {sorted(allowed)})")
    except paypal.PayPalError as e:
        await pool.execute("update disputes set evidence_status='failed', paypal_response=$2, updated_at=now()"
                           " where paypal_dispute_id=$1", dispute_id, json.dumps({"status": e.status, "error": e.body}))
        async with pool.acquire() as conn:
            await watcher.log(conn, row["order_id"], "dispute", "PayPal rejected the dispute response",
                              {"dispute_id": dispute_id, "paypal_status": e.status, "paypal_error": e.body}, now)
        return {"status": "failed", "paypal_status": e.status, "paypal_error": e.body}

    row = await pool.fetchrow(
        "update disputes set evidence_status='submitted', submitted_at=now(), paypal_response=$2, updated_at=now()"
        " where paypal_dispute_id=$1 returning *", dispute_id, json.dumps(res, default=str))
    async with pool.acquire() as conn:
        await watcher.log(conn, row["order_id"], "paypal", f"PayPal: seller {how}",
                          {"dispute_id": dispute_id, "paypal_response": res}, now)
    await upsert_from_paypal(await paypal.get_dispute(dispute_id))
    return {"status": "submitted", "how": how, "dispute": _f(row)}


async def list_all() -> list[dict]:
    pool = await db.get_pool()
    assert pool
    rows = await pool.fetch(
        "select d.*, o.buyer_name, o.paypal_order_id from disputes d left join orders o on o.id = d.order_id"
        " order by d.created_at desc limit 50")
    return [_f(r) for r in rows]
