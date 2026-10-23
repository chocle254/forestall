"""Delivery simulator: the PayPal sandbox has no carrier data, so we script it.

A scenario is a delivery story: carrier scans and buyer messages at set hours
after payment. Once an order is shipped with a scenario, events are revealed
only as (simulated) time reaches them, so the watcher sees the story unfold.
"""

import random
from datetime import datetime, timedelta

# (hours after payment, status, location)
SCENARIOS: dict[str, dict] = {
    "on_track": {
        "label": "On track: tracking on PayPal, delivered in 3 days",
        "attach_tracking": True,
        "scans": [(5, "label_created", "Austin, TX"), (20, "in_transit", "Dallas, TX hub"),
                  (52, "out_for_delivery", "Denver, CO"), (70, "delivered", "Denver, CO")],
        "messages": [],
    },
    "no_tracking": {
        "label": "Seller forgets to add tracking to PayPal",
        "attach_tracking": False,
        "scans": [(6, "label_created", "Austin, TX"), (22, "in_transit", "Dallas, TX hub"),
                  (49, "in_transit", "Kansas City, MO hub"), (75, "out_for_delivery", "Omaha, NE"),
                  (80, "delivered", "Omaha, NE")],
        "messages": [],
    },
    "stalled": {
        "label": "Parcel gets stuck at a hub",
        "attach_tracking": True,
        "scans": [(5, "label_created", "Austin, TX"), (21, "in_transit", "Memphis, TN hub"),
                  (27, "in_transit", "Memphis, TN hub")],
        "messages": [(130, "It's been almost a week and tracking still shows Memphis. Where is my order?")],
    },
    "buyer_concern": {
        "label": "Buyer gets worried before delivery",
        "attach_tracking": True,
        "scans": [(6, "label_created", "Austin, TX"), (23, "in_transit", "Memphis, TN hub")],
        "messages": [(92, "Hi, I ordered 4 days ago and the tracking hasn't changed since Tuesday. "
                          "Is my package lost? I needed it for a birthday this weekend.")],
    },
    "delivered_not_received": {
        "label": "Carrier says delivered, buyer says no",
        "attach_tracking": True,
        "scans": [(5, "label_created", "Austin, TX"), (20, "in_transit", "Dallas, TX hub"),
                  (60, "out_for_delivery", "Chicago, IL"), (68, "delivered", "Chicago, IL")],
        "messages": [(81, "Tracking says delivered yesterday but there is nothing here. "
                          "I checked with my neighbours and the building's mailroom.")],
    },
}


def tracking_number() -> str:
    return "9400" + "".join(random.choice("0123456789") for _ in range(18))


def label_hour(scenario: str) -> int:
    return SCENARIOS[scenario]["scans"][0][0]


def due_events(scenario: str, captured: datetime, now: datetime, scans_done: int, msgs_done: int):
    """Scans and messages from the script whose time has come but aren't recorded yet."""
    s = SCENARIOS[scenario]
    scans = [(captured + timedelta(hours=h), st, loc) for h, st, loc in s["scans"][scans_done:]
             if captured + timedelta(hours=h) <= now]
    msgs = [(captured + timedelta(hours=h), body) for h, body in s["messages"][msgs_done:]
            if captured + timedelta(hours=h) <= now]
    return scans, msgs


def next_event_time(scenario: str, captured: datetime, scans_done: int, msgs_done: int) -> datetime | None:
    s = SCENARIOS[scenario]
    times = [captured + timedelta(hours=h) for h, *_ in s["scans"][scans_done:]]
    times += [captured + timedelta(hours=h) for h, _ in s["messages"][msgs_done:]]
    return min(times) if times else None
