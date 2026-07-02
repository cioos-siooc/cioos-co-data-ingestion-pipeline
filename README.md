# ECCC_Buoy_Parser

A pipeline that subscribes to Environment and Climate Change Canada (ECCC) /
Meteorological Service of Canada (MSC) Datamart real-time marine buoy
notifications over AMQP, downloads the SWOB-ML XML files, converts them to
ERDDAP-compatible NCCSV, and serves them through the **CIOOS National ERDDAP**
(vendored here as the `cioos-national-erddap` git submodule).

## Architecture

The stack is four Docker services orchestrated by `docker-compose.yml`:

| Service | Image | Purpose | Port |
| --- | --- | --- | --- |
| `prefect` | `prefecthq/prefect:3-latest` | Prefect 3 server (UI + API). The parser registers its flows/tasks here. | `4200` |
| `eccc_buoy_parser` | built from `./ECCC_Buoy_Parser/Dockerfile` | Long-running AMQP consumer + parser (`app/amqp_client.py`). | — |
| `erddap_sync` | `ghcr.io/astral-sh/uv` | One-shot. Runs the national `sync-erddap-datasets.py`: harvests the regional CIOOS servers and merges every `datasets.d/*.xml` fragment (incl. the ECCC buoy fragment) into `datasets.xml`, then exits. | — |
| `erddap` | `erddap/erddap:v2.28.1` | CIOOS National ERDDAP (from the submodule). Serves the regional datasets **and** the ECCC buoy dataset. Waits for `erddap_sync`. | `8080` |

The national ERDDAP lives in the [`cioos-national-erddap`](https://github.com/cioos-siooc/cioos-national-erddap)
submodule. Its `datasets/` directory is the contract between the parser (writer)
and ERDDAP (reader); the parser writes NCCSV there and ERDDAP mounts it.

```
MSC Datamart (AMQPS)
        │
        ▼
 eccc_buoy_parser ──writes──► cioos-national-erddap/datasets/ECCCbuoys/*.csv
        │                                    │
        │        GenerateDatasetsXml_ecccbuoys.sh ──► cioos-national-erddap/datasets.d/ECCC.xml
        │                                    │
        │                              erddap_sync (sync-erddap-datasets.py)
        │                          harvest regionals + merge datasets.d ──► datasets.xml
        │                                    │
        │                                    ▼
        └──registers flows──► prefect     erddap (CIOOS National) ──► :8080
```

## Prerequisites

- Docker Engine + Docker Compose v2 (`docker compose ...`).
- The `cioos-national-erddap` submodule initialised (`git submodule update --init`).
- Outbound access to `dd.weather.gc.ca:5671` (AMQPS) and HTTPS for SWOB-ML
  downloads, plus HTTPS to the regional CIOOS ERDDAP servers (harvested by
  `erddap_sync`) and PyPI (for `uv` to resolve the sync deps).
- Free local ports: `4200` (Prefect), `8080` (ERDDAP).

## Quick start

Clone with the submodule (or initialise it in an existing clone):

```sh
git clone --recurse-submodules git@github.com:cioos-siooc/ECCC_Buoy_Parser.git
# or, in an existing clone:
git submodule update --init
```

From the repo root:

```sh
docker compose up --build
```

First boot takes a few minutes — Prefect must report healthy before the parser
container starts (`depends_on: service_healthy`), the `erddap_sync` one-shot must
finish harvesting the regional servers and merging `datasets.d` before `erddap`
starts, and ERDDAP then needs a moment to initialise Tomcat and load ~390
datasets.

Once everything is up:

- **Prefect UI** — <http://localhost:4200>. Watch `process_message_flow` runs as
  buoy notifications arrive.
- **ERDDAP** — <http://localhost:8080/erddap/index.html>. Datasets appear once
  `datasets.xml` references them and NCCSV files exist in `./datasets/ECCCbuoys/`.
- **Parser logs** — `docker compose logs -f eccc_buoy_parser`.

Stop everything:

```sh
docker compose down
```

Add `-v` to also drop the `prefect_data` volume.

## First-run notes

- The parser binds a **durable, named** queue
  `q_anonymous.subscribe.marine_buoys.{hostname}_dev2` on the broker. The
  `dev2` suffix is hard-coded in `app/amqp_client.py` so restarts reuse the
  same queue and don't lose backlog. Changing it creates a brand-new queue;
  the old one stays on the broker until it expires.
- New buoys auto-extend `ECCC_Buoy_Parser/app/config/ECCCbuoys_json_fields.json`
  (gitignored) and `ECCCbuoys_types.json` (checked in) the first time they're
  seen. Expect those files to grow on first run.
- ERDDAP won't list the buoy dataset until its fragment exists in
  `cioos-national-erddap/datasets.d/` and `erddap_sync` has merged it into
  `datasets.xml` — see the next section.

## Generating the ECCC buoy dataset fragment

The buoy dataset is served via the national ERDDAP's `datasets.d/` mechanism.
`GenerateDatasetsXml_ecccbuoys.sh` regenerates the fragment from the current
NCCSV files and writes it straight into the submodule's `datasets.d/`:

```sh
# Reads cioos-national-erddap/datasets/ECCCbuoys/*.csv and writes
# cioos-national-erddap/datasets.d/ECCC.xml
./GenerateDatasetsXml_ecccbuoys.sh
```

On the next `docker compose up` (or by running the `erddap_sync` service again)
the national `sync-erddap-datasets.py` merges that fragment into
`cioos-national-erddap/erddap/content/datasets.xml` alongside the harvested
regional datasets. To reload without a full restart, touch the dataset's flag
file:

```sh
docker compose exec erddap touch /erddapData/flag/<datasetID>
```

The fragment is generated output — it is not committed to the submodule; only
the `datasets.d/` mechanism itself lives in `cioos-national-erddap`.

To validate a single dataset definition:

```sh
./DasDds.sh
```

## Local development (parser only)

If you just want to iterate on the parser without rebuilding the container:

```sh
cd ECCC_Buoy_Parser/app
uv sync
uv run python amqp_client.py
```

You'll need a Prefect server reachable at `PREFECT_API_URL` (start just that
service: `docker compose up prefect`).

