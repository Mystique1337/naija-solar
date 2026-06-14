"""Deterministic solar sizing engine — location-aware, over the real catalog.

All math in Python (the honest core). Uses the user's location peak-sun-hours and the
real product catalog; the ≤32B model only narrates over these results.
"""
from __future__ import annotations

import math

import catalog
from data import APPLIANCES, AUTONOMY_DAYS, SYSTEM_LOSS

PF = 0.8           # inverter power factor
DIVERSITY = 0.8    # not everything runs continuously at once
HEADROOM = 1.2     # inverter sizing margin
SURGE_RATIO = 2.0  # inverters tolerate ~2x their rating for a few seconds (startup)
# Startup surge multiple for motor/compressor loads (they pull 2-3x running on start).
SURGE = {"ac": 3.0, "fridge": 3.0, "freezer": 3.0, "pump": 3.0, "washing": 2.5, "microwave": 1.5}


def _surge_factor(name: str) -> float:
    n = name.lower()
    if "air condition" in n:
        return SURGE["ac"]
    if "fridge" in n or "refriger" in n:
        return SURGE["fridge"]
    if "freezer" in n:
        return SURGE["freezer"]
    if "pump" in n:
        return SURGE["pump"]
    if "washing" in n:
        return SURGE["washing"]
    if "microwave" in n:
        return SURGE["microwave"]
    return 1.0


def load_profile(selection: dict):
    items, daily_wh, peak_w, surge_extra = [], 0.0, 0.0, 0.0
    for name, qty in selection.items():
        qty = int(qty or 0)
        if qty <= 0 or name not in APPLIANCES:
            continue
        watt, hours, cat = APPLIANCES[name]
        wh = watt * hours * qty
        items.append({"name": name, "qty": qty, "watt": watt, "hours": hours, "wh": round(wh), "category": cat})
        daily_wh += wh
        peak_w += watt * qty
        extra = watt * (_surge_factor(name) - 1.0)   # one unit's startup surge above its running watts
        if extra > surge_extra:
            surge_extra = extra
    return {"items": items, "daily_wh": round(daily_wh), "peak_w": round(peak_w), "surge_extra": round(surge_extra)}


def size(selection: dict, sun_hours: float = 4.6):
    prof = load_profile(selection)
    if not prof["items"]:
        return {"error": "No appliances selected."}
    daily_wh, peak_w = prof["daily_wh"], prof["peak_w"]
    daily_kwh = round(daily_wh / 1000, 2)

    # continuous diversified load, and the worst-case single motor-startup surge
    running_va = peak_w * DIVERSITY / PF
    surge_va = (peak_w + prof["surge_extra"]) / PF
    req_kva = round(max(running_va, surge_va / SURGE_RATIO) / 1000 * HEADROOM, 2)
    inv = catalog.pick_inverter(req_kva)

    array_w = round(daily_wh / (sun_hours * SYSTEM_LOSS))
    panel = catalog.pick_panel(array_w)
    n_panels = max(1, math.ceil(array_w / panel["watt"]))
    panels_cost = n_panels * panel["naira"]

    durable = catalog.pick_battery(daily_kwh, AUTONOMY_DAYS, "lithium")
    budget = catalog.pick_battery(daily_kwh, AUTONOMY_DAYS, "tubular")

    return {
        "profile": prof, "daily_kwh": daily_kwh, "peak_w": peak_w, "sun_hours": sun_hours,
        "array_w": array_w,
        "panel": {"name": f"{panel['brand']} {panel['model']}", "count": n_panels, "each_naira": panel["naira"]},
        "inverter": {"name": f"{inv['brand']} {inv['model']}", "kva": inv["kva"], "naira": inv["naira"], "required_kva": req_kva},
        "batteries": {
            "durable": {"name": f"{durable['battery']['brand']} {durable['battery']['model']}",
                        "count": durable["count"], "backup_kwh": durable["backup_kwh"],
                        "total": panels_cost + inv["naira"] + durable["cost"]},
            "budget": {"name": f"{budget['battery']['brand']} {budget['battery']['model']}",
                       "count": budget["count"], "backup_kwh": budget["backup_kwh"],
                       "total": panels_cost + inv["naira"] + budget["cost"]},
        },
        "recommended": "durable",
    }


def summary_text(r, state=None):
    if r.get("error"):
        return r["error"]
    d, b = r["batteries"]["durable"], r["batteries"]["budget"]
    loc = f" for **{state}** (~{r['sun_hours']} sun-hrs/day)" if state else ""
    return (
        f"Daily energy{loc}: **{r['daily_kwh']} kWh/day** · peak **{r['peak_w']} W**.\n\n"
        f"**Recommended system**\n"
        f"- ☀️ **{r['panel']['count']} × {r['panel']['name']}** (~{r['array_w']} W array)\n"
        f"- 🔌 **{r['inverter']['name']}** ({r['inverter']['kva']} kVA, needs ≥{r['inverter']['required_kva']})\n"
        f"- 🔋 **{d['count']} × {d['name']}** ({d['backup_kwh']} kWh backup)\n\n"
        f"💰 **₦{d['total']:,}** durable (lithium) · **₦{b['total']:,}** budget (tubular)\n\n"
        f"_Real catalog prices ({catalog.SOURCES}) — confirm with a licensed installer._"
    )
