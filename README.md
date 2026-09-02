# cioos-ingest

Ingestion pipelines for Canadian marine data, packaged as **one Python package,
one Docker image, one CLI** (`cioos-ingest`), publishing to the CIOOS buoy
dataset bucket on Calcul Québec's Juno cloud:

- **`cioos-ingest eccc`** — subscribes to ECCC/MSC Datamart over AMQP in real
  time, converts SWOB-ML XML to NCCSV, publishes the `ECCCbuoys` dataset.
- **`cioos-ingest meds`** — downloads the DFO MEDS buoy CSV archive on a daily
  schedule, fixes it up, publishes the `MEDS_CSV` dataset.
- **`cioos-ingest argo`** — mirrors Argo Canada profile NetCDF (the MEDS DAC
  tree at the Argo GDAC) on a daily schedule, publishes the `ARGO_MEDS`
  dataset.

Each pipeline publishes its files to a configurable destination
(`PUBLISH_URL`), defaulting to `s3://cioos-juno-buoy-data/datasets`.

## Architecture

| Service | Purpose | Port |
| --- | --- | --- |
| `prefect` | Prefect 3 server (UI + API) for the pipelines | `4200` |
| `eccc_buoy_parser` | `cioos-ingest eccc` — real-time AMQP consumer | — |
| `meds_buoy_parser` | `cioos-ingest meds` — daily batch | — |
| `argo_meds_parser` | `cioos-ingest argo` — daily batch | — |

The three parser services share the single image built from the root
`Dockerfile`; the per-service `command:` picks the pipeline.

```
cioos-ingest eccc ──► ECCCbuoys/*.csv     ──┐
cioos-ingest meds ──► MEDS_CSV/*.csv      ──┤  via PUBLISH_URL (fsspec)
cioos-ingest argo ──► ARGO_MEDS/*_prof.nc ──┤
                                            ▼
                    s3://cioos-juno-buoy-data/datasets/<DATASET>/
                    (Ceph RADOS Gateway, objets.juno.calculquebec.ca)
```

## Quickstart

