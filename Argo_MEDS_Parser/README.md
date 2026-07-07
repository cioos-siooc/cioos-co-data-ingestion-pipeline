# Argo_MEDS_Parser

Mirrors Argo Canada profile NetCDF files from the Argo GDAC and publishes them
to the CIOOS National ERDDAP as the `ARGO_MEDS` dataset. Third pipeline in this
repo, same conventions as `MEDS_Buoy_Parser` (scheduled Prefect batch flow).
See the [root README](../README.md) for full stack setup.

This directory also demos **two integration versions** for Argo (see
[Two versions](#two-versions-federate-vs-mirror) below): federating Ifremer's
global `ArgoFloats` ERDDAP dataset, and mirroring the Canadian GDAC files
locally. Both ship as `erddap_config/` fragments.

## Background: where Argo Canada data lives

MEDS (DFO's Marine Environmental Data Section) is Canada's **Argo Data Assembly
Centre** (DAC, data-centre code `ME`): it runs the real-time and delayed-mode QC
and submits the authoritative files. The archive of record is the `dac/meds/`
tree at the two **Global Data Assembly Centres** (byte-identical, synced daily):

- Ifremer/Coriolis: `https://data-argo.ifremer.fr/dac/meds/`
- US GODAE (US Navy): `https://usgodae.org/pub/outgoing/argo/dac/meds/`

Each float directory holds an aggregated `<WMO>_prof.nc` (all cycles, all core
parameters, R/D QC modes rolled up) — the natural per-float harvest unit.
~900 Canadian floats, ~2 MB each.

## What it does (`app/flow.py`)

1. **download** (`argo_download.py`) — scrape the GDAC `dac/meds/` index for
   float WMO ids, mirror each float's `<WMO>_prof.nc` with conditional GETs
   (`If-Modified-Since`), so unchanged floats cost one 304 each.
2. **publish** — copy the NetCDF files to `datasets/ARGO_MEDS/`, where the
   `ARGO_MEDS` fragment (EDDTableFromMultidimNcFiles) serves them.

No fix step: GDAC files are already CF-compliant Argo NetCDF. All
CIOOS/CDE-specific reshaping happens in the ERDDAP fragment's `addAttributes`.

## Two versions: federate vs. mirror

| | v1 federate (`ArgoFloats_Ifremer.xml`) | v2 mirror (`ARGO_MEDS.xml` + this parser) |
| --- | --- | --- |
| Mechanism | `EDDTableFromErddap` redirect to Ifremer | Local files, `EDDTableFromMultidimNcFiles` |
| Scope | **Entire global Argo** — cannot subset | Canadian (MEDS DAC) floats only |
| Data hosting | None | ~2 GB for the full DAC |
| Availability | Depends on Ifremer uptime (observed slow) | Local |
| Metadata control | None (`addAttributes` forbidden) | Full (CDE-compatible attrs baked in) |
| CDE harvestable | No — Ifremer advertises `TrajectoryProfile`, which cde_harvester skips | Yes — advertised as `TimeSeriesProfile` |

**Why v1 can't be Canada-only:** per the ERDDAP docs, `EDDTableFromErddap` is a
pure redirect mirror (no `addAttributes`, no constraints), and no ERDDAP dataset
type can bake a fixed row filter (e.g. `data_center="ME"`) over a remote server
(`defaultDataQuery` is only a form default). Subsetting requires owning the
files — which is v2. v1 is included as a working demo of the federation
mechanics and a national-server convenience link to the global dataset.

## Configuration

| Var | Default | Purpose |
| --- | --- | --- |
| `ARGO_CRON` | `0 7 * * *` | Daily schedule |
| `ARGO_FLOATS` | *(unset)* | Comma-separated WMO allowlist, e.g. `4902674,4902530` |
| `ARGO_FLOAT_LIMIT` | `10` | Demo default: first N floats. `0` = all (~900 floats, ~2 GB) |
| `ARGO_RUN_NOW` | *(unset)* | Run once and exit instead of serving on a schedule |
| `ARGO_GDAC_URL` | `https://data-argo.ifremer.fr` | GDAC mirror; alt: `https://usgodae.org/pub/outgoing/argo` |
| `ARGO_DATA_DIR` | `data` | Work dir for mirrored NetCDF |
| `ARGO_DATASETS_DIR` | `datasets` | National ERDDAP datasets dir (mounted in Docker) |
| `ARGO_DATASET_NAME` | `ARGO_MEDS` | Dataset subfolder + ERDDAP datasetID |

## Running it standalone

```sh
cd Argo_MEDS_Parser/app
uv run python tests.py                     # offline tests
ARGO_TEST_NETWORK=1 uv run python tests.py # + live single-float round trip
ARGO_RUN_NOW=1 ARGO_FLOAT_LIMIT=3 uv run python flow.py
```

## Production notes

- The GDACs also offer an rsync service (`vdmzrs.ifremer.fr`) — the better
  transport when mirroring the full DAC; this parser's HTTP conditional-GET
  mirror keeps the stack dependency-free and matches the MEDS CSV pattern.
- BGC floats additionally have `<WMO>_Sprof.nc` (synthetic biogeochemical
  profiles) — out of scope here, easy extension later.
- After new files land, ERDDAP picks them up via `updateEveryNMillis`/daily
  reload, or immediately with
  `docker compose exec erddap touch /erddapData/flag/ARGO_MEDS`.
