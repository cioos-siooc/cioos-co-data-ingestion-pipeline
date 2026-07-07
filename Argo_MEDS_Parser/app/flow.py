#!/usr/bin/env python3
"""
Prefect entrypoint for the Argo Canada (MEDS GDAC) pipeline.

Scheduled batch flow, same shape as the MEDS buoy pipeline: mirror the
aggregated ``<WMO>_prof.nc`` files from the GDAC's ``dac/meds/`` tree, then
publish them into the national ERDDAP's shared datasets dir, where the
ARGO_MEDS dataset (EDDTableFromMultidimNcFiles) serves them. ``main()`` serves
the flow on a daily cron; set ``ARGO_RUN_NOW=1`` to run once and exit.

No fix step is needed: the GDAC NetCDF is already CF-compliant Argo format —
all reshaping for CIOOS/CDE happens in the ERDDAP fragment's addAttributes
(see ../erddap_config/ARGO_MEDS.xml).

Paths default to the in-container mounts (see docker-compose.yml) but fall back
to repo-relative dirs so ``uv run python flow.py`` works from ``app/`` locally.
"""

import os
import shutil
from pathlib import Path

from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from prefect.logging import get_run_logger

import argo_download

# --- configuration (env-overridable) ----------------------------------------
DATA_DIR = Path(os.environ.get("ARGO_DATA_DIR", "data"))
DATASETS_DIR = Path(os.environ.get("ARGO_DATASETS_DIR", "datasets"))
DATASET_NAME = os.environ.get("ARGO_DATASET_NAME", "ARGO_MEDS")
CRON = os.environ.get("ARGO_CRON", "0 7 * * *")  # daily at 07:00
GDAC_URL = os.environ.get("ARGO_GDAC_URL", argo_download.GDAC_URL)

NC_DIR = DATA_DIR / "nc"
PUBLISH_DIR = DATASETS_DIR / DATASET_NAME

# Demo default: 10 floats. Set ARGO_FLOAT_LIMIT=0 to mirror the whole MEDS DAC
# (~900 floats, several GB); ARGO_FLOATS takes precedence over the limit.
DEFAULT_FLOAT_LIMIT = 10


def _floats_from_env():
    """Optional ARGO_FLOATS='4902674,4902530' allowlist for dev runs."""
    raw = os.environ.get("ARGO_FLOATS", "").strip()
    return [f.strip() for f in raw.split(",") if f.strip()] or None


def _limit_from_env():
    raw = os.environ.get("ARGO_FLOAT_LIMIT", "").strip()
    if raw == "":
        return DEFAULT_FLOAT_LIMIT
    return int(raw) or None  # 0 -> None -> all floats


@task(name="download-argo-profiles", cache_policy=NO_CACHE)
def download(wanted=None, limit=None):
    logger = get_run_logger()
    float_ids = argo_download.list_float_ids(base_url=GDAC_URL)
    logger.info(f"GDAC lists {len(float_ids)} MEDS float(s) at {GDAC_URL}")
    selected = argo_download.select_floats(float_ids, wanted=wanted, limit=limit)
    logger.info(f"Mirroring {len(selected)} float(s)")
    argo_download.download_profiles(selected, NC_DIR, base_url=GDAC_URL, logger=logger)
    return NC_DIR


@task(name="publish-to-erddap", cache_policy=NO_CACHE)
def publish():
    logger = get_run_logger()
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(NC_DIR.glob("*_prof.nc")):
        dest = PUBLISH_DIR / src.name
        # copy2 keeps mtimes so ERDDAP's updateEveryNMillis change-detection and
        # the next run's If-Modified-Since both keep working.
        if not dest.exists() or dest.stat().st_mtime != src.stat().st_mtime:
            shutil.copy2(src, dest)
        count += 1
    logger.info(f"📤 published {count} profile file(s) to {PUBLISH_DIR}")
    return count


@flow(name="argo-meds-pipeline")
def argo_pipeline():
    logger = get_run_logger()
    logger.info("🛟 Starting Argo Canada (MEDS GDAC) pipeline")
    wanted = _floats_from_env()
    limit = _limit_from_env()
    if wanted:
        logger.info(f"Float allowlist active: {wanted}")
    elif limit:
        logger.info(f"Float limit active: first {limit} floats (demo default)")
    download(wanted=wanted, limit=limit)
    published = publish()
    logger.info(f"✅ Argo MEDS pipeline complete ({published} floats published)")
    return published


def main():
    if os.environ.get("ARGO_RUN_NOW"):
        argo_pipeline()
        return
    # Register a daily deployment and keep the container alive. The first run
    # happens at the next cron tick; trigger a run from the Prefect UI to
    # populate immediately.
    argo_pipeline.serve(name="argo-meds-daily", cron=CRON)


if __name__ == "__main__":
    main()