You need the two bucket credentials from a CIOOS administrator — see
[Credentials](#credentials).

```sh
cp .env.example .env          # then fill in the two AWS_* keys
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

Verify what landed in the bucket:

```sh
aws --endpoint-url https://objets.juno.calculquebec.ca \
    s3 ls --recursive s3://cioos-juno-buoy-data/datasets/
```

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
uv run pytest                          # offline tests (MEDS, Argo, ECCC, publish)
uv run cioos-ingest --help
MEDS_RUN_NOW=1 MEDS_STATIONS=c44131 uv run cioos-ingest meds
```

Two test suites are opt-in because they need the network:
`ARGO_TEST_NETWORK=1 uv run pytest tests/test_argo.py` adds a live single-float
GDAC round trip, and `JUNO_TEST_BUCKET=1 uv run pytest tests/test_publish.py`
adds a publish/read/delete round trip against the real bucket (needs
credentials). `tests/eccc_driver.py` reprocesses a directory of downloaded SWOB
XML without a live AMQP connection.

## Configuration

All configuration is via environment variables (see `docker-compose.yml` for
the in-container values).

| Var | Default | Purpose |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | — | Bucket credentials; required for an `s3://` destination |
| `AWS_ENDPOINT_URL` | `https://objets.juno.calculquebec.ca` | Juno S3 gateway |
| `AWS_DEFAULT_REGION` | `us-east-1` | Placeholder; the gateway ignores it but SDKs require one |
| `PUBLISH_URL` | `s3://cioos-juno-buoy-data/datasets` | Destination for published files (see below) |
| `ECCC_PUBLISH_URL` / `MEDS_PUBLISH_URL` / `ARGO_PUBLISH_URL` | *(unset)* | Per-pipeline destination override |
| `ECCC_DATASET_NAME` | `ECCCbuoys` | Dataset subdir/prefix at the destination |
| `MEDS_DATASET_NAME` | `MEDS_CSV` | Dataset subdir/prefix at the destination |
| `ARGO_DATASET_NAME` | `ARGO_MEDS` | Dataset subdir/prefix at the destination |
| `ECCC_DATA_DIR` / `MEDS_DATA_DIR` / `ARGO_DATA_DIR` | `data` | Per-pipeline work dir |
| `ECCC_CONFIG_DIR` | `config` | ECCC station mapping/types JSON dir |
| `MEDS_CRON` / `ARGO_CRON` | `0 6 * * *` / `0 7 * * *` | Daily schedules |
| `MEDS_RUN_NOW` / `ARGO_RUN_NOW` | *(unset)* | Run once and exit instead of serving |
| `MEDS_STATIONS` | *(all)* | Station allowlist, e.g. `c46131,meds210` |
| `ARGO_FLOATS` | *(unset)* | WMO allowlist, e.g. `4902674,4902530` |
| `ARGO_FLOAT_LIMIT` | `0` | First N floats. `0` = all (~900 floats, ~2 GB) |
| `ARGO_GDAC_URL` | `https://data-argo.ifremer.fr` | GDAC mirror; alt: `https://usgodae.org/pub/outgoing/argo` |

Note that the code default in
[`publish.py`](src/cioos_ingest/publish.py) is `file://./datasets` — the
`s3://` default above comes from `docker-compose.yml`, so a bare `uv run`
outside compose publishes locally unless you set `PUBLISH_URL`.

### Credentials

Credentials are issued by a CIOOS administrator; there is no self-service. Put
them in the gitignored `.env` locally, and in the Coolify resource's
Environment Variables tab for the deployment. **Never** commit them, bake them
into an image, or log them.

The issued key can read, write and delete **any bucket in the CIOOS project**,
not just this one — there is no per-bucket permission system in this storage
backend, so the key is far more powerful than the job it does. Report a leak to
the CIOOS infrastructure team immediately; it is revocable in seconds.

Full access documentation, including troubleshooting, lives in the
infrastructure repo:
[`cioos-co-alliance-juno-infra` → `docs/buoy-data-bucket-access.md`](https://github.com/cioos-siooc/cioos-co-alliance-juno-infra/blob/main/docs/buoy-data-bucket-access.md).

## Publishing destinations

Publishing goes through [`cioos_ingest.publish`](src/cioos_ingest/publish.py)
(fsspec). The destination URL resolves as `<PIPELINE>_PUBLISH_URL` >
`PUBLISH_URL` > `file://./datasets`. Files land at
`<PUBLISH_URL>/<DATASET_NAME>/<filename>`.

- **`s3://bucket/prefix`** (the compose default) — the CIOOS Juno bucket.
  Credentials via the standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`,
  endpoint via `AWS_ENDPOINT_URL`.
- **`file://`** — plain local copy, mtimes preserved (`shutil.copy2`). Useful
  for offline work and for the `scripts/` helpers, which read `./datasets`.
- **`sftp://user@host/path`** — needs the `remote` extra
  (`uv sync --all-extras`); password in the URL or standard SSH keys.

### The Juno store is not AWS

It is a **Ceph RADOS Gateway** speaking the S3 API. Four differences matter:

- **Path-style addressing is mandatory.** There is no wildcard DNS for buckets
  and the TLS certificate covers only `objets.juno.calculquebec.ca`, so a
  virtual-host-style client fails with a DNS or certificate error naming
  `cioos-juno-buoy-data.objets…` rather than a useful S3 error. `botocore`
  (hence `s3fs`) already defaults to path style whenever a custom
  `endpoint_url` is set, which is why no client configuration is needed here.
- **The bucket is private and has no anonymous read.** An unauthenticated
  request gets `NoSuchBucket` (404), *not* `AccessDenied` — the gateway won't
  confirm that a bucket it won't serve exists. **A 404 does not mean the bucket
  is missing**; it usually means the credentials never reached the request.
  `publish.check_credentials` catches the common case of unset keys up front so
  this doesn't have to be diagnosed from a 404. Relatedly, `publish_files`
  issues no `makedirs` on s3: s3fs would interpret that 404 as "no such bucket"
  and try to *create* it, and the issued key is permitted to do so — a
  misspelled bucket name would otherwise leave a stray bucket in the CIOOS
  project. `put_file` creates the key by itself.
- **The region is a placeholder.** Any value works; use `us-east-1`.
- **`SignatureDoesNotMatch`** almost always means the access key id was pasted
  into both fields. They are different 32-character values.

### Caveats

- `skip_unchanged` (used by MEDS and Argo) compares destination size **and**
  mtime. Object storage reports the `LastModified` of the upload, not the
  source mtime, so on `s3://` the check degrades to **size-only** — a file
  whose content changes without changing length will be skipped.
- The ECCC consumer re-uploads the full station CSV on every AMQP message.
  That is cheap locally but has a real cost against a versioned bucket — see
  below.

## Bucket versioning and storage growth

**The bucket is versioned, and this pipeline amplifies that hard. It needs a
lifecycle rule.**

### What versioning does

A PUT to an existing key does not overwrite it. It creates a new *current*
version and demotes the previous copy to a *noncurrent* version; both consume
storage. `s3 rm` doesn't reclaim anything either — it writes a *delete marker*
and leaves the old versions behind. So `aws s3 ls`, which lists only current
versions, systematically under-reports true usage.

This is a useful property in itself: a pipeline bug that overwrites good data
is recoverable, so ask CIOOS before re-uploading from scratch.

### Why this pipeline amplifies it

[`swob_parser.py`](src/cioos_ingest/eccc/swob_parser.py) appends **one row**
per AMQP message to the station's NCCSV, and
[`consumer.py`](src/cioos_ingest/eccc/consumer.py) then publishes that path,
which uploads the **whole file** (`publish.py`, `fs.put_file`). Every
observation therefore re-uploads all history accumulated so far, and versioning
retains every one of those cumulative snapshots. Retained bytes grow
**quadratically** in observation count, not linearly.

Order of magnitude per station per year, assuming hourly reporting. The file
geometry is measured from a published station file: 103 columns, 361 B per data
row, 20.7 KB of NCCSV header (234 lines of column metadata, which is why an
almost-empty station file is still ~21 KB).

| | |
|---|---|
| Uploads | ~8,760 |
| Live file size after a year | ~3.2 MB |
| Sum of all retained versions | `8760×20.7 KB + 361 B×8760²/2` ≈ **14 GB** |

That is roughly 4,400× amplification — on the order of 1.4 TB/year across a
~100-buoy feed backing ~320 MB of actual data. The reporting cadence and
station count are assumptions, not measurements, but the shape holds at any
plausible values.

Argo has a milder form of the same thing: ~900 files of ~2 MB, each fully
re-uploaded whenever a float adds a cycle.

### What to do about it

**Ask the CIOOS infrastructure team for a `NoncurrentVersionExpiration`
lifecycle rule** on the bucket — e.g. expire noncurrent versions after 7 days.
It is enforced server-side, needs no code, and can be added at any time; it
bounds storage at roughly live data plus a week of churn while still leaving a
week of recovery history.

A deferred second measure, **not implemented here**: debounce ECCC publishes so
each station uploads at most every N hours instead of once per message. At
6-hourly that cuts generated bytes ~36×, and it composes with the lifecycle
rule — the rule bounds retention, the debounce bounds what gets generated. The
cost is up to N hours of publish latency.

## ERDDAP

The pipelines no longer run or feed an ERDDAP instance; they publish to object
storage and stop there. The local test ERDDAP that used to live in this repo
(and read the pipelines' output over a shared `./datasets` bind mount) has been
removed — see git history if it needs to come back.

The dataset definitions in [`datasets.d/`](datasets.d/) are **kept** as the
committed source of truth for dataset metadata, because they are hand-curated
and cannot be reconstructed from the data files:

- **Curated** (`MEDS.xml`, `ARGO_MEDS.xml`, `ArgoFloats_Ifremer.xml`) —
  MEDS's `sourceName`s match `meds_fix.COLUMN_ORDER` exactly (plus the `Q_FLAG`
  comment block and the `10000` missing-value sentinel); ARGO_MEDS carries the
  CF/CDE attributes for the GDAC NetCDF. If `COLUMN_ORDER` or the source file
  structure changes, update code and fragment together.
- **Generated-then-frozen** (`ECCC.xml`) — originally produced by ERDDAP's
  GenerateDatasetsXml from the NCCSV files and committed as-is; its datasetID
  (`ECCCbuoys_2892_afd8_6091`) is deliberately frozen by being committed.

Their `<fileDir>/datasets/<DATASET>/</fileDir>` paths are **inert** while output
goes to the bucket. Note that ERDDAP 2.x cannot read `s3://` for these dataset
types, so wiring a future ERDDAP to this bucket would need `<cacheFromUrl>` or
a FUSE/rclone mount rather than a path change.

`scripts/generate-datasets-xml.sh` (draft a fragment with ERDDAP's own tool and
diff it against the committed one) and `scripts/DasDds.sh` (validate a
fragment's parsed types) still work — they run the ERDDAP image ad-hoc via
`docker run` and need no compose service — but they read a local `./datasets`,
so populate it first with a `PUBLISH_URL=file://$PWD/datasets` run.

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

A station file with an incomplete NCCSV header is unreadable forever, so
`validate_nccsv_header` gates every publish — a bad header is logged and
dropped rather than uploaded.

### MEDS buoys (`cioos-ingest meds`)

download → fix → publish. Download lists MEDS's `CSVDATA/` index and mirrors
any new/changed `*_csv.zip` (conditional GETs), extracts, and normalises
headers (canonical 23-column header for `C*` buoys). Fix converts dates to
ISO 8601 UTC, flips longitude to degrees east (raw values kept as
`preciseLat`/`preciseLon`), sets fixed deployment coordinates from
`b_pw_inv.json`, and reindexes every buoy type to one unified column set —
modern `C*` buoys carry wave + wind + pressure + temperature, historic
`MEDS*`/`WEL*` buoys are wave-only with empty met cells.

### Argo Canada (`cioos-ingest argo`)

MEDS (DFO's Marine Environmental Data Section) is Canada's **Argo Data
Assembly Centre** (DAC code `ME`); the archive of record is the `dac/meds/`
tree at the Global Data Assembly Centres (Ifremer and US GODAE,
byte-identical). Each float directory holds an aggregated `<WMO>_prof.nc` —
all cycles, all core parameters, R/D QC modes rolled up — the natural
per-float harvest unit (~900 Canadian floats, ~2 MB each).

The flow mirrors those files with conditional GETs (unchanged floats cost one
304 each) and publishes them; no fix step is needed since GDAC files are
already CF-compliant Argo NetCDF — all reshaping is described in the
`ARGO_MEDS.xml` fragment's `addAttributes`.

`ArgoFloats_Ifremer.xml` is retained as a demo of the alternative integration
route: a pure `EDDTableFromErddap` redirect to Ifremer covering **all** of
global Argo, with no data hosting but also no ability to subset to Canadian
floats or control metadata. Mirroring (this pipeline) is what buys Canadian
scope and CDE-compatible attributes.

Production notes: the GDACs also offer rsync (`vdmzrs.ifremer.fr`) — the
better transport when mirroring the full DAC; HTTP conditional-GET keeps this
stack dependency-free. BGC floats' `<WMO>_Sprof.nc` (synthetic biogeochemical
profiles) are out of scope, an easy extension later.
