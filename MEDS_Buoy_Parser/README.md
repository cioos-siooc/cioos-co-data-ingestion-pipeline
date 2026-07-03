# MEDS_Buoy_Parser

Downloads the **non-real-time DFO MEDS buoy CSV archive**, fixes it up, and
publishes it to the **CIOOS National ERDDAP** (the `cioos-national-erddap`
submodule) as the `MEDS_CSV` dataset.

It is the batch counterpart to the event-driven `ECCC_Buoy_Parser` in this repo:
same Prefect / uv / Docker conventions and the same `datasets.d/*.xml` +
`erddap_sync` publishing path, but instead of an AMQP subscription it runs on a
daily schedule and mirrors the MEDS `CSVDATA/` zip archive.

## Pipeline

```
MEDS waveshare archive (HTTPS)
  CSVDATA/*_csv.zip  +  INVENTORY/b_pw_inv.json
        │
        ▼
 meds_buoy_parser  (Prefect flow: download → fix → publish)
        │  writes per-station CSVs
        ▼
 cioos-national-erddap/datasets/MEDS_CSV/<STN_ID>.csv
        │
 GenerateDatasetsXml_medsbuoys.sh ──► cioos-national-erddap/datasets.d/MEDS.xml
        │
 erddap_sync (sync-erddap-datasets.py) merges datasets.d ──► datasets.xml
        │
        ▼
 erddap (CIOOS National) ──► :8080/erddap/tabledap/MEDS_CSV
```

### Stages (`app/flow.py`)

1. **download** (`meds_download.py`) — list the MEDS `CSVDATA/` Apache index and
   conditionally download each `*_csv.zip` (`If-Modified-Since`, i.e. `wget -N`
   behaviour), extract to a work dir, then normalise headers: strip the trailing
   comma and, for modern `C*` buoys, force the canonical 23-column header (the raw
   files have `$` suffixes and duplicate secondary-sensor column names).
2. **fix** (`meds_fix.py`) — convert dates to ISO 8601 UTC (dropping invalid ones
   like `01/10/1988 00:84`), flip longitude to degrees east (keeping the raw
   coordinate as `preciseLat`/`preciseLon`), set the fixed deployment lat/lon from
   `b_pw_inv.json`, and reindex **every** buoy type to one unified column set so a
   single ERDDAP dataset serves them all (historic `MEDS*`/`WEL*` buoys have empty
   wind/pressure/temperature cells).
3. **publish** — copy the fixed per-station CSVs into
   `cioos-national-erddap/datasets/MEDS_CSV/`, which ERDDAP reads at `/datasets`.

## Scope

All buoy families in the archive are published to the one `MEDS_CSV` dataset:

| Prefix | Example | Variables |
| --- | --- | --- |
| `C*` | `C44131` | full wave + wind + pressure + air/sea temperature |
| `MEDS*` | `MEDS210` | wave only |
| `WEL*` | `WEL233` | wave only |

## Configuration (environment variables)

| Var | Default | Purpose |
| --- | --- | --- |
| `MEDS_CRON` | `0 6 * * *` | Daily schedule for the served deployment. |
| `MEDS_STATIONS` | *(all)* | Comma-separated station allowlist (e.g. `c46131,meds210`) for dev runs. |
| `MEDS_RUN_NOW` | *(unset)* | If set, run the pipeline once and exit instead of serving. |
| `MEDS_DATA_DIR` | `data` | Work dir for zips / extracted / fixed CSVs. |
| `MEDS_DATASETS_DIR` | `datasets` | National ERDDAP datasets dir (mounted in Docker). |
| `MEDS_DATASET_NAME` | `MEDS_CSV` | Dataset subfolder + ERDDAP datasetID. |

## Run it

Via the full stack (from the repo root):

```sh
docker compose up --build meds_buoy_parser
```

The container serves the `meds-daily` deployment on the Prefect server. On first
boot there's no data until the cron fires — trigger a run immediately from the
Prefect UI (<http://localhost:4200>), or run once locally:

```sh
cd MEDS_Buoy_Parser/app
uv sync
MEDS_RUN_NOW=1 MEDS_STATIONS=c44131,meds210,wel233 \
  MEDS_DATASETS_DIR=../../cioos-national-erddap/datasets \
  uv run python flow.py
```

Then install the ERDDAP fragment and let `erddap_sync` merge it:

```sh
./GenerateDatasetsXml_medsbuoys.sh          # installs datasets.d/MEDS.xml
# pass --generate to also produce a fresh draft from ERDDAP's tool for review
```

### Offline test (no network)

```sh
cd MEDS_Buoy_Parser/app
uv run python tests.py   # exercises normalise + fix against test/sample_csv
```

## ERDDAP dataset fragment

`erddap_config/MEDS.xml` is the curated, reviewed dataset definition (its
`sourceName`s are kept in lock-step with the columns `meds_fix.COLUMN_ORDER`
writes). `GenerateDatasetsXml_medsbuoys.sh` installs it into the submodule's
`datasets.d/`. If the column set ever changes, update `COLUMN_ORDER` and this
fragment together (the `--generate` flag produces a draft to diff against).

Python 3.12, packaged with `uv` (`uv.lock` committed).
