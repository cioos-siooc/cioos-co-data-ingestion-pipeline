# cioos-ingest

Ingestion pipelines for Canadian marine data, packaged as **one Python package,
one Docker image, one CLI** (`cioos-ingest`), publishing files for ERDDAP:

- **`cioos-ingest eccc`** — subscribes to ECCC/MSC Datamart over AMQP in real
  time, converts SWOB-ML XML to NCCSV, publishes the `ECCCbuoys` dataset.
- **`cioos-ingest meds`** — downloads the DFO MEDS buoy CSV archive on a daily
  schedule, fixes it up, runs the automated SST QC on the Pacific `C*` buoys,
  publishes the `MEDS_CSV` dataset.
- **`cioos-ingest argo`** — mirrors Argo Canada profile NetCDF (the MEDS DAC
  tree at the Argo GDAC) on a daily schedule, publishes the `ARGO_MEDS`
  dataset; a federated `ArgoFloats_Ifremer` fragment demos the
  ERDDAP-to-ERDDAP alternative.

Each pipeline publishes its files to a configurable destination
(`PUBLISH_URL`: local directory by default, or `s3://` / `sftp://`), and a
committed ERDDAP dataset fragment in [`datasets.d/`](datasets.d/) describes
each dataset.

> **The ERDDAP in this repo is a local instance for developing and testing
> datasets only.** Production ERDDAP deployment is managed elsewhere.

## Architecture

| Service | Purpose | Port |
| --- | --- | --- |
| `prefect` | Prefect 3 server (UI + API) for the pipelines | `4200` |
| `eccc_buoy_parser` | `cioos-ingest eccc` — real-time AMQP consumer | — |
| `meds_buoy_parser` | `cioos-ingest meds` — daily batch | — |
| `argo_meds_parser` | `cioos-ingest argo` — daily batch | — |
| `erddap` | Local test ERDDAP serving the published datasets | `8080` |

The three parser services share the single image built from the root
`Dockerfile`; the per-service `command:` picks the pipeline.

```
cioos-ingest eccc ──► datasets/ECCCbuoys/*.csv     ──┐
cioos-ingest meds ──► datasets/MEDS_CSV/*.csv      ──┤   (via PUBLISH_URL,
cioos-ingest argo ──► datasets/ARGO_MEDS/*_prof.nc ──┤    default file://)
                                                      ▼
   committed datasets.d/{ECCC,MEDS,ARGO_MEDS,ArgoFloats_Ifremer}.xml
                                                      │
              assembled into datasets.xml at erddap container start
                                                      ▼
                                         erddap ──► :8080/erddap
```

## Quickstart

```sh
cp .env.example .env          # set ERDDAP_flagKeyKey to any non-default value
docker compose up --build
```

First boot takes a few minutes: Prefect must be healthy before the parsers
start. The parsers register their flows but don't produce data until they run —
the ECCC consumer reacts to incoming AMQP messages; MEDS/Argo wait for their
daily cron. To populate immediately, trigger a run from the Prefect UI
(<http://localhost:4200>) or run once:

```sh
docker compose run --rm -e MEDS_RUN_NOW=1 -e MEDS_STATIONS=c44131 meds_buoy_parser
docker compose run --rm -e ARGO_RUN_NOW=1 -e ARGO_FLOAT_LIMIT=2 argo_meds_parser
```

Verify at <http://localhost:8080/erddap> (`tabledap/MEDS_CSV.html`,
`tabledap/ARGO_MEDS.html`, `tabledap/ECCCbuoys_2892_afd8_6091.html`; the
`ArgoFloats_Ifremer` dataset redirects to Ifremer). Since ERDDAP only starts
serving a dataset after its first files exist, restart it after the first
data lands: `docker compose restart erddap`.

Stop everything with `docker compose down` (add `-v` to also drop the
`prefect_data` volume).

## CLI

```sh
cioos-ingest eccc              # ECCC AMQP consumer (runs forever)
cioos-ingest meds [--run-now]  # MEDS batch flow (serves on MEDS_CRON)
cioos-ingest argo [--run-now]  # Argo batch flow (serves on ARGO_CRON)
```

Local development (Python 3.12, packaged with `uv`, single `uv.lock`):

```sh
uv sync --all-extras
uv run pytest                          # offline tests (MEDS, Argo, publish)
uv run cioos-ingest --help
MEDS_RUN_NOW=1 MEDS_STATIONS=c44131 uv run cioos-ingest meds
```

`ARGO_TEST_NETWORK=1 uv run pytest tests/test_argo.py` adds a live single-float
GDAC round trip. `tests/eccc_driver.py` reprocesses a directory of downloaded
SWOB XML without a live AMQP connection.

## Configuration

All configuration is via environment variables (see `docker-compose.yml` for
the in-container values).

