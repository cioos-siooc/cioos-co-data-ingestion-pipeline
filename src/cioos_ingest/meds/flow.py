#!/usr/bin/env python3
"""
Prefect entrypoint for the MEDS buoy pipeline.

Unlike the event-driven ECCC parser, MEDS bulk CSVs are non-real-time, so this is
a scheduled batch flow: download (conditional) -> fix -> SST QC -> publish to the PUBLISH_URL
destination (default: the local datasets dir ERDDAP serves; see cioos_ingest.publish
for s3://, sftp://, ...). ``main()`` serves the flow on a daily cron so the
container stays alive as a Prefect deployment; set ``MEDS_RUN_NOW=1`` to run once
and exit (useful for local testing / CI).

Paths default to the in-container mounts (see docker-compose.yml) but fall back to
repo-relative dirs so a local ``uv run cioos-ingest meds`` works too.
"""

import datetime as dt
import os
import shutil
from pathlib import Path

from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from prefect.logging import get_run_logger

from cioos_ingest.meds import download as meds_download
from cioos_ingest.meds import fix as meds_fix
from cioos_ingest.meds import oisst, qc, sst_qc
from cioos_ingest.publish import publish_files

# --- configuration (env-overridable) ---------------------------------------
DATA_DIR = Path(os.environ.get("MEDS_DATA_DIR", "data"))
DATASET_NAME = os.environ.get("MEDS_DATASET_NAME", "MEDS_CSV")
CRON = os.environ.get("MEDS_CRON", "0 6 * * *")  # daily at 06:00

ZIP_DIR = DATA_DIR / "zip"
CSV_DIR = DATA_DIR / "csv"
FIXED_DIR = DATA_DIR / "csv-fixed"
OISST_DIR = DATA_DIR / "oisst"


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


@task(name="qc-sst", cache_policy=NO_CACHE, retries=2, retry_delay_seconds=300)
def qc_sst():
    """Fill SSTP_flags/SSTP_UQL of the QC stations; returns the files whose QC failed."""
    logger = get_run_logger()
    today = dt.datetime.now(dt.timezone.utc).date()
    oisst.refresh(OISST_DIR, today, logger=logger)
    archive = oisst.Archive.from_dir(OISST_DIR)
    gaps = archive.missing_days()
    if len(gaps):
        raise RuntimeError(f"OISST cache has {len(gaps)} missing days, first {gaps[0].date()}")
    pixels = archive.ocean_pixels()
    avhrr = qc.load_avhrr(sst_qc.AVHRR_CSV)

    failed = []
    for station in sst_qc.QC_STATIONS:
        path = FIXED_DIR / f"{station}.csv"
        if not path.exists():
            continue
        try:
            rows = sst_qc.flag_station_csv(path, archive, pixels, avhrr, today)
        except Exception as exc:  # one bad station must not block the others
            logger.error(f"❌ SST QC failed for {station}: {exc!r}")
            failed.append(path)
            continue
        logger.info(f"🔎 SST QC {station}: {rows} readings flagged")
    return failed


@task(name="publish-to-erddap", cache_policy=NO_CACHE)
def publish(exclude=()):
    logger = get_run_logger()
    files = [p for p in sorted(FIXED_DIR.glob("*.csv")) if p not in set(exclude)]
    return publish_files(files, DATASET_NAME, pipeline="meds", logger=logger)


@flow(name="meds-buoy-pipeline")
def meds_pipeline():
    logger = get_run_logger()
    logger.info("🌊 Starting MEDS buoy pipeline")
    stations = _stations_from_env()
    if stations:
        logger.info(f"Station allowlist active: {stations}")
    download(stations=stations)
    fix()
    # A station whose QC failed is not published, so its previously published
    # file (and flags) stays in place; the run still ends Failed to surface it.
    # If the QC as a whole fails (e.g. OISST unavailable), only the QC stations
    # are held back; the rest of the archive still publishes.
    try:
        failed = qc_sst()
    except Exception as exc:
        logger.error(f"❌ SST QC failed: {exc!r}")
        failed = [FIXED_DIR / f"{s}.csv" for s in sst_qc.QC_STATIONS]
    published = publish(exclude=failed)
    if failed:
        raise RuntimeError(f"SST QC failed for {', '.join(p.stem for p in failed)}; not republished")
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
