# HANDOFF — Tuinscan

## Status (2026-11-25)
- PoC build plan written (PDOK values pinned): `docs/superpowers/plans/2026-11-25-tuinscan-poc.md` (16 tasks). No code yet.
- Spec: "Specsheet Tuinscan" doc — https://claude.ai/artifact/H4Wmv6QW3E4iU3wH7tZ94X

## Key decisions
- Tobias column names unknown → code only uses canonical field names; mapping lives in `invoer/kolommen.yaml`, drafted by `tuinscan inspecteer` (value patterns such as 16-digit BAG-id/postcode beat header names).
- `tuinscan voorbeeld` builds a synthetic export from BAG so the full chain runs before Tobias delivers.
- Complex perceel split: Voronoi on per-house seeds (BAG often has one pand per row, so no party walls in the data). QGIS correction via `invoer/tuincorrecties.gpkg`.
- Voor/achter: dot product against the direction to the nearest BGT wegdeel. `tuin.delen` default `[voor]` until FG decides.
- Missing signal = 0 contribution + "ontbrekend" flag (never inflates the score).
- Added Pillow (thumbnails, outlines) on top of the spec's package list.
- PDOK verified 2026-11-25 (`docs/pdok-bronnen.md`): `ortho25`/`ortho25IR` = summer (leaf-on), `orthoHR` = winter. Summer CIR exists (spec says it doesn't) → vegetation mask uses summer NDVI + ExG; NDVI start threshold 0.2.
- BAG vbo → pand via `pand.href` ↔ pand feature id (no `pandidentificatie` field).
- AHN: only "actueel" on PDOK, no previous version → S4a "nieuw t.o.v. vorige AHN" not possible in PoC.

## Open issues
- Task 6 (laptop only): `/vsicurl/` and PDOK behind the corporate proxy; QGIS alignment check for the real wijk.

## Next steps
1. Review plan, choose execution method.
2. Tasks 1–5 (can run in cloud, tests are offline).
3. Task 6 on the laptop, then Tasks 7–15, then Task 16 (real run).
