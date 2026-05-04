# ECCC_Buoy_Parser

A pipeline that subscribes to Environment and Climate Change Canada (ECCC) /
Meteorological Service of Canada (MSC) Datamart real-time marine buoy
notifications over AMQP, downloads the SWOB-ML XML files, converts them to
ERDDAP-compatible NCCSV, and serves them via a local ERDDAP instance.

## Architecture

The stack is three Docker services orchestrated by `docker-compose.yml`:

| Service | Image | Purpose | Port |
| --- | --- | --- | --- |
| `prefect` | `prefecthq/prefect:3-latest` | Prefect 3 server (UI + API). The parser registers its flows/tasks here. | `4200` |
| `eccc_buoy_parser` | built from `./ECCC_Buoy_Parser/Dockerfile` | Long-running AMQP consumer + parser (`app/amqp_client.py`). | — |
| `erddap` | `axiom/docker-erddap:v2.28.1` | Serves the NCCSV files written by the parser. | `8080` |

The shared `./datasets` directory is the contract between the parser (writer)
and ERDDAP (reader). Both containers mount it.

```
MSC Datamart (AMQPS)
        │
        ▼
 eccc_buoy_parser ──writes──► ./datasets/ECCCbuoys/*.csv ──reads──► erddap
        │
        └──registers flows──► prefect
```

## Prerequisites

- Docker Engine + Docker Compose v2 (`docker compose ...`).
- Outbound access to `dd.weather.gc.ca:5671` (AMQPS) and HTTPS for SWOB-ML
  downloads.
- Free local ports: `4200` (Prefect), `8080` (ERDDAP).

## Quick start

From the repo root:

```sh
docker compose up --build
```

First boot takes a few minutes — Prefect must report healthy before the parser
container starts (`depends_on: service_healthy`), and ERDDAP needs a moment to
initialise Tomcat.

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
- ERDDAP won't list a dataset until you wire it into
  `erddap/content/datasets.xml` — see the next section.

## Generating ERDDAP `datasets.xml`

`erddap/content/datasets.xml` is generated, not hand-written. Two helpers wrap
the `axiom/docker-erddap` image's `GenerateDatasetsXml.sh`:

```sh
# Pre-baked args for the ECCC buoy NCCSV directory:
./GenerateDatasetsXml_ecccbuoys.sh

# Or run it interactively and answer the prompts yourself:
./GenerateDatasetsXml.sh
```

Copy the relevant `<dataset>...</dataset>` block into
`erddap/content/datasets.xml` and either restart the `erddap` service or touch
its flag file to reload.

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

- `./datasets` → parser `/app/datasets` **and** ERDDAP `/datasets`. Don't break
  this — it's how data gets from the parser to ERDDAP.
- `./ECCC_Buoy_Parser/app/config` → parser `/app/config`. Note this is **not**
  the same as the top-level `./config/` directory (which contains a duplicate
  `ECCCbuoys_types.json`). The duplication is intentional but easy to confuse.
- `./erddap/content` → ERDDAP's Tomcat content dir. `datasets.xml` lives here.

The parser uses relative paths (`./datasets/...`, `./config/...`) so it works
both from `app/` locally and `/app` in the container. If you change the working
directory or volume layout, fix the paths in `marine_buoy_parser.py` together.

## Pinned versions

- ERDDAP: `v2.28.1` in compose; `2.23-jdk17-openjdk` in
  `GenerateDatasetsXml_ecccbuoys.sh`. The NCCSV header conventions in
  `marine_buoy_parser.py` target these — change deliberately.
- Prefect: `3-latest`.
- Python: `3.12`.
