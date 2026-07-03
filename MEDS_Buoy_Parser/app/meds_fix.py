#!/usr/bin/env python3
"""
Fix up MEDS buoy CSV files (ported from buoy_data_ingest ``meds_csv_fix.py``):

 - convert ``DATE`` to ISO 8601 (UTC) and drop records with invalid datetimes
   (e.g. ``01/10/1988 00:84``);
 - flip longitude sign (MEDS stores degrees **west** as positive) and keep the
   original per-record coordinate as ``preciseLat`` / ``preciseLon``;
 - set a fixed lat/lon from the most-recent deployment metadata for each buoy;
 - normalise **every** file (modern ``C*`` and historic ``MEDS*``/``WEL*``) to a
   single superset column order so one ERDDAP ``EDDTableFromAsciiFiles`` dataset
   can serve them all — historic buoys simply have empty wind/pressure/temp cells.

Output: ``<output_dir>/<STN_ID>.csv`` per station, ready for ERDDAP.
"""

import glob
import os
from pathlib import Path

import pandas as pd
import requests

INVENTORY_URL = (
    "https://www.meds-sdmm.dfo-mpo.gc.ca/alphapro/wave/waveshare/INVENTORY/b_pw_inv.json"
)

# Unified superset column order for ALL buoy types. Modern "C" buoys populate
# every column; historic MEDS/WEL buoys only carry the wave columns and leave the
# rest empty. preciseLat/preciseLon hold the raw per-record coordinate.
COLUMN_ORDER = [
    "STN_ID",
    "DATE",
    "Q_FLAG",
    "LATITUDE",
    "LONGITUDE",
    "DEPTH",
    "VCAR",
    "VTPK",
    "VWH",
    "VCMX",
    "VTP",
    "WDIR",
    "WSPD",
    "WSS",
    "GSPD",
    "WDIR_2",
    "WSPD_2",
    "WSS_2",
    "GSPD_2",
    "ATMS",
    "ATMS_2",
    "DRYT",
    "SSTP",
    "preciseLat",
    "preciseLon",
]

# ISO 8601 UTC — matches the `units` attribute in the ERDDAP dataset fragment.
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def get_deployment_metadata(session=None):
    """Most-recent deployment lat/lon/depth per station from the MEDS inventory."""
    session = session or requests
    res = session.get(INVENTORY_URL, timeout=60).json()["data"]

    df = pd.DataFrame(
        res,
        columns=[
            "station_name",
            "station",
            "type",
            "date_start",
            "date_end",
            "lat",
            "lon",
            "depth",
            "days",
        ],
    ).set_index("station", drop=False)

    df["date_start"] = pd.to_datetime(df.date_start).dt.tz_localize(None)
    df["date_end"] = pd.to_datetime(df.date_end).dt.tz_localize(None)

    # when there are multiple deployments, keep the most recent one
    df = df.sort_values(by="date_end").drop_duplicates("station", keep="last")
    return df


def fix_csv_file(file, df_metadata, output_dir, logger=None):
    """Fix a single normalised MEDS CSV; returns the output Path or None if skipped."""
    output_dir = Path(output_dir)
    station = os.path.basename(file)[:-4].upper()

    df = pd.read_csv(file, dtype=str)

    num_coordinates = len(df[["LATITUDE", "LONGITUDE"]].drop_duplicates())

    # correct longitude (MEDS stores degrees west as positive) and keep raw coords
    df["LONGITUDE"] = -df["LONGITUDE"].astype("double")
    df["preciseLat"] = df["LATITUDE"]
    df["preciseLon"] = df["LONGITUDE"]

    if station in df_metadata.index:
        df["LATITUDE"] = df_metadata.loc[station]["lat"]
        df["LONGITUDE"] = df_metadata.loc[station]["lon"]
    elif num_coordinates == 1:
        # single coordinate and no deployment metadata: precise == lat/lon already
        if logger:
            logger.debug(f"Single-coordinate station without metadata: {station}")
    else:
        if logger:
            logger.warning(f"Missing metadata for {station} with multiple coords; skipping")
        return None

    # remove bad dates, e.g. "01/10/1988 00:84", and write ISO 8601 UTC
    parsed = pd.to_datetime(df.DATE, errors="coerce").dt.tz_localize(None)
    df["DATE"] = parsed
    df = df[df.DATE.notna()].copy()
    df["DATE"] = df.DATE.dt.strftime(DATE_FORMAT)

    # reindex to the unified column order (missing columns -> empty)
    df_out = df.reindex(columns=COLUMN_ORDER)

    outfile = output_dir / f"{station}.csv"
    df_out.to_csv(outfile, index=False)
    if logger:
        logger.info(f"✅ wrote {outfile.name} ({len(df_out)} rows)")
    return outfile


def fix_all(input_dir, output_dir, df_metadata=None, session=None, logger=None):
    """Fix every ``*.csv`` in ``input_dir`` into ``output_dir``."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if df_metadata is None:
        df_metadata = get_deployment_metadata(session=session)

    written = []
    for file in sorted(glob.glob(os.path.join(input_dir, "*.csv"))):
        try:
            out = fix_csv_file(file, df_metadata, output_dir, logger=logger)
        except Exception as exc:  # keep going on a single bad file
            if logger:
                logger.warning(f"⚠️ Failed to fix {os.path.basename(file)}: {exc}")
            continue
        if out:
            written.append(out)
    if logger:
        logger.info(f"Fixed {len(written)} file(s) into {output_dir}")
    return written
