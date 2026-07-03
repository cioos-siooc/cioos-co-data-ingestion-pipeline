# ECCC_Buoy_Parser

Two pipelines that publish Canadian marine buoy data to the **CIOOS National
ERDDAP** (vendored here as the `cioos-national-erddap` git submodule):

- **`eccc_buoy_parser`** — subscribes to ECCC/MSC Datamart over AMQP in
  real time, converts SWOB-ML XML to NCCSV, publishes the `ECCCbuoys` dataset.
  Details: [`ECCC_Buoy_Parser/README.md`](ECCC_Buoy_Parser/README.md).
- **`meds_buoy_parser`** — downloads the DFO MEDS buoy CSV archive on a daily
  schedule, publishes the `MEDS_CSV` dataset. Details:
  [`MEDS_Buoy_Parser/README.md`](MEDS_Buoy_Parser/README.md).

Both follow the same pattern: parser writes CSV/NCCSV files into a shared
`datasets/` directory, a `GenerateDatasetsXml_*.sh` script writes an ERDDAP
dataset fragment into `datasets.d/`, and `erddap_sync` merges every fragment
into `datasets.xml` for ERDDAP to serve.

## Architecture

| Service | Defined in | Purpose | Port |
| --- | --- | --- | --- |
| `prefect` | this repo | Prefect 3 server (UI + API) for both parsers | `4200` |
| `eccc_buoy_parser` | this repo | Real-time AMQP consumer + parser | — |
| `meds_buoy_parser` | this repo | Daily batch MEDS download + parser | — |
| `erddap_sync` | this repo | One-shot: harvests regional CIOOS servers + merges `datasets.d/*.xml` into `datasets.xml`, then exits | — |
| `erddap` | `include`d from the `cioos-national-erddap` submodule | Serves all datasets | `8080` |

```
eccc_buoy_parser ──► datasets/ECCCbuoys/*.csv  ──┐
meds_buoy_parser ──► datasets/MEDS_CSV/*.csv   ──┤
                                                  ▼
              GenerateDatasetsXml_*.sh ──► datasets.d/{ECCC,MEDS}.xml
                                                  │
                              erddap_sync merges into datasets.xml
                                                  │
                                                  ▼
                                     erddap ──► :8080/erddap
```

## Setup

**1. Clone with the submodule**

```sh
git clone --recurse-submodules git@github.com:cioos-siooc/ECCC_Buoy_Parser.git
# or, in an existing clone:
git submodule update --init
```

**2. Create the ERDDAP env file** (`cioos-national-erddap/.env`)

```sh
printf 'ERDDAP_PORT=8080\nERDDAP_flagKeyKey=changeme-local\n' > cioos-national-erddap/.env
```

`flagKeyKey` is a secret ERDDAP requires to be non-default; any local value
works for development.

**3. Start the stack**

```sh
docker compose up --build
```

First boot takes a few minutes: Prefect must be healthy before the parsers
start, and `erddap_sync` must finish before `erddap` starts. Both parsers
register their flows with Prefect but don't produce data until they run —
the ECCC parser reacts to incoming AMQP messages, the MEDS parser waits for
its daily cron (or a manual trigger).

**4. Generate the ERDDAP dataset fragments**

Each parser has its own script. Run them after the parsers have written at
least some data:

```sh
./GenerateDatasetsXml_ecccbuoys.sh   # reads datasets/ECCCbuoys/*.csv  -> datasets.d/ECCC.xml
./GenerateDatasetsXml_medsbuoys.sh   # installs a curated fragment     -> datasets.d/MEDS.xml
```

The ECCC script regenerates its fragment from the current NCCSV files every
run. The MEDS script installs a pre-built, reviewed fragment
(`MEDS_Buoy_Parser/erddap_config/MEDS.xml`) rather than generating one from
scratch — pass `--generate` to also produce a draft from ERDDAP's own tool for
comparison when the column set changes.

**5. Load the fragments into ERDDAP**

Fragments are picked up automatically the next time `erddap_sync` runs (e.g.
on the next `docker compose up`). To reload a running ERDDAP without
restarting:

```sh
docker compose run --rm erddap_sync
docker compose exec erddap touch /erddapData/flag/ECCCbuoys_<id>
docker compose exec erddap touch /erddapData/flag/MEDS_CSV
```

**6. Verify**

- Prefect UI: <http://localhost:4200>
- ERDDAP: <http://localhost:8080/erddap/tabledap/MEDS_CSV.html> and `/ECCCbuoys_<id>.html`

To validate a fragment's parsed types without a full reload: `./DasDds.sh`.

## When do the generate scripts need rerunning?

Only when the **set of columns** changes (e.g. a new sensor field appears in
the source data) — that's a fragment/schema change. New stations/buoys
appearing in already-known columns don't require it: ERDDAP dataset
directories are scanned by regex, so new files matching the pattern are
picked up on the next reload with no fragment change needed.

Stop everything with `docker compose down` (add `-v` to also drop the
`prefect_data` volume).

## Local development (without Docker)

```sh
cd ECCC_Buoy_Parser/app && uv sync && uv run python amqp_client.py   # needs PREFECT_API_URL reachable
cd MEDS_Buoy_Parser/app  && uv sync && uv run python tests.py        # offline, no network
```

Both are Python 3.12, packaged with `uv` (`uv.lock` committed).

## Notes

- `docker-compose.yml` mounts `./cioos-national-erddap/datasets` into both
  parsers and into ERDDAP — this is how files reach ERDDAP. Don't change the
  paths on one side without the other.
- The `cioos-national-erddap` submodule is pinned to branch `feat/datasets.d`.
- ERDDAP version: `erddap/erddap:v2.28.1` (via the submodule's compose);
  `axiom/docker-erddap:2.23-jdk17-openjdk` is used by the `GenerateDatasetsXml_*`
  scripts to run ERDDAP's own generator tool.
