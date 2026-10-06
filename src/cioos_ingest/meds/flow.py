#!/usr/bin/env python3
"""
Prefect entrypoint for the MEDS buoy pipeline.

Unlike the event-driven ECCC parser, MEDS bulk CSVs are non-real-time, so this is
a scheduled batch flow: download (conditional) -> fix -> publish to the PUBLISH_URL
destination (default: the CIOOS Juno buoy bucket; see cioos_ingest.publish for
file://, sftp://, ...). ``main()`` serves the flow on a daily cron so the
container stays alive as a Prefect deployment; set ``MEDS_RUN_NOW=1`` to run once
and exit (useful for local testing / CI).

Paths default to the in-container mounts (see docker-compose.yml) but fall back to
repo-relative dirs so a local ``uv run cioos-ingest meds`` works too.
"""

import os
import shutil
from pathlib import Path

from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from prefect.logging import get_run_logger

from cioos_ingest.meds import download as meds_download
from cioos_ingest.meds import fix as meds_fix
from cioos_ingest.publish import publish_files

# --- configuration (env-overridable) ---------------------------------------
DATA_DIR = Path(os.environ.get("MEDS_DATA_DIR", "data"))
DATASET_NAME = os.environ.get("MEDS_DATASET_NAME", "MEDS_CSV")
CRON = os.environ.get("MEDS_CRON", "0 6 * * *")  # daily at 06:00

ZIP_DIR = DATA_DIR / "zip"
CSV_DIR = DATA_DIR / "csv"
FIXED_DIR = DATA_DIR / "csv-fixed"


def _stations_from_env():
    """Optional MEDS_STATIONS='c46131,meds210' allowlist for dev runs."""
    raw = os.environ.get("MEDS_STATIONS", "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()] or None


@task(name="download-meds-zips", cache_policy=NO_CACHE)
def download(stations=None):
    logger = get_run_logger()
    meds_download.download_zips(ZIP_DIR, stations=stations, logger=logger)
    meds_download.extract_zips(ZIP_DIR, CSV_DIR, logger=logger)
    meds_download.normalize_headers(CSV_DIR, logger=logger)
    return CSV_DIR


@task(name="fix-meds-csv", cache_policy=NO_CACHE)
def fix():
    logger = get_run_logger()
    if FIXED_DIR.exists():
        shutil.rmtree(FIXED_DIR)
    meds_fix.fix_all(CSV_DIR, FIXED_DIR, logger=logger)
    return FIXED_DIR


@task(name="publish", cache_policy=NO_CACHE)
def publish():
    logger = get_run_logger()
    return publish_files(sorted(FIXED_DIR.glob("*.csv")), DATASET_NAME,
                         pipeline="meds", logger=logger)


@flow(name="meds-buoy-pipeline")
def meds_pipeline():
    logger = get_run_logger()
    logger.info("🌊 Starting MEDS buoy pipeline")
    stations = _stations_from_env()
    if stations:
        logger.info(f"Station allowlist active: {stations}")
    download(stations=stations)
    fix()
    published = publish()
    logger.info(f"✅ MEDS buoy pipeline complete ({published} stations published)")
    return published


def main(run_now=False):
    if run_now or os.environ.get("MEDS_RUN_NOW"):
        meds_pipeline()
        return
    # Register a daily deployment and keep the container alive. The first run
    # happens at the next cron tick; trigger a run from the Prefect UI to
    # populate immediately.
    meds_pipeline.serve(name="meds-daily", cron=CRON)


if __name__ == "__main__":
    main()
