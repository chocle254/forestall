"""Simulated carrier: the PayPal sandbox has no carrier data, so we script it.

A scenario is a delivery story: carrier scans, carrier exceptions, hub
disruptions, proof of delivery and buyer messages, each at a set number of
hours after payment. Once an order is shipped with a scenario, everything is
revealed only as (simulated) time reaches it, so the watcher and the
investigating agent see the story unfold like real carrier data.

"{dest}" in a location is replaced by the buyer's city from the PayPal address.
In production this module is replaced by a carrier/tracking API (USPS, AfterShip...).
"""

import random
from datetime import datetime, timedelta

SOURCE = "simulated carrier (PayPal sandbox has no carrier data)"
MEMPHIS_BACKLOG = ("Memphis, TN hub",
                   "Sorting backlog after an equipment outage at the Memphis Network Distribution Center. "
                   "USPS estimates 3 to 5 extra days for parcels routed through Memphis. Parcels are not lost; "
                   "they are processed in arrival order.")

# scans: (hours after payment, status, location)
SCENARIOS: dict[str, dict] = {
    "on_track": {
        "label": "On track: tracking on PayPal, delivered in 3 days",
        "attach_tracking": True,
        "expected_delivery_h": 72,
        "scans": [(5, "label_created", "Austin, TX"), (20, "in_transit", "Dallas, TX hub"),
                  (52, "out_for_delivery", "{dest}"), (70, "delivered", "{dest}")],
        "exceptions": [],
        "network": [],
        "delivery_proof": {"method": "Handed to resident", "signature_captured": True, "photo": False,
                           "gps_distance_from_address_m": 9},
        "messages": [],
    },
    "no_tracking": {
        "label": "Seller forgets to add tracking to PayPal",
        "attach_tracking": False,
        "expected_delivery_h": 80,
        "scans": [(6, "label_created", "Austin, TX"), (22, "in_transit", "Dallas, TX hub"),
                  (49, "in_transit", "Kansas City, MO hub"), (75, "out_for_delivery", "{dest}"),
                  (80, "delivered", "{dest}")],
        "exceptions": [],
        "network": [],
        "delivery_proof": {"method": "Left in mailbox", "signature_captured": False, "photo": True,
                           "gps_distance_from_address_m": 14},
        "messages": [],
    },
    "stalled": {
        "label": "Parcel gets stuck at a hub",
        "attach_tracking": True,
        "expected_delivery_h": 72,
        "scans": [(5, "label_created", "Austin, TX"), (21, "in_transit", "Memphis, TN hub"),
                  (27, "arrived_at_hub", "Memphis, TN hub"), (150, "departed_hub", "Memphis, TN hub"),
                  (170, "out_for_delivery", "{dest}"), (176, "delivered", "{dest}")],
        "exceptions": [(50, "PROCESSING_DELAY", "Delayed: processing at Memphis, TN Network Distribution Center")],
        "network": [(24, *MEMPHIS_BACKLOG)],
        "delivery_proof": {"method": "Left at front door", "signature_captured": False, "photo": True,
                           "gps_distance_from_address_m": 11},
        "messages": [(130, "It's been almost a week and tracking still shows Memphis. Where is my order?")],
    },
    "buyer_concern": {
        "label": "Buyer gets worried before delivery",
        "attach_tracking": True,
        "expected_delivery_h": 72,
        "scans": [(6, "label_created", "Austin, TX"), (23, "arrived_at_hub", "Memphis, TN hub"),
                  (120, "departed_hub", "Memphis, TN hub"), (140, "out_for_delivery", "{dest}"),
                  (146, "delivered", "{dest}")],
        "exceptions": [(48, "PROCESSING_DELAY", "Delayed: processing at Memphis, TN Network Distribution Center")],
        "network": [(20, *MEMPHIS_BACKLOG)],
        "delivery_proof": {"method": "Handed to resident", "signature_captured": True, "photo": False,
                           "gps_distance_from_address_m": 7},
        "messages": [(92, "Hi, I ordered 4 days ago and the tracking hasn't changed since Tuesday. "
                          "Is my package lost? I needed it for a birthday this weekend.")],
    },
    "delivered_not_received": {
        "label": "Carrier says delivered, buyer says no",
        "attach_tracking": True,
        "expected_delivery_h": 72,
        "scans": [(5, "label_created", "Austin, TX"), (20, "in_transit", "Dallas, TX hub"),
                  (60, "out_for_delivery", "{dest}"), (68, "delivered", "{dest}")],
        "exceptions": [],
        "network": [],
        "delivery_proof": {
            "method": "Left at front door", "signature_captured": False, "photo": True,
            "photo_description": "Parcel on a doorstep beside a red door numbered 1424; no building entrance or "
                                 "apartment buzzer visible.",
            "gps_distance_from_address_m": 380,
        },
        "messages": [(81, "Tracking says delivered yesterday but there is nothing here. "
                          "I checked with my neighbours and the building's mailroom.")],
    },
}


