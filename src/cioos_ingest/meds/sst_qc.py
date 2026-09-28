"""Run the buoy SST QC (``qc.py``) on the fixed MEDS station CSVs.

Reproduces the legacy nightly job (cioos-siooc/dfo_buoy_qc_operationalize) for
the 17 Pacific ``C*`` buoys it covered: each reading's SSTP is compared against
NOAA OISST (``oisst.py``) and the static AVHRR record, and the result is written
into the station CSV's ``SSTP_flags`` (1-16, higher is better) and ``SSTP_UQL``
(QARTOD: 1 pass, 2 not evaluated, 3 suspect, 4 fail) columns. Rows the QC does
not cover (no SSTP, or newer than the last OISST day) keep both columns empty.
"""

import os
from pathlib import Path

import pandas as pd

from cioos_ingest.meds import oisst, qc

# The stations the legacy job QC'd (its stations.txt); COASTAL_STATIONS in qc.py is a subset.
QC_STATIONS = (
    "C46004", "C46036", "C46131", "C46132", "C46134", "C46145", "C46146", "C46147",
    "C46181", "C46183", "C46184", "C46185", "C46204", "C46205", "C46206", "C46207",
    "C46208",
)
# AVHRR daily SST at each station, static (ends 2020-12-31), from dfo_buoy_qc_operationalize
AVHRR_CSV = Path(__file__).parent / "data" / "avhrr_daily_by_station.csv"
FLAG_COLUMNS = ("SSTP_flags", "SSTP_UQL")


def observations(df):
    """The columns the QC needs from a fixed station CSV (read as strings)."""
    return pd.DataFrame({
        "STN_ID": df["STN_ID"],
        "time": pd.to_datetime(df["DATE"], utc=True).dt.tz_localize(None),
        "latitude": pd.to_numeric(df["LATITUDE"]).astype("float32"),
        "longitude": pd.to_numeric(df["LONGITUDE"]).astype("float32"),
        "SSTP": pd.to_numeric(df["SSTP"]).astype("float32"),
    })


def flag_station_csv(path, archive, pixels, avhrr, today):
    """QC one station CSV and fill its flag columns in place. Returns the number of rows flagged."""
    path = Path(path)
    station = path.stem
    # strings throughout so every other cell is written back byte for byte
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    rows = observations(df)
    obs = qc.prepare(rows, archive.latest_date())
    with_sst = obs[obs["SSTP"].notna()]
    if with_sst.empty:
        flags = pd.DataFrame(columns=["time", *FLAG_COLUMNS])
    else:
        first = with_sst.iloc[0]
        lat, lon = oisst.closest_pixel(first["latitude"], first["longitude"], pixels)
        flags = qc.qc_station(obs, archive.pixel_series(lat, lon), avhrr.get(station), station, today)

    # qc.prepare keeps the first of duplicate timestamps; duplicates share its flags
    by_time = flags.set_index("time")
    for col in FLAG_COLUMNS:
        df[col] = by_time[col].reindex(rows["time"]).astype("Int64").set_axis(df.index)

    tmp = path.with_suffix(".csv.part")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return len(flags)
