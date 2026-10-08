"""Single source of truth for Cedar Lane Auto Detailing's business rules."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Dict, Optional, Tuple

SHOP_NAME = "Cedar Lane Auto Detailing"
AGENT_NAME = "Alex"
ADDRESS = "1420 Cedar Lane, San Mateo, CA"


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    price: int
    duration_min: int
    description: str


SERVICES: Dict[str, Service] = {
    s.key: s
    for s in [
        Service("exterior_wash", "Exterior Wash", 40, 30,
                "Hand wash, wheels and tires, windows, tire shine."),
        Service("interior_detail", "Interior Detail", 120, 60,
                "Full vacuum, seat and carpet shampoo, all surfaces wiped and conditioned, interior glass."),
        Service("interior_detail_and_wash", "Interior Detail + Wash", 150, 60,
                "Our Interior Detail plus the Exterior Wash, done side by side by two techs."),
        Service("full_detail", "Full Detail", 250, 180,
                "Interior Detail plus exterior hand wash, clay bar, machine polish and wax."),
    ]
}

QUOTE_ONLY = {
    "ceramic_coating": "Ceramic coating starts at $800 and takes a full day. The owner quotes these personally after seeing the car.",
}

LARGE_VEHICLE_SURCHARGE = 25

HOURS: Dict[int, Optional[Tuple[time, time]]] = {
    0: (time(8), time(18)),
    1: (time(8), time(18)),
    2: (time(8), time(18)),
    3: (time(8), time(18)),
    4: (time(8), time(18)),
    5: (time(8), time(18)),
    6: None,
}

SERVICE_ALIASES = {
    "wash": "exterior_wash", "exterior": "exterior_wash", "car wash": "exterior_wash",
    "interior": "interior_detail", "interior detail": "interior_detail",
    "interior detail and wash": "interior_detail_and_wash", "interior and wash": "interior_detail_and_wash",
    "interior detail + wash": "interior_detail_and_wash",
    "full": "full_detail", "full detail": "full_detail",
}


def get_service(key: str) -> Service:
    k = (key or "").strip().lower()
    if k in SERVICES:
        return SERVICES[k]
    k2 = k.replace("_", " ").replace("&", "and")
    if k2 in SERVICE_ALIASES:
        return SERVICES[SERVICE_ALIASES[k2]]
    raise ValueError(f"Unknown service '{key}'. Valid services: {', '.join(SERVICES)}")


BUSINESS_INFO = {
    "services_and_pricing": "\n".join(
        [f"- {s.name} (key: {s.key}): ${s.price}, about {s.duration_min} min. {s.description}" for s in SERVICES.values()]
        + [f"- Ceramic coating: {QUOTE_ONLY['ceramic_coating']} (do not book; hand off to the owner)",
           f"- SUVs with 3 rows, full-size trucks and vans: +${LARGE_VEHICLE_SURCHARGE} on any service."]
    ),
    "hours": "Monday to Saturday, 8 AM to 6 PM. Closed Sunday. Last Full Detail starts at 3 PM.",
    "location": f"{ADDRESS}. Free parking out front; there's a small waiting lounge with wifi and coffee.",
    "vehicles": ("We do cars, SUVs, pickup trucks and minivans/passenger vans. "
                 "We do NOT service boats, RVs/motorhomes, motorcycles or commercial semis."),
    "prep": ("Please remove personal items and valuables, empty the trunk if you want it cleaned, "
             "and arrive about 5 minutes early. Let us know ahead of time about heavy pet hair, "
             "smoke odor, mold or biohazard (vomit, blood); those may cost extra and need the owner's OK."),
    "policies": ("Payment by card or cash at pickup. No cancellation fee, but please give us as much notice as you can. "
                 "If you're more than 15 minutes late we may need to shorten the service or reschedule. "
                 "You can wait in the lounge or drop the car off; we'll text when it's ready."),
}


def business_info_text() -> str:
    return "\n\n".join(f"## {k.replace('_', ' ').title()}\n{v}" for k, v in BUSINESS_INFO.items())
