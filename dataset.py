"""Loader over the bundled Nigerian solar price dataset (dataset/solar_prices.json).

The dataset is static and shipped in the repo, so the app never calls the internet at
runtime. These helpers return median, vendor-grounded prices that catalog.py builds on.
Refresh by re-running the research and overwriting solar_prices.json; everything below
recomputes automatically. Every figure traces back to a sourced row with a date.
"""
from __future__ import annotations

import json
import pathlib
import statistics

_PATH = pathlib.Path(__file__).resolve().parent / "dataset" / "solar_prices.json"
DATA = json.loads(_PATH.read_text(encoding="utf-8"))

PANELS = DATA["panels"]
INVERTERS = DATA["inverters"]
BATTERIES = DATA["batteries"]
SYSTEMS = DATA["systems"]
VENDORS = DATA["vendors"]
GENERATORS = DATA.get("generators", [])
META = DATA["meta"]


def pick_generator(peak_w, daily_kwh):
    """Cheapest portable solar generator that can run the peak load (output >= peak)."""
    cands = [g for g in GENERATORS
             if isinstance(g.get("w"), (int, float)) and g["w"] >= peak_w * 1.1
             and isinstance(g.get("price_ngn"), (int, float)) and g["price_ngn"] > 0]
    return min(cands, key=lambda g: g["price_ngn"]) if cands else None


def _nums(xs):
    return [x for x in xs if isinstance(x, (int, float))]


def _median(xs):
    xs = _nums(xs)
    return statistics.median(xs) if xs else 0


def panel_price(watts: int) -> int:
    """Median price (NGN) for panels at that wattage; else the per-watt median scaled."""
    same = [p["price_ngn"] for p in PANELS if p["watts"] == watts]
    if _nums(same):
        return int(_median(same))
    ppw = _median([p["price_ngn"] / p["watts"] for p in PANELS if isinstance(p["price_ngn"], (int, float))])
    return int(round(ppw * watts / 1000.0) * 1000)


def inverter_price(kva: float) -> int:
    """Median price (NGN) for inverters near that kVA; else the nearest-rated unit."""
    same = [i["price_ngn"] for i in INVERTERS if abs(i["kva"] - kva) < 0.3]
    if _nums(same):
        return int(_median(same))
    near = sorted((i for i in INVERTERS if isinstance(i["price_ngn"], (int, float))),
                  key=lambda i: abs(i["kva"] - kva))
    return int(near[0]["price_ngn"]) if near else 0


def battery_ngn_per_kwh(chemistry: str = "lithium") -> int:
    """Median price per kWh (NGN). chemistry: 'lithium' (LiFePO4) or 'tubular'."""
    key = "LiFePO4" if chemistry == "lithium" else "tubular"
    return int(_median([b["price_ngn"] / b["kwh"] for b in BATTERIES
                        if b.get("chemistry") == key and b.get("kwh")]))


def battery_price(kwh: float, chemistry: str = "lithium") -> int:
    """Median price (NGN) of real units near that capacity; else per-kWh median scaled.
    Near-capacity median avoids the per-kWh figure being skewed by premium modular brands."""
    key = "LiFePO4" if chemistry == "lithium" else "tubular"
    near = [b["price_ngn"] for b in BATTERIES
            if b.get("chemistry") == key and b.get("kwh") and abs(b["kwh"] - kwh) <= 1.5]
    if _nums(near):
        return int(_median(near))
    return int(round(battery_ngn_per_kwh(chemistry) * kwh / 1000.0) * 1000)


def panel_ngn_per_watt() -> int:
    return int(_median([p["price_ngn"] / p["watts"] for p in PANELS if isinstance(p["price_ngn"], (int, float))]))
