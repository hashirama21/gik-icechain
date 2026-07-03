# EM-DAT East Africa flood events

`east_africa_floods.csv` is a **curated** flood-event table in EM-DAT export
schema, used by `gik_icechain.risk.cpt_refinement` (CPT refinement + validation).

## Provenance / status
This is **not** the official EM-DAT export. It is a curated subset of
well-documented East Africa flood events (2023-10 → 2024-12) compiled from
public situation reports (OCHA, IFRC, ReliefWeb), mapped to the admin-1 pcodes
used in `data/admin_boundaries/east_africa_admin1.geojson` (`ISO3_Name` form).
Death/affected figures are event-level approximations and may differ from the
final EM-DAT records.

**To use the authoritative data:** export from https://www.emdat.be
(Disaster Type = Flood, Region = Africa), keep the columns
`DisNo., Disaster Type, ISO, Country, Admin1, Admin1 Code, Start Date,
End Date, Total Deaths, No. Affected`, map `Admin1 Code` to the boundary
pcodes, and overwrite `east_africa_floods.csv`.

## Schema (columns required by the loader)
`DisNo., Disaster Type, ISO, Country, Admin1, Admin1 Code, Start Date,
End Date, Total Deaths, No. Affected` - one row per (event × admin-1 unit);
rows of the same event share `DisNo.`.
