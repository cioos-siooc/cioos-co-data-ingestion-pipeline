# MEDS_Buoy_Parser

Downloads the non-real-time DFO MEDS buoy CSV archive, fixes it up, and
publishes it to the CIOOS National ERDDAP as the `MEDS_CSV` dataset. Batch
counterpart to `ECCC_Buoy_Parser` in this repo — same conventions, daily
schedule instead of AMQP. See the [root README](../README.md) for full stack
setup and dataset-fragment generation.

## What it does (`app/flow.py`)

1. **download** (`meds_download.py`) — list MEDS's `CSVDATA/` index, download
   any new/changed `*_csv.zip`, extract, normalise headers (canonical 23-column
   header for `C*` buoys).
2. **fix** (`meds_fix.py`) — ISO 8601 UTC dates, longitude flipped to degrees
   east (raw values kept as `preciseLat`/`preciseLon`), fixed deployment
   lat/lon from `b_pw_inv.json`, all buoy types reindexed to one unified
   column set (historic buoys get empty wind/pressure/temperature cells).
3. **publish** — write per-station CSVs to `datasets/MEDS_CSV/`.

## Scope

| Prefix | Example | Variables |
| --- | --- | --- |
| `C*` | `C44131` | wave + wind + pressure + air/sea temperature |
| `MEDS*` | `MEDS210` | wave only |
| `WEL*` | `WEL233` | wave only |

## Configuration

| Var | Default | Purpose |
| --- | --- | --- |
| `MEDS_CRON` | `0 6 * * *` | Daily schedule |
| `MEDS_STATIONS` | *(all)* | Comma-separated station allowlist, e.g. `c46131,meds210` |
| `MEDS_RUN_NOW` | *(unset)* | Run once and exit instead of serving on a schedule |
| `MEDS_DATA_DIR` | `data` | Work dir for zips / extracted / fixed CSVs |
| `MEDS_DATASETS_DIR` | `datasets` | National ERDDAP datasets dir (mounted in Docker) |
| `MEDS_DATASET_NAME` | `MEDS_CSV` | Dataset subfolder + ERDDAP datasetID |

## Running it standalone

Via the stack: `docker compose up --build meds_buoy_parser` (registers the
`meds-daily` deployment; trigger it manually from the Prefect UI, or run once
locally without waiting for cron):

```sh
cd MEDS_Buoy_Parser/app
uv sync
MEDS_RUN_NOW=1 MEDS_STATIONS=c44131,meds210,wel233 \
  MEDS_DATASETS_DIR=../../cioos-national-erddap/datasets \
  uv run python flow.py
```

Offline test (no network, uses `test/sample_csv`):

```sh
uv run python tests.py
```

## ERDDAP dataset fragment

`erddap_config/MEDS.xml` is the curated, reviewed fragment — its
`sourceName`s match `meds_fix.COLUMN_ORDER` exactly, and it includes the
`Q_FLAG` comment block and `missing_value` markers for the `10000` sentinel
used in raw numeric fields. `../GenerateDatasetsXml_medsbuoys.sh` installs it
into the submodule's `datasets.d/`.

If `COLUMN_ORDER` ever changes, update it and this fragment together. Pass
`--generate` to the script to produce a fresh draft from ERDDAP's own tool
(`logs/MEDS.draft.xml`) to diff against — it is not installed automatically.

Python 3.12, packaged with `uv` (`uv.lock` committed).