def tracking_number() -> str:
    return "9400" + "".join(random.choice("0123456789") for _ in range(18))


def label_hour(scenario: str) -> int:
    return SCENARIOS[scenario]["scans"][0][0]


def dest_of(ship_to: dict | None) -> str:
    if ship_to and ship_to.get("admin_area_2"):
        return f"{ship_to['admin_area_2']}, {ship_to.get('admin_area_1', '')}".strip(", ")
    return "Destination city"


def due_events(scenario: str, captured: datetime, now: datetime, scans_done: int, msgs_done: int,
               ship_to: dict | None = None):
    """Scans and messages from the script whose time has come but aren't recorded yet."""
    s, dest = SCENARIOS[scenario], dest_of(ship_to)
    scans = [(captured + timedelta(hours=h), st, loc.replace("{dest}", dest)) for h, st, loc in s["scans"][scans_done:]
             if captured + timedelta(hours=h) <= now]
    msgs = [(captured + timedelta(hours=h), body) for h, body in s["messages"][msgs_done:]
            if captured + timedelta(hours=h) <= now]
    return scans, msgs


def next_event_time(scenario: str, captured: datetime, scans_done: int, msgs_done: int) -> datetime | None:
    s = SCENARIOS[scenario]
    times = [captured + timedelta(hours=h) for h, *_ in s["scans"][scans_done:]]
    times += [captured + timedelta(hours=h) for h, _ in s["messages"][msgs_done:]]
    return min(times) if times else None


# --- The "carrier API" the investigating agent calls ---------------------------

def carrier_tracking(order, scans: list, now: datetime) -> dict:
    """What a carrier tracking API would return for this parcel right now."""
    out = {
        "source": SOURCE,
        "carrier": order["carrier"],
        "tracking_number": order["tracking_number"],
        "scans": [{"status": e["status"], "location": e["location"], "time": e["occurred_at"].isoformat()} for e in scans],
        "latest_status": scans[-1]["status"] if scans else "no_scans",
    }
    scenario = order["demo_scenario"]
    if not scenario:
        out["note"] = "No live carrier connection in the sandbox; only scans recorded in Forestall are shown."
        return out
    s, captured = SCENARIOS[scenario], order["captured_at"]
    expected = captured + timedelta(hours=s["expected_delivery_h"])
    out["expected_delivery"] = expected.isoformat()
    out["expected_delivery_passed"] = now > expected and out["latest_status"] != "delivered"
    out["exceptions"] = [{"code": c, "text": t, "time": (captured + timedelta(hours=h)).isoformat()}
                         for h, c, t in s["exceptions"] if captured + timedelta(hours=h) <= now]
    if out["latest_status"] == "delivered":
        out["proof_of_delivery"] = s["delivery_proof"]
    return out


def network_status(order, location: str, now: datetime) -> dict:
    """Known disruptions at a carrier facility (what a carrier service-alerts feed would say)."""
    scenario = order["demo_scenario"]
    alerts = []
    if scenario:
        captured = order["captured_at"]
        loc = location.lower()
        for h, hub, note in SCENARIOS[scenario]["network"]:
            if captured + timedelta(hours=h) <= now and (hub.lower() in loc or loc.split(",")[0] in hub.lower()):
                alerts.append({"facility": hub, "alert": note})
    return {"source": SOURCE, "location": location, "alerts": alerts or "No active service alerts for this facility."}
