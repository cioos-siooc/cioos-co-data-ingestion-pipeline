#!/usr/bin/env python3
"""
Mirror Argo Canada profile NetCDF files from a GDAC.

MEDS (DFO's Marine Environmental Data Section) is Canada's Argo Data Assembly
Centre; the authoritative archive of its floats is the ``dac/meds/`` tree at the
Argo Global Data Assembly Centres (Ifremer and US GODAE, byte-identical, synced
daily). Each float directory holds an aggregated ``<WMO>_prof.nc`` — all cycles,
all core parameters, real-time and delayed-mode QC rolled up — which is the
natural per-float harvest unit.

Same mirroring pattern as ``meds_download.py``: scrape the Apache index for the
float directories, then conditional-GET (``If-Modified-Since``) each float's
``_prof.nc`` so unchanged files are not re-downloaded.
"""

import os
import re
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import requests

# Ifremer/Coriolis GDAC; US GODAE mirror: https://usgodae.org/pub/outgoing/argo
GDAC_URL = "https://data-argo.ifremer.fr"
DAC_PATH = "dac/meds"

# Float dirs in the Apache index are WMO numbers, e.g. href="4902674/"
_FLOAT_HREF_RE = re.compile(r'href="(\d{5,7})/"')


def list_float_ids(base_url=GDAC_URL, dac_path=DAC_PATH, session=None):
    """Return the WMO float ids listed in the GDAC's dac/meds/ index."""
    session = session or requests
    url = f"{base_url.rstrip('/')}/{dac_path}/"
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    return parse_float_ids(resp.text)


def parse_float_ids(index_html):
    """Extract WMO float ids from an Apache index page (de-duped, in order)."""
    ids = []
    seen = set()
    for wmo in _FLOAT_HREF_RE.findall(index_html):
        if wmo not in seen:
            seen.add(wmo)
            ids.append(wmo)
    return ids


def select_floats(float_ids, wanted=None, limit=None):
    """
    Restrict the float list for a run.

    ``wanted``: iterable of WMO ids (from ARGO_FLOATS) — takes precedence.
    ``limit``: keep only the first N ids (from ARGO_FLOAT_LIMIT); 0/None = all.
    """
    if wanted:
        wanted = {str(w).strip() for w in wanted}
        return [f for f in float_ids if f in wanted]
    if limit:
        return float_ids[: int(limit)]
    return float_ids


def download_profiles(float_ids, nc_dir, base_url=GDAC_URL, dac_path=DAC_PATH,
                      session=None, logger=None):
    """
    Mirror each float's aggregated ``<WMO>_prof.nc`` into ``nc_dir``.

    Conditional GETs (If-Modified-Since keyed on the local file's mtime) make
    re-runs cheap; the server's Last-Modified is copied onto the local file so
    the next run's header is right. Floats without a ``_prof.nc`` (404) are
    skipped with a warning. Returns the list of local Paths present after the run.
    """
    session = session or requests
    nc_dir = Path(nc_dir)
    nc_dir.mkdir(parents=True, exist_ok=True)

    local_paths = []
    for wmo in float_ids:
        url = f"{base_url.rstrip('/')}/{dac_path}/{wmo}/{wmo}_prof.nc"
        dest = nc_dir / f"{wmo}_prof.nc"
        headers = {}
        if dest.exists():
            headers["If-Modified-Since"] = formatdate(dest.stat().st_mtime, usegmt=True)

        try:
            resp = session.get(url, headers=headers, timeout=300)
        except requests.RequestException as exc:
            if logger:
                logger.warning(f"⚠️ Failed to fetch {wmo}_prof.nc: {exc}")
            if dest.exists():
                local_paths.append(dest)
            continue

        if resp.status_code == 304:
            if logger:
                logger.debug(f"↳ unchanged: {wmo}_prof.nc")
            local_paths.append(dest)
            continue
        if resp.status_code == 404:
            if logger:
                logger.warning(f"⚠️ No _prof.nc for float {wmo} (404), skipping")
            continue
        resp.raise_for_status()

        dest.write_bytes(resp.content)
        last_mod = resp.headers.get("Last-Modified")
        if last_mod:
            try:
                ts = parsedate_to_datetime(last_mod).timestamp()
                os.utime(dest, (ts, ts))
            except (TypeError, ValueError):
                pass
        if logger:
            logger.info(f"📥 downloaded: {wmo}_prof.nc ({len(resp.content)} bytes)")
        local_paths.append(dest)

    if logger:
        logger.info(f"Mirrored {len(local_paths)} float profile file(s) into {nc_dir}")
    return local_paths