To reprocess a directory of cached XML files (no AMQP needed — useful when
changing parser logic):

```sh
cd ECCC_Buoy_Parser/app
uv run python tests.py   # ad-hoc driver, not a pytest suite
```

Python 3.12, packaged with `uv` (`uv.lock` is committed).

## Path coupling — read before moving things

`docker-compose.yml` mounts:

- `./cioos-national-erddap/datasets` → parser `/app/datasets` **and** ERDDAP
  `/datasets`. Don't break this — it's how data gets from the parser to ERDDAP.
- `./cioos-national-erddap/datasets.d` → ERDDAP `/datasets.d`, and the target of
  `GenerateDatasetsXml_ecccbuoys.sh` / source for `erddap_sync`.
- `./cioos-national-erddap/erddap/content` → ERDDAP's Tomcat content dir;
  `datasets.xml` (regenerated by `erddap_sync`) lives here.
- `./ECCC_Buoy_Parser/app/config` → parser `/app/config`. Note this is **not**
  the same as the top-level `./config/` directory (which contains a duplicate
  `ECCCbuoys_types.json`). The duplication is intentional but easy to confuse.

The old bundled `./erddap/` directory in this repo is no longer wired into
`docker-compose.yml` (the national submodule provides ERDDAP now).

The parser uses relative paths (`./datasets/...`, `./config/...`) so it works
both from `app/` locally and `/app` in the container. If you change the working
directory or volume layout, fix the paths in `marine_buoy_parser.py` together.

## Pinned versions

- ERDDAP: `erddap/erddap:v2.28.1` in compose (via the national submodule);
  `axiom/docker-erddap:2.23-jdk17-openjdk` in `GenerateDatasetsXml_ecccbuoys.sh`.
  The NCCSV header conventions in `marine_buoy_parser.py` target these — change
  deliberately.
- `cioos-national-erddap` submodule: pinned to the `feat/datasets.d` branch.
- Prefect: `3-latest`.
- Python: `3.12`.
