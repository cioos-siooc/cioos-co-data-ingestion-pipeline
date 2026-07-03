#!/usr/bin/env python3
"""
Download and header-normalise MEDS buoy CSV data.

Pure-Python replacement for the old ``meds_download.sh`` (wget + unzip + gsed):
 - list the MEDS ``CSVDATA/`` Apache index and download each ``*_csv.zip`` with a
   conditional GET (``If-Modified-Since``) so only changed zips re-download — the
   ``wget -N`` behaviour;
 - extract the zips to a working ``csv/`` dir (lowercased names);
 - normalise the CSV header row: strip the trailing comma, and for modern ``C*``
   buoys force the canonical 23-column header (the raw header has ``$`` suffixes
   and duplicate column names for the secondary sensor set).

Data-level fixes (dates, coordinates, column ordering) live in ``meds_fix.py``.
"""

import os
import re
import zipfile
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import requests

CSVDATA_URL = "https://www.meds-sdmm.dfo-mpo.gc.ca/alphapro/wave/waveshare/CSVDATA/"

# Canonical header for modern "C" buoys. The raw files ship this with `$`
# suffixes (VWH$, VTP$, WSS$) and repeated names for the secondary wind/pressure
# sensors, plus a trailing comma — see the sample files in buoy_data_ingest.
C_BUOY_HEADER = (
    "STN_ID,DATE,Q_FLAG,LATITUDE,LONGITUDE,DEPTH,VCAR,VTPK,VWH,VCMX,VTP,"
    "WDIR,WSPD,WSS,GSPD,WDIR_2,WSPD_2,WSS_2,GSPD_2,ATMS,ATMS_2,DRYT,SSTP"
)

_HREF_RE = re.compile(r'href="([^"]+?_csv\.zip)"', re.IGNORECASE)


def list_zip_names(base_url=CSVDATA_URL, session=None):
    """Return the list of ``*_csv.zip`` filenames in the MEDS CSVDATA index."""
    session = session or requests
    resp = session.get(base_url, timeout=60)
    resp.raise_for_status()
    # The index links may be relative ("c44131_csv.zip") or absolute; keep the
    # basename only and de-duplicate while preserving order.
    names = []
    seen = set()
    for href in _HREF_RE.findall(resp.text):
        name = os.path.basename(href)
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def _station_of(zip_name):
    """`c44131_csv.zip` -> `c44131` (used for allowlist filtering)."""
    return re.sub(r"_csv\.zip$", "", zip_name, flags=re.IGNORECASE).lower()


def download_zips(zip_dir, base_url=CSVDATA_URL, stations=None, session=None, logger=None):
    """
    Mirror the ``*_csv.zip`` files into ``zip_dir`` using conditional GETs.

    ``stations``: optional iterable of station prefixes (lowercased, no ``_csv.zip``)
    to restrict the download — handy for dev runs against a couple of buoys.
    Returns the list of local zip Paths that exist after the run.
    """
    session = session or requests
    zip_dir = Path(zip_dir)
    zip_dir.mkdir(parents=True, exist_ok=True)
    wanted = {s.lower() for s in stations} if stations else None

    names = list_zip_names(base_url, session=session)
    if wanted is not None:
        names = [n for n in names if _station_of(n) in wanted]

    local_paths = []
    for name in names:
        url = base_url.rstrip("/") + "/" + name
        dest = zip_dir / name.lower()
        headers = {}
        if dest.exists():
            headers["If-Modified-Since"] = formatdate(dest.stat().st_mtime, usegmt=True)

        try:
            resp = session.get(url, headers=headers, timeout=120)
        except requests.RequestException as exc:
            if logger:
                logger.warning(f"⚠️ Failed to fetch {name}: {exc}")
            if dest.exists():
                local_paths.append(dest)
            continue

        if resp.status_code == 304:
            if logger:
                logger.debug(f"↳ unchanged: {name}")
            local_paths.append(dest)
            continue
        resp.raise_for_status()

        dest.write_bytes(resp.content)
        # Preserve the server mtime so the next run's If-Modified-Since works.
        last_mod = resp.headers.get("Last-Modified")
        if last_mod:
            try:
                ts = parsedate_to_datetime(last_mod).timestamp()
                os.utime(dest, (ts, ts))
            except (TypeError, ValueError):
                pass
        if logger:
            logger.info(f"📥 downloaded: {name} ({len(resp.content)} bytes)")
        local_paths.append(dest)

    if logger:
        logger.info(f"Mirrored {len(local_paths)} zip(s) into {zip_dir}")
    return local_paths


def extract_zips(zip_dir, csv_dir, logger=None):
    """Extract every zip in ``zip_dir`` into ``csv_dir`` with lowercased names."""
    zip_dir = Path(zip_dir)
    csv_dir = Path(csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for zpath in sorted(zip_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(zpath) as zf:
                for member in zf.namelist():
                    if member.endswith("/"):
                        continue
                    target = csv_dir / os.path.basename(member).lower()
                    with zf.open(member) as src, open(target, "wb") as out:
                        out.write(src.read())
                    count += 1
        except zipfile.BadZipFile:
            if logger:
                logger.warning(f"⚠️ Skipping bad zip: {zpath.name}")
    if logger:
        logger.info(f"Extracted {count} CSV file(s) into {csv_dir}")
    return csv_dir


def normalize_headers(csv_dir, logger=None):
    """
    Rewrite the header row of each extracted CSV in place.

    - modern ``c*`` files: replace the whole header with ``C_BUOY_HEADER`` (drops
      ``$`` suffixes, renames the duplicated secondary-sensor columns to ``*_2``);
    - historic ``meds*``/``wel*`` files: just strip the trailing comma.

    Everything downstream (``meds_fix``) can then rely on clean, unique column
    names that match the ERDDAP dataset definition.
    """
    csv_dir = Path(csv_dir)
    for path in sorted(csv_dir.glob("*.csv")):
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            lines = fh.readlines()
        if not lines:
            continue

        name = path.name.lower()
        if name.startswith("c"):
            lines[0] = C_BUOY_HEADER + "\n"
        else:
            # strip a trailing comma (and any trailing whitespace) from the header
            lines[0] = re.sub(r",\s*$", "", lines[0].rstrip("\r\n")) + "\n"

        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(lines)
    if logger:
        logger.info(f"Normalised headers in {csv_dir}")
    return csv_dir
