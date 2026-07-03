# ECCC_Buoy_Parser

Subscribes to Environment and Climate Change Canada (ECCC) / MSC Datamart
marine buoy notifications over AMQP in real time, converts the SWOB-ML XML
files to ERDDAP-compatible NCCSV, and publishes them to the CIOOS National
ERDDAP as the `ECCCbuoys` dataset. Real-time counterpart to
`MEDS_Buoy_Parser` in this repo — see the [root README](../README.md) for full
stack setup and dataset-fragment generation.

## What it does (`app/amqp_client.py`)

1. Connects anonymously to the ECCC Datamart broker (`dd.weather.gc.ca:5671`,
   AMQPS) and binds a durable queue to the marine SWOB-ML topic on the
   `xpublic` exchange.
2. For each notification, downloads the referenced SWOB-ML XML file.
3. Parses it (`marine_buoy_parser.py`) and appends the parsed observation to
   the station's NCCSV file under `datasets/ECCCbuoys/`.

## Queue naming

The queue name is
`q_anonymous.subscribe.marine_buoys.<hostname>_dev2` — the `dev2` suffix is
hard-coded so restarts reuse the same durable queue instead of losing
backlog. Changing it creates a new queue; the old one persists on the broker
until it expires.

## Station config

New stations extend two files under `app/config/` the first time they're
seen:

- `ECCCbuoys_json_fields.json` — field mapping, **gitignored** (regenerated
  per environment).
- `ECCCbuoys_types.json` — column types, **checked in**.

Expect both to grow on first run against a fresh queue.

## Running it standalone

Via the stack: `docker compose up --build eccc_buoy_parser` — needs a
reachable Prefect server (`PREFECT_API_URL`) and outbound access to
`dd.weather.gc.ca:5671` plus HTTPS for the SWOB-ML downloads.

Locally, without Docker:

```sh
cd ECCC_Buoy_Parser/app
uv sync
uv run python amqp_client.py
```

To reprocess a directory of already-downloaded XML files without needing a
live AMQP connection (useful when changing parser logic):

```sh
uv run python tests.py   # ad-hoc driver, not a pytest suite
```

## ERDDAP dataset fragment

Unlike MEDS_Buoy_Parser's curated fragment, the ECCC fragment is fully
regenerated from the current NCCSV files each time
`../GenerateDatasetsXml_ecccbuoys.sh` runs — there's no hand-tuned XML to keep
in sync, since ECCC's NCCSV files already carry ERDDAP-ready column metadata.

Python 3.12, packaged with `uv` (`uv.lock` committed).
