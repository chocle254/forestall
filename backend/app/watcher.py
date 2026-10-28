"""Watches every shipped order until it is safely delivered.

An order is checked:
  - when something happens (new carrier scan, buyer message, seller action), and
  - when the AI asked to look again. Every check is an AI investigation (risk.py), and the
    agent itself decides when the next one should be and why (for example "just after the
    carrier's expected delivery date"). Code only caps the gap at 24 hours.
The order closes as protected when the AI judges it safe AND PayPal confirms the tracking
is on the transaction AND the buyer has had a day since delivery to report a problem.
The AI can't close an order without that proof.

Every step is written to `order_timeline`, which is the proof that the order was followed.
"""

import json
from datetime import datetime, timedelta, timezone

from . import ai, db, easypost, emails, paypal, recommend, risk, simulator

# Safety rails around the AI's decisions (not risk rules):
MAX_GAP = timedelta(hours=24)               # never go longer than this between checks, whatever the AI says
QUIET_AFTER_DELIVERY = timedelta(hours=24)  # give the buyer a day to report a problem before closing


async def log(conn, order_id, kind: str, title: str, detail: dict | None = None, at: datetime | None = None):
    await conn.execute(
        "insert into order_timeline (order_id, kind, title, detail, occurred_at) values ($1,$2,$3,$4,coalesce($5, now()))",
        order_id, kind, title, json.dumps(detail or {}, default=str), at,
    )


def _now(order) -> datetime:
    """The order's clock: wall time plus any demo time-travel."""
    return datetime.now(timezone.utc) + timedelta(hours=order["demo_shift_hours"])


# --- Shipping ----------------------------------------------------------------

async def ship(order_id: str, scenario: str | None = None, carrier: str = "USPS",
               tracking_number: str | None = None, attach_to_paypal: bool | None = None) -> dict:
    """Mark an order shipped and start watching it.

    With a scenario, the order's clock is moved to the label time and the
    delivery story starts playing; otherwise this is a real seller shipment.
    """
    pool = await db.get_pool()
    assert pool
    if scenario and scenario not in simulator.SCENARIOS:
        raise KeyError(scenario)
    async with pool.acquire() as conn:
        o = await conn.fetchrow("select * from orders where id = $1", order_id)
        if not o:
            raise LookupError("order not found")
        if o["watch_status"] != "not_shipped":
            raise ValueError("order is already shipped")

        shift = o["demo_shift_hours"]
        if scenario:
            real_age_h = (datetime.now(timezone.utc) - o["captured_at"]).total_seconds() / 3600
            shift = max(0, round(simulator.label_hour(scenario) - real_age_h))
            if attach_to_paypal is None:
                attach_to_paypal = simulator.SCENARIOS[scenario]["attach_tracking"]
        tracking_number = tracking_number or simulator.tracking_number()
        shipped_at = datetime.now(timezone.utc) + timedelta(hours=shift)

        async with conn.transaction():
            await conn.execute(
                """update orders set shipped_at=$2, tracking_number=$3, carrier=$4, demo_shift_hours=$5,
                   demo_scenario=$6, carrier_source=$7, watch_status='watching', next_check_at=now() where id=$1""",
                order_id, shipped_at, tracking_number, carrier, shift, scenario, "simulator" if scenario else "manual",
            )
            await conn.execute("delete from shipment_events where order_id=$1", order_id)
            await conn.execute("delete from buyer_messages where order_id=$1", order_id)
            await log(conn, order_id, "shipped", f"Marked shipped · {carrier} {tracking_number}",
                      {"carrier": carrier, "tracking_number": tracking_number, "scenario": scenario}, shipped_at)

    if attach_to_paypal:
        await attach_tracking(order_id, actor="seller")
    await emails.shipping_confirmation(order_id)
    return await check(order_id, trigger="shipped")