| Var | Default | Purpose |
| --- | --- | --- |
| `PUBLISH_URL` | `file://./datasets` | Destination for published files (see below) |
| `ECCC_PUBLISH_URL` / `MEDS_PUBLISH_URL` / `ARGO_PUBLISH_URL` | *(unset)* | Per-pipeline destination override |
| `ECCC_DATASET_NAME` | `ECCCbuoys` | Dataset subdir at the destination |
| `MEDS_DATASET_NAME` | `MEDS_CSV` | Dataset subdir + ERDDAP datasetID |
| `ARGO_DATASET_NAME` | `ARGO_MEDS` | Dataset subdir + ERDDAP datasetID |
| `ECCC_DATA_DIR` / `MEDS_DATA_DIR` / `ARGO_DATA_DIR` | `data` | Per-pipeline work dir |
| `ECCC_CONFIG_DIR` | `config` | ECCC station mapping/types JSON dir |
| `MEDS_CRON` / `ARGO_CRON` | `0 6 * * *` / `0 7 * * *` | Daily schedules |
| `MEDS_RUN_NOW` / `ARGO_RUN_NOW` | *(unset)* | Run once and exit instead of serving |
| `MEDS_STATIONS` | *(all)* | Station allowlist, e.g. `c46131,meds210` |
| `ARGO_FLOATS` | *(unset)* | WMO allowlist, e.g. `4902674,4902530` |
| `ARGO_FLOAT_LIMIT` | `10` | Demo default: first N floats. `0` = all (~900 floats, ~2 GB) |
| `ARGO_GDAC_URL` | `https://data-argo.ifremer.fr` | GDAC mirror; alt: `https://usgodae.org/pub/outgoing/argo` |
| `ERDDAP_PORT` / `ERDDAP_flagKeyKey` | `8080` / — | Local test ERDDAP (in `.env`) |

## Publishing destinations

Publishing goes through [`cioos_ingest.publish`](src/cioos_ingest/publish.py)
(fsspec). The destination URL resolves as `<PIPELINE>_PUBLISH_URL` >
`PUBLISH_URL` > `file://./datasets`.

- **`file://`** (default) — plain local copy, mtimes preserved
  (`shutil.copy2`); this is what the compose stack uses
  (`PUBLISH_URL=file:///app/datasets`, bind-mounted to `./datasets`, which
  ERDDAP reads at `/datasets`). Don't change one side of that mount without
  the other.
- **`s3://bucket/prefix`** — needs the `remote` extra (included in the Docker
  image; locally `uv sync --all-extras`). Credentials via the standard
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (+ `AWS_ENDPOINT_URL` for
  MinIO-style endpoints). Example smoke test against MinIO:
  `PUBLISH_URL=s3://test-bucket/datasets AWS_ENDPOINT_URL=http://localhost:9000 ...`
- **`sftp://user@host/path`** — needs the `remote` extra; password in the URL
  or standard SSH keys.

Caveats for non-local destinations: Argo's skip-unchanged check falls back
from mtime+size to size-only, and the ECCC consumer re-uploads the full
station CSV on every AMQP message (fine locally, chattier remotely).

## ERDDAP dataset fragments (`datasets.d/`)

`datasets.d/*.xml` are the **committed source of truth** for the dataset
definitions. The `erddap` service (`axiom/docker-erddap`) assembles them into
`erddap/content/datasets.xml` at container start — after editing a fragment,
`docker compose restart erddap`.

Two kinds of fragment:

- **Curated** (`MEDS.xml`, `ARGO_MEDS.xml`, `ArgoFloats_Ifremer.xml`) —
  hand-reviewed: MEDS's `sourceName`s match `meds_fix.COLUMN_ORDER` exactly
  (plus the `Q_FLAG` comment block and the `10000` missing-value sentinel);
  ARGO_MEDS carries the CF/CDE attributes for the GDAC NetCDF. If
  `COLUMN_ORDER` or the source file structure changes, update code and
  fragment together.
- **Generated-then-frozen** (`ECCC.xml`) — originally produced by ERDDAP's
  GenerateDatasetsXml from the NCCSV files (which already carry column
  metadata) and committed as-is; its datasetID
  (`ECCCbuoys_2892_afd8_6091`) is deliberately frozen by being committed.

To help update a fragment when a dataset's **column set** changes, generate a
fresh draft with ERDDAP's own tool and diff it against the committed fragment:

```sh
scripts/generate-datasets-xml.sh meds   # -> logs/MEDS.draft.xml
scripts/generate-datasets-xml.sh argo   # -> logs/ARGO_MEDS.draft.xml
scripts/generate-datasets-xml.sh eccc   # -> logs/ECCC.draft.xml
```

Drafts are never installed automatically. New stations/floats/files that match
a fragment's existing `fileNameRegex` need **no** fragment change — ERDDAP
picks them up on reload (`updateEveryNMillis`, or immediately with
`docker compose exec erddap touch /erddapData/flag/<datasetID>`).

`scripts/DasDds.sh` validates a fragment's parsed types without a full reload.
`ArgoFloats_Ifremer.xml` has no generator: it's a pure `EDDTableFromErddap`
redirect (see below) — edit the committed fragment directly.

