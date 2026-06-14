"""Location-aware solar data — peak sun-hours (PSH) by Nigerian state/zone.

PSH rises northward (more sun, less rain), so the SAME appliances need fewer panels up
North than in the South. Real, zone-based values (curated, no cloud) → accurate sizing.
Browser geolocation (in the app) gives lat/lon → psh_from_lat(); the dropdown uses states.
"""

ZONE_PSH = {
    "Far North": 6.0, "North": 5.6, "Middle Belt": 5.2,
    "South West": 4.6, "South East": 4.4, "South South": 4.2,
}

STATE_ZONE = {
    # Far North
    "Sokoto": "Far North", "Kebbi": "Far North", "Zamfara": "Far North", "Katsina": "Far North",
    "Kano": "Far North", "Jigawa": "Far North", "Yobe": "Far North", "Borno": "Far North",
    # North
    "Kaduna": "North", "Bauchi": "North", "Gombe": "North", "Adamawa": "North", "Niger": "North",
    # Middle Belt
    "FCT (Abuja)": "Middle Belt", "Plateau": "Middle Belt", "Nasarawa": "Middle Belt",
    "Benue": "Middle Belt", "Kogi": "Middle Belt", "Kwara": "Middle Belt", "Taraba": "Middle Belt",
    # South West
    "Lagos": "South West", "Ogun": "South West", "Oyo": "South West", "Osun": "South West",
    "Ondo": "South West", "Ekiti": "South West",
    # South East
    "Enugu": "South East", "Anambra": "South East", "Imo": "South East", "Abia": "South East",
    "Ebonyi": "South East",
    # South South
    "Rivers": "South South", "Bayelsa": "South South", "Delta": "South South", "Edo": "South South",
    "Cross River": "South South", "Akwa Ibom": "South South",
}
STATES = sorted(STATE_ZONE)
DEFAULT_STATE = "Lagos"


def psh_for_state(state):
    return ZONE_PSH.get(STATE_ZONE.get(state, ""), 4.6)


def zone_for_state(state):
    return STATE_ZONE.get(state, "South West")


def psh_from_lat(lat):
    """Nigeria spans ~4.3°N (south) to ~13.9°N (north). PSH ~4.2→6.0 northward."""
    try:
        lat = float(lat)
    except (TypeError, ValueError):
        return 4.6
    return round(max(4.2, min(6.0, 4.2 + (lat - 4.3) * (6.0 - 4.2) / (13.9 - 4.3))), 1)


def nearest_state_from_lat(lat):
    """Rough: pick a representative state for the latitude band (for display)."""
    bands = [(12.5, "Kano"), (10.5, "Kaduna"), (9.0, "FCT (Abuja)"), (7.5, "Ilorin/Kwara"),
             (6.5, "Lagos"), (0, "Port Harcourt/Rivers")]
    try:
        lat = float(lat)
    except (TypeError, ValueError):
        return DEFAULT_STATE
    for thr, name in bands:
        if lat >= thr:
            return name
    return "Rivers"