async def ship_with_easypost(order_id: str, tracking_code: str, carrier: str = "USPS",
                             attach_to_paypal: bool = True, webhook_url: str | None = None) -> dict:
    """Ship with a real EasyPost tracker; EasyPost's carrier data drives the watch."""
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id=$1", order_id)
    if not o:
        raise LookupError("order not found")
    if o["watch_status"] != "not_shipped":
        raise ValueError("order is already shipped")
    if webhook_url:
        try:
            await easypost.ensure_webhook(webhook_url)
        except easypost.EasyPostError:
            pass  # tracking still works by polling; the webhook is a bonus
    tracker = await easypost.create_tracker(tracking_code, carrier)
    now = datetime.now(timezone.utc)

    # Test mode: shift EasyPost's fixed historical timestamps so the parcel's history starts an hour
    # after payment, and move the order's clock to just after the latest scan.
    offset_s, shift_h, shipped_at = 0, 0, now
    times = [datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00"))
             for e in easypost.details_to_events(tracker) if e["occurred_at"]]
    if easypost.test_mode() and times:
        offset = (o["captured_at"] + timedelta(hours=1)) - min(times)
        offset_s = int(offset.total_seconds())
        last = max(times) + offset
        shift_h = max(0, int(((last - now).total_seconds() + 3599) // 3600) + 1)
        shipped_at = min(times) + offset

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """update orders set shipped_at=$2, tracking_number=$3, carrier=$4, carrier_source='easypost',
               easypost_tracker_id=$5, tracking_public_url=$6, demo_scenario=null, demo_shift_hours=$7,
               carrier_time_offset_s=$8, watch_status='watching', next_check_at=now() where id=$1""",
            order_id, shipped_at, tracking_code, tracker.get("carrier") or carrier, tracker["id"],
            tracker.get("public_url"), shift_h, offset_s)
        await conn.execute("delete from shipment_events where order_id=$1", order_id)
        await log(conn, order_id, "shipped", f"Marked shipped · {tracker.get('carrier') or carrier} {tracking_code} (EasyPost)",
                  {"carrier": carrier, "tracking_number": tracking_code, "easypost_tracker": tracker["id"],
                   "public_url": tracker.get("public_url"),
                   "test_mode_time_shift_hours": round(offset_s / 3600, 1) if offset_s else None}, shipped_at)
    await ingest_easypost(order_id, tracker)
    if attach_to_paypal:
        await attach_tracking(order_id, actor="seller")
    await emails.shipping_confirmation(order_id)
    return await check(order_id, trigger="shipped")


async def ingest_easypost(order_id, tracker: dict) -> int:
    """Store EasyPost scans we haven't seen yet and put them on the timeline. Returns how many were new."""
    pool = await db.get_pool()
    assert pool
    new = 0
    offset_s = await pool.fetchval("select carrier_time_offset_s from orders where id=$1", order_id) or 0
    async with pool.acquire() as conn, conn.transaction():
        for e in easypost.details_to_events(tracker):
            if not e["occurred_at"]:
                continue
            at = datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00")) + timedelta(seconds=offset_s)
            inserted = await conn.fetchval(
                """insert into shipment_events (order_id, status, location, occurred_at, source, message)
                   values ($1,$2,$3,$4,'easypost',$5) on conflict do nothing returning id""",
                order_id, e["status"], e["location"], at, e["message"])
            if inserted:
                new += 1
                where = f" · {e['location']}" if e["location"] else ""
                await log(conn, order_id, "delivered" if e["status"] == "delivered" else "scan",
                          f"Carrier (EasyPost): {e['status'].replace('_', ' ')}{where}",
                          {"status": e["status"], "message": e["message"], "source": "easypost",
                           "test_mode_time_shifted": bool(offset_s)}, at)
        if new:
            await conn.execute("update orders set next_check_at=now() where id=$1", order_id)
    return new


async def attach_tracking(order_id: str, actor: str, notify_payer: bool = False) -> dict:
    """Attach the order's tracking to its PayPal transaction (a real PayPal call)."""
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id=$1", order_id)
    result = await paypal.add_tracking(o["paypal_order_id"], o["capture_id"], o["tracking_number"],
                                       o["carrier"] or "USPS", notify_payer=notify_payer)
    at = _now(o)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("update orders set tracking_attached_at=$2, next_check_at=now() where id=$1", order_id, at)
        await log(conn, order_id, "paypal", "PayPal confirmed tracking on the transaction",
                  {"by": actor, "tracking_number": o["tracking_number"], "notify_payer": notify_payer,
                   "paypal_response": result}, at)
    return result


# --- Checking ----------------------------------------------------------------

async def _reveal_demo_events(conn, o) -> list[str]:
    """Write scripted scans/messages whose time has come; return their kinds."""
    if not o["demo_scenario"]:
        return []
    scans_done = await conn.fetchval("select count(*) from shipment_events where order_id=$1", o["id"])
    msgs_done = await conn.fetchval("select count(*) from buyer_messages where order_id=$1", o["id"])
    ship_to = json.loads(o["ship_to"]) if isinstance(o["ship_to"], str) else o["ship_to"]
    scans, msgs = simulator.due_events(o["demo_scenario"], o["captured_at"], _now(o), scans_done, msgs_done, ship_to)
    kinds = []
    for at, status, loc in scans:
        await conn.execute(
            "insert into shipment_events (order_id, status, location, occurred_at) values ($1,$2,$3,$4)",
            o["id"], status, loc, at)
        pretty = status.replace("_", " ")
        if status == "delivered":
            pretty = "delivered"
        await log(conn, o["id"], "delivered" if status == "delivered" else "scan",
                  f"Carrier: {pretty} · {loc}", {"status": status, "location": loc}, at)
        kinds.append(status)
    for at, body in msgs:
        await conn.execute("insert into buyer_messages (order_id, body, received_at) values ($1,$2,$3)", o["id"], body, at)
        await log(conn, o["id"], "buyer_message", "Buyer wrote in", {"text": body}, at)
        kinds.append("buyer_message")
    return kinds


async def check(order_id: str, trigger: str = "scheduled") -> dict:
    """Bring a watched order up to date, run the AI check, and schedule the next one."""
    pool = await db.get_pool()
    assert pool
    async with pool.acquire() as conn:
        o = await conn.fetchrow("select * from orders where id=$1", order_id)
        if not o:
            raise LookupError("order not found")
        if o["watch_status"] != "watching":
            return {"order_id": str(order_id), "watch_status": o["watch_status"], "skipped": True}
        async with conn.transaction():
            new_events = await _reveal_demo_events(conn, o)
        prev_flag = await conn.fetchrow("select signal, risk_level from flags where order_id=$1 and not resolved", order_id)

    await _follow_original_parcel(order_id)

    try:
        a = await risk.assess(order_id)
    except ai.AIUnavailable as e:
        async with pool.acquire() as conn:
            await log(conn, order_id, "check", "Check postponed: AI busy" if e.busy else "Check postponed: AI unavailable",
                      {"error": str(e), "trigger": trigger}, _now(o))
            await conn.execute("update orders set next_check_at = now() + interval '5 minutes' where id=$1", order_id)
        return {"order_id": str(order_id), "error": "The AI is busy with other orders right now." if e.busy else str(e),
                "busy": e.busy, "retry_in_s": 10}

    async with pool.acquire() as conn:
        o = await conn.fetchrow("select * from orders where id=$1", order_id)
        now = _now(o)
        await log(conn, order_id, "check", f"AI investigated: {a['risk_level']} risk",
                  {"trigger": trigger, "risk_level": a["risk_level"], "signal": a["signal"], "reason": a["reason"],
                   "summary": a.get("summary"), "steps": a.get("steps"), "tools_used": a.get("tools_used"),
                   "buyer_mood": a.get("buyer_mood"), "next_check_reason": a.get("next_check_reason"),
                   "safe_to_close": a.get("safe_to_close"), "model": a.get("model"),
                   "cached": a["cached"], "new_events": new_events}, now)

        flag = a["flag"]
        if flag and (not prev_flag or prev_flag["signal"] != flag["signal"] or prev_flag["risk_level"] != flag["risk_level"]):
            await log(conn, order_id, "flag", f"Flagged {flag['risk_level']}: {flag['signal'].replace('_', ' ')}",
                      {"reason": flag["reason"], "signal": flag["signal"], "risk_level": flag["risk_level"]}, now)
        if prev_flag and not flag:
            await log(conn, order_id, "flag_cleared", "Risk cleared", {"previous_signal": prev_flag["signal"]}, now)
            await recommend.supersede_for_order(conn, order_id)

    proposal = None
    if a["flag"]:
        proposal = await _propose(order_id, now)

    async with pool.acquire() as conn:

        events = await conn.fetch("select status, occurred_at from shipment_events where order_id=$1 order by occurred_at", order_id)
        delivered_at = next((e["occurred_at"] for e in events if e["status"] == "delivered"), None)

        # Protected: the AI judges the order safe, and PayPal itself confirms the tracking is on the transaction.
        if (a.get("safe_to_close") and delivered_at and o["tracking_attached_at"] and not flag
                and now >= delivered_at + QUIET_AFTER_DELIVERY):
            proof = await _paypal_proof(o)
            if proof.get("trackers_on_paypal"):
                await conn.execute("update orders set watch_status='protected', next_check_at=null where id=$1", order_id)
                await log(conn, order_id, "protected", "Watch closed: delivered with proof on PayPal",
                          {**proof, "ai_reason": a.get("next_check_reason")}, now)
                return {**_summary(a), "watch_status": "protected", "proof": proof, "proposal": proposal}

        nxt = _next_check(a, now)
        if a.get("safe_to_close") and delivered_at and now < delivered_at + QUIET_AFTER_DELIVERY:
            nxt = min(nxt, delivered_at + QUIET_AFTER_DELIVERY)  # the AI is ready to close: look again when allowed
        scans_done, msgs_done = len(events), await conn.fetchval("select count(*) from buyer_messages where order_id=$1", order_id)
        if o["demo_scenario"]:
            ev = simulator.next_event_time(o["demo_scenario"], o["captured_at"], scans_done, msgs_done)
            if ev and ev > now:
                nxt = min(nxt, ev)
        wall = nxt - timedelta(hours=o["demo_shift_hours"]) + timedelta(minutes=1)
        if proposal and proposal.get("pending"):  # the fix still needs writing: come back soon
            wall = min(wall, datetime.now(timezone.utc) + timedelta(minutes=5))
        await conn.execute("update orders set next_check_at=$2 where id=$1", order_id, wall)
    busy = bool(proposal and proposal.get("pending"))
    return {**_summary(a), "watch_status": "watching", "proposal": None if busy else proposal,
            "next_check_in_hours": round((nxt - now).total_seconds() / 3600, 1),
            **({"busy": True, "retry_in_s": 10, "error": "Risk found; the AI is busy writing the fix."} if busy else {})}


ACTION_TITLE = {
    "attach_tracking": "Fix proposed: attach tracking to PayPal",
    "message_buyer": "Fix proposed: message the buyer",
    "partial_refund": "Fix proposed: goodwill partial refund",
}


async def _propose(order_id: str, now: datetime) -> dict | None:
    """Ask the AI for a fix when a flag is new or changed; log it once."""
    pool = await db.get_pool()
    assert pool
    before = await pool.fetchval("select id from actions where order_id=$1 and status='proposed'", order_id)
    try:
        p = await recommend.recommend(order_id)
    except (ai.AIUnavailable, ValueError) as e:
        async with pool.acquire() as conn:
            await log(conn, order_id, "action", "Fix not proposed yet", {"error": str(e)}, now)
            if isinstance(e, ai.AIUnavailable):
                await conn.execute("update orders set next_check_at = now() + interval '5 minutes' where id=$1", order_id)
        return {"pending": True} if isinstance(e, ai.AIUnavailable) and e.busy else None
    if p and str(before) != p["id"]:
        async with pool.acquire() as conn:
            await log(conn, order_id, "action", ACTION_TITLE[p["type"]],
                      {"action_id": p["id"], "type": p["type"], "justification": p["justification"],
                       "params": p["params"], "model": p.get("model")}, now)
    return p


ORIGINAL_PARCEL_NEWS = {
    "return_to_sender": "First parcel is on its way back to you (returned to sender). You can restock it.",
    "delivered": "Heads-up: the first parcel was delivered after all. The buyer may now have two; consider asking "
                 "them to return or keep it.",
}


async def _follow_original_parcel(order_id) -> None:
    """After a replacement: keep an eye on the first parcel and tell the seller what becomes of it."""
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id=$1", order_id)
    if not o or not o["original_easypost_tracker_id"] or not easypost.configured():
        return
    try:
        t = await easypost.get_tracker(o["original_easypost_tracker_id"])
    except easypost.EasyPostError:
        return
    status = t.get("status")
    if status and status != o["original_parcel_status"]:
        async with pool.acquire() as conn:
            await conn.execute("update orders set original_parcel_status=$2 where id=$1", order_id, status)
            await log(conn, order_id, "scan", ORIGINAL_PARCEL_NEWS.get(status, f"First parcel update: {status}"),
                      {"tracking_number": o["original_tracking_number"], "status": status}, _now(o))


def _summary(a: dict) -> dict:
    return {k: a.get(k) for k in ("risk_level", "signal", "reason", "cached", "flag", "summary", "steps",
                                  "next_check_reason", "model")}


def _next_check(a: dict, now: datetime) -> datetime:
    """When the AI asked to look again, capped by the safety rail."""
    hours = a.get("next_check_in_hours") or 12
    return now + min(timedelta(hours=max(1, hours)), MAX_GAP)


async def _paypal_proof(o) -> dict:
    """Read the order back from PayPal and record what PayPal itself says."""
    try:
        po = await paypal.get_order(o["paypal_order_id"])
        return {"paypal_order_status": po.get("status"), "trackers_on_paypal": paypal.trackers_on(po),
                "read_from": "GET /v2/checkout/orders/{id}"}
    except paypal.PayPalError as e:
        return {"error": f"PayPal read failed ({e.status})"}


# --- Demo time travel and the scheduled sweep ---------------------------------

async def advance(order_id: str, hours: int = 24) -> dict:
    """Move this order's clock forward, then check it (demo only)."""
    pool = await db.get_pool()
    assert pool
    status = await pool.fetchval("select watch_status from orders where id=$1", order_id)
    if status is None:
        raise LookupError("order not found")
    if status != "watching":
        raise ValueError(f"order is {status}, not being watched")
    await pool.execute("update orders set demo_shift_hours = demo_shift_hours + $2 where id=$1", order_id, hours)
    return await check(order_id, trigger=f"time +{hours}h")


SWEEP_EVERY = timedelta(minutes=5)


async def sweep(limit: int = 20) -> dict:
    """Check every watched order that is due.

    Safe to call publicly: the database lets only one sweep run per SWEEP_EVERY,
    and a sweep only touches orders whose next_check_at has passed.
    """
    pool = await db.get_pool()
    assert pool
    claimed = await pool.fetchval(
        "update sweep_state set last_run_at = now() where id = 1 and last_run_at < now() - $1::interval"
        " returning last_run_at", SWEEP_EVERY)
    if not claimed:
        nxt = await pool.fetchval("select last_run_at + $1::interval from sweep_state where id = 1", SWEEP_EVERY)
        return {"ran": False, "reason": "a sweep ran recently", "next_sweep_after": nxt}
    due = await pool.fetch(
        # Simulated demo orders move only when someone presses "Advance", so the background sweep skips them
        # (they would otherwise spend the AI budget judges need).
        "select id from orders where watch_status='watching' and demo_scenario is null"
        " and (next_check_at is null or next_check_at <= now())"
        " order by next_check_at nulls first limit $1", limit)
    results = []
    for r in due:
        try:
            res = await check(r["id"], trigger="scheduled")
            results.append({"order_id": str(r["id"]), "risk_level": res.get("risk_level"), "watch_status": res.get("watch_status")})
        except Exception as e:  # one bad order must not stop the sweep
            results.append({"order_id": str(r["id"]), "error": type(e).__name__})
    watching = await pool.fetchval("select count(*) from orders where watch_status='watching'")
    return {"ran": True, "checked": len(results), "still_watching": watching, "results": results}