## Pipeline notes

### ECCC buoys (`cioos-ingest eccc`)

Connects anonymously to the Datamart broker (`dd.weather.gc.ca:5671`, AMQPS),
binds a durable queue to the marine SWOB-ML topic on the `xpublic` exchange,
downloads each referenced XML, and appends the parsed observation to the
station's NCCSV file (staged under `data/nccsv/`, then published). The queue
name suffix (`_dev2`) is hard-coded so restarts reuse the durable queue;
changing it creates a new queue on the broker.

New stations extend two files in `config/eccc/` the first time they're seen:
`ECCCbuoys_json_fields.json` (field mapping, gitignored — regenerated per
environment) and `ECCCbuoys_types.json` (column types, checked in). Expect
both to grow on a fresh queue.

### MEDS buoys (`cioos-ingest meds`)

download → fix → publish. Download lists MEDS's `CSVDATA/` index and mirrors
any new/changed `*_csv.zip` (conditional GETs), extracts, and normalises
headers (canonical 23-column header for `C*` buoys). Fix converts dates to
ISO 8601 UTC, flips longitude to degrees east (raw values kept as
`preciseLat`/`preciseLon`), sets fixed deployment coordinates from
`b_pw_inv.json`, and reindexes every buoy type to one unified column set —
modern `C*` buoys carry wave + wind + pressure + temperature, historic
`MEDS*`/`WEL*` buoys are wave-only with empty met cells.

**SST QC.** After the fix step, the 17 Pacific buoys the legacy CIOOS Pacific
job QC'd (`sst_qc.QC_STATIONS`) get `SSTP_flags` (1–16, higher is better) and
`SSTP_UQL` (QARTOD 1/2/3/4) filled in; every other row leaves them empty. The
algorithm, [`meds/qc.py`](src/cioos_ingest/meds/qc.py), is vendored unchanged
from `cioos-siooc/cioos-pacific-pipeline`, where it reproduces the legacy
`dfo_buoy_qc_operationalize` flags row for row; keep it in sync there. It
compares each station's daily mean SST with the nearest NOAA OISST v2.1 cell
and, up to 2020-12-31, with a static AVHRR record
([`meds/data/`](src/cioos_ingest/meds/data/)), then flags out-of-range values,
hourly jumps and isolated spikes. OISST comes from NOAA PSL's yearly global files, as in the
legacy job. Each file is cropped to the NE Pacific box and cached as
`MEDS_DATA_DIR/oisst/sst.day.mean.YYYY.nc` (~10 MB). The first run downloads
~45 files of ~450 MB (about an hour); later runs re-fetch only the current
year. Readings newer than the last OISST day stay unflagged until a later run. A station whose QC fails isn't
republished (its previous file and flags stay) and the run ends Failed.

The legacy job also topped the archive up with realtime SWOB rows and QC'd
those. Here the QC covers the MEDS archive only. The ECCC realtime dataset
isn't QC'd.

### Argo Canada (`cioos-ingest argo`)

MEDS (DFO's Marine Environmental Data Section) is Canada's **Argo Data
Assembly Centre** (DAC code `ME`); the archive of record is the `dac/meds/`
tree at the Global Data Assembly Centres (Ifremer and US GODAE,
byte-identical). Each float directory holds an aggregated `<WMO>_prof.nc` —
all cycles, all core parameters, R/D QC modes rolled up — the natural
per-float harvest unit (~900 Canadian floats, ~2 MB each).

The flow mirrors those files with conditional GETs (unchanged floats cost one
304 each) and publishes them; no fix step is needed since GDAC files are
already CF-compliant Argo NetCDF — all reshaping happens in the fragment's
`addAttributes`.

**Two integration versions** ship as fragments:

| | v1 federate (`ArgoFloats_Ifremer.xml`) | v2 mirror (`ARGO_MEDS.xml` + this pipeline) |
| --- | --- | --- |
| Mechanism | `EDDTableFromErddap` redirect to Ifremer | Local files, `EDDTableFromMultidimNcFiles` |
| Scope | **Entire global Argo** — cannot subset | Canadian (MEDS DAC) floats only |
| Data hosting | None | ~2 GB for the full DAC |
| Availability | Depends on Ifremer uptime (observed slow) | Local |
| Metadata control | None (`addAttributes` forbidden) | Full (CDE-compatible attrs baked in) |

**Why v1 can't be Canada-only:** `EDDTableFromErddap` is a pure redirect
mirror (no `addAttributes`, no constraints), and no ERDDAP dataset type can
bake a fixed row filter (e.g. `data_center="ME"`) over a remote server —
subsetting requires owning the files, which is v2. v1 is included as a
working demo of the federation mechanics.

Production notes: the GDACs also offer rsync (`vdmzrs.ifremer.fr`) — the
better transport when mirroring the full DAC; HTTP conditional-GET keeps this
stack dependency-free. BGC floats' `<WMO>_Sprof.nc` (synthetic biogeochemical
profiles) are out of scope, an easy extension later.
