# Nigerian Solar Price Dataset (bundled)

`solar_prices.json` is a static, sourced snapshot of the Nigerian solar market, compiled
**2026-06-07**. It is shipped in the repo so the app never calls the internet at runtime,
which keeps it self-contained and competition-compliant (the small model retrieves and
explains over this data; it never invents a price).

## Contents
| Category | Rows | Span |
|---|---|---|
| `panels` | 35 | 150W to 600W, mono |
| `inverters` | 28 | 1 to 10 kVA, pure sine / hybrid MPPT |
| `batteries` | 20 | LiFePO4 (2.4 to 15 kWh) and tubular (200/220Ah) |
| `systems` | 18 | full bundles, 1 to 10 kVA |
| `vendors` | 20 | retailers, installers, PAYG and lease providers |

Every row carries `source_url`, `date_seen`, and a `confidence` tag:
`high` = exact price from a live product page, `medium` = vendor category or dated price
list, `low` = a sourced range midpoint or premium-brand estimate.

## Where it came from
Compiled from five parallel research streams over Nigerian sources: Jumia, Konga, vendor
sites (Arnergy, Mercury Direct, Zit, SolarKobo, Maypatronic, Me3 Energy, StellarMart,
Kara, naturesolar, SMK Solar, EnergyMall, Solar Depot, Cloud Energy) and reputable price
guides (Techpoint, Solarlify, NaijaTechGuide). See `meta.sources`.

## Benchmarks (from the data)
- Panels: about **₦173 to ₦289 per watt** (large 450W to 600W panels are cheaper per watt).
- Lithium LiFePO4: about **₦230k to ₦320k per kWh** (mainstream brands cluster ₦200k to ₦250k; Pylontech is a premium).
- Tubular lead-acid: about **₦70k to ₦175k per nominal kWh** (but ~50% usable and a 3 to 5 year life).
- Home sweet spot: **3.5 to 5 kVA** for a typical 3-bedroom home.

## How the app uses it
`dataset.py` loads the JSON and exposes median, capacity-aware prices
(`panel_price`, `inverter_price`, `battery_price`). `catalog.py` builds its representative
products from those, so the sizing engine quotes real, current prices. Update prices by
overwriting `solar_prices.json`; `catalog.py` recomputes on import, no code change needed.

## Caveats
Nigerian prices move with the USD/NGN rate (about 1,500 to 1,600 per USD in 2026), since
most hardware is imported, and the same model varies widely by vendor. Treat every figure
as a dated snapshot and re-verify with a local vendor before quoting an end user. The
`vendors` list is a directory, not an endorsement, and is not a price source for those
that quote on request.
