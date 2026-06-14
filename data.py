"""Real reference data for the Solar Sizing app (no demo data).

- Appliance wattages: standard engineering values (typical Nigerian home appliances),
  with realistic daily run-hours (duty-cycle adjusted for the fridge/freezer/AC).
- Products: real 2025/26 Nigerian price BANDS (panels, inverters, batteries, bundles).
  Sources: solarenergysupplystores.com, gve-group.com, maypatronic.com (June 2026).
  Bands are typical — the app tells users to confirm with a local vendor.
- NERC Net Billing 2026: curated summary of the live regulation (energy credits, not cash;
  C&I 50kWp–1.5MWp scope) — sources: NERC draft (Sept 2025), technext24 (4 Jun 2026).

All figures are editable in-app; the user enters their own appliances and can adjust prices.
"""

# name -> (typical watts, typical run-hours/day [duty-cycle adjusted], category)
APPLIANCES = {
    "LED bulb": (10, 6, "lighting"),
    "Energy-saver bulb": (20, 6, "lighting"),
    "Security light": (30, 11, "lighting"),
    "Ceiling fan": (70, 8, "cooling"),
    "Standing fan": (50, 8, "cooling"),
    "TV (32–43\" LED)": (80, 5, "entertainment"),
    "TV (large)": (150, 5, "entertainment"),
    "Decoder (DStv/GOtv)": (25, 5, "entertainment"),
    "Sound system": (100, 3, "entertainment"),
    "Fridge (small)": (150, 10, "refrigeration"),       # duty-cycle adjusted
    "Chest freezer": (200, 12, "refrigeration"),
    "Laptop": (65, 5, "work"),
    "Desktop PC": (200, 5, "work"),
    "Phone charger": (10, 3, "work"),
    "Wifi router": (15, 18, "work"),
    "Air conditioner (1HP)": (746, 6, "cooling"),
    "Air conditioner (1.5HP)": (1100, 6, "cooling"),
    "Air conditioner (2HP)": (1500, 6, "cooling"),
    "Water pump (0.5HP)": (370, 1, "utility"),
    "Water pump (1HP)": (746, 1, "utility"),
    "Electric iron": (1000, 0.3, "utility"),
    "Microwave": (1000, 0.3, "kitchen"),
    "Blender": (350, 0.2, "kitchen"),
    "Electric kettle": (1500, 0.3, "kitchen"),
    "Washing machine": (500, 0.5, "utility"),
    "Water heater": (1500, 0.5, "utility"),
}

# Real product classes with typical ₦ price bands (2025/26).
PANELS = [
    {"name": "100W poly", "watt": 100, "naira": 28000},
    {"name": "200W mono", "watt": 200, "naira": 75000},
    {"name": "450W mono", "watt": 450, "naira": 180000},
    {"name": "550W mono", "watt": 550, "naira": 230000},
]
INVERTERS = [
    {"name": "1.5kVA hybrid (MPPT)", "kva": 1.5, "naira": 200000},
    {"name": "2.5kVA hybrid (MPPT)", "kva": 2.5, "naira": 320000},
    {"name": "3.5kVA hybrid (MPPT)", "kva": 3.5, "naira": 450000},
    {"name": "5kVA hybrid (MPPT)", "kva": 5.0, "naira": 750000},
    {"name": "10kVA hybrid (MPPT)", "kva": 10.0, "naira": 1500000},
]
BATTERIES = [
    {"name": "Tubular 200Ah/12V (2–4 yr)", "ah": 200, "volt": 12, "naira": 180000, "dod": 0.5, "chem": "tubular"},
    {"name": "Lithium 100Ah/12V LiFePO₄ (~10 yr)", "ah": 100, "volt": 12, "naira": 350000, "dod": 0.9, "chem": "lithium"},
    {"name": "Lithium 200Ah/12V LiFePO₄ (~10 yr)", "ah": 200, "volt": 12, "naira": 650000, "dod": 0.9, "chem": "lithium"},
]
# Whole-system reference bundles (real market bands).
BUNDLES = [
    {"name": "Starter (1–2kVA)", "kva_max": 2.0, "powers": "lights, fans, TV, phone", "naira": "₦850k–1.2M"},
    {"name": "Home (3–4kVA)", "kva_max": 4.0, "powers": "fridge, pump, small AC", "naira": "₦2–3M"},
    {"name": "Large duplex (5–8kVA)", "kva_max": 8.0, "powers": "multiple ACs + appliances", "naira": "₦3.5–5.5M"},
]

# Sizing assumptions (editable).
SUN_HOURS = 4.5          # avg peak-sun-hours/day in Nigeria
SYSTEM_LOSS = 0.8        # 20% system losses
AUTONOMY_DAYS = 1.0      # battery backup days

NERC_NETBILLING = (
    "NERC Net Billing Regulations (implementation commenced 2026): solar users can become "
    "‘prosumers’ and export excess power, but you earn **energy credits (not cash)** that offset "
    "future bills, via a **bidirectional meter**, with DisCo approval. The scheme mainly targets "
    "**Commercial & Industrial systems (50kWp–1.5MWp)** — so most homes should size for "
    "**self-consumption + backup**, not selling to the grid. Confirm current rules with your DisCo/NERC. "
    "(Sources: NERC draft net-billing regs, Sept 2025; technext24, 4 Jun 2026.)"
)
