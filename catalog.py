"""Real Nigerian solar product catalog (curated June 2026 — the model reads THIS, it
does not invent prices). Bundled (not live-fetched) so the app stays self-contained and
compliant (the ≤32B model only retrieves + explains over this data).

Prices are typical 2025/26 ₦ bands from Nigerian vendors (Felicity, Luminous, Jumia,
solarenergysupplystores.com, maypatronic.com). Refresh with scripts/refresh_catalog.py
and confirm with a local vendor. Schema is flat so it doubles as a HF Dataset row set.
"""

import dataset  # bundled, sourced Nigerian price data (dataset/solar_prices.json)

# Representative products; every price comes from the bundled dataset (median per capacity)
# so the app's cost estimates stay grounded in real, sourced 2026 figures. To refresh,
# overwrite dataset/solar_prices.json and these recompute on import.
PANELS = [
    {"brand": "Generic", "model": "200W mono", "watt": 200, "type": "mono", "naira": dataset.panel_price(200)},
    {"brand": "Canadian Solar", "model": "300W mono", "watt": 300, "type": "mono", "naira": dataset.panel_price(300)},
    {"brand": "Jinko", "model": "450W mono", "watt": 450, "type": "mono", "naira": dataset.panel_price(450)},
    {"brand": "Jinko", "model": "550W mono half-cell", "watt": 550, "type": "mono", "naira": dataset.panel_price(550)},
]

INVERTERS = [
    {"brand": "Mercury", "model": "1.5kVA/24V", "kva": 1.5, "type": "hybrid MPPT", "naira": dataset.inverter_price(1.5)},
    {"brand": "Mercury", "model": "2.5kVA/24V", "kva": 2.5, "type": "hybrid MPPT", "naira": dataset.inverter_price(2.5)},
    {"brand": "Mercury", "model": "3.5kVA/24V", "kva": 3.5, "type": "hybrid MPPT", "naira": dataset.inverter_price(3.5)},
    {"brand": "Felicity", "model": "5kVA/48V", "kva": 5.0, "type": "hybrid MPPT", "naira": dataset.inverter_price(5.0)},
    {"brand": "Felicity", "model": "7.5kVA/48V", "kva": 7.5, "type": "hybrid MPPT", "naira": dataset.inverter_price(7.5)},
    {"brand": "Felicity", "model": "10kVA/48V", "kva": 10.0, "type": "hybrid MPPT", "naira": dataset.inverter_price(10.0)},
]

BATTERIES = [
    {"brand": "Quanta", "model": "Tubular 200Ah/12V", "chem": "tubular", "volt": 12, "ah": 200,
     "kwh": 2.4, "dod": 0.5, "life_yrs": 3, "naira": dataset.battery_price(2.4, "tubular")},
    {"brand": "Felicity", "model": "5kWh 48V LiFePO4", "chem": "lithium", "volt": 48, "ah": 100,
     "kwh": 5.0, "dod": 0.9, "life_yrs": 10, "naira": dataset.battery_price(5.0, "lithium")},
    {"brand": "Felicity", "model": "10kWh 48V LiFePO4", "chem": "lithium", "volt": 48, "ah": 200,
     "kwh": 10.0, "dod": 0.9, "life_yrs": 10, "naira": dataset.battery_price(10.0, "lithium")},
]

SOURCES = ("Bundled dataset/solar_prices.json: Jumia, Mercury Direct, Zit, SolarKobo, Maypatronic, "
           "Felicity vendors and Nigerian price guides (June 2026, median per capacity).")


def pick_panel(array_w):
    """Fewest panels (realistic installs use big panels), tie-break cheapest total."""
    import math
    scored = [(math.ceil(max(array_w, 1) / p["watt"]), math.ceil(max(array_w, 1) / p["watt"]) * p["naira"], p)
              for p in PANELS]
    scored.sort(key=lambda x: (x[0], x[1]))
    return scored[0][2]


def pick_inverter(required_kva):
    for inv in INVERTERS:
        if inv["kva"] >= required_kva:
            return inv
    return INVERTERS[-1]


def pick_battery(daily_kwh, autonomy_days, pref="lithium"):
    """Smallest set of real batteries covering the backup energy for the preference."""
    need_kwh = daily_kwh * autonomy_days
    cands = [b for b in BATTERIES if (b["chem"] == pref)] or BATTERIES
    import math
    best = None
    for b in cands:
        usable = b["kwh"] * b["dod"]
        count = max(1, math.ceil(need_kwh / usable))
        cost = count * b["naira"]
        if best is None or cost < best["cost"]:
            best = {"battery": b, "count": count, "cost": cost, "backup_kwh": round(count * usable, 1)}
    return best
