"""NOAA OISST v2 high-res daily SST over the NE Pacific, cached as one small NetCDF per year.

The SST QC (``qc.py``) compares each buoy's daily mean against the OISST cell
nearest the buoy. Each yearly global file from NOAA PSL (~450 MB, the legacy
job's source) is cropped to the legacy ``cdo sellonlatbox,220,240,40,60`` box
and kept as ``sst.day.mean.YYYY.nc`` (~10 MB), so a missing year is simply
fetched again. (CoastWatch ERDDAP serves the same values far cheaper, but its
aggregation misses ~1,200 days, mostly 1992-1998.)

Adapted from cioos-siooc/cioos-pacific-pipeline (pipelines/buoy_qc/oisst.py).
"""

import datetime as dt
import glob
import os
import tempfile
import time

import numpy as np
import pandas as pd
import requests
import xarray

DATASET = "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres"
FIRST_DAY = dt.date(1981, 9, 1)
# same box as `cdo sellonlatbox,220,240,40,60`: 80 x 80 cells
LAT = slice(40, 60)
LON = slice(220, 240)
# re-download the previous year while its last weeks may still be preliminary
PRELIMINARY_DAYS = 45


def year_path(oisst_dir, year):
    return os.path.join(oisst_dir, f"sst.day.mean.{year}.nc")


def crop(ds):
    return ds[["sst"]].sel(lat=LAT, lon=LON)


def download_year(year, oisst_dir, session=None):
    """Download one global year file and keep the cropped box. Returns days stored."""
    session = session or requests
    os.makedirs(oisst_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=oisst_dir, suffix=".global.nc") as tmp:
        with session.get(f"{DATASET}/sst.day.mean.{year}.nc", stream=True, timeout=600) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(1 << 20):
                tmp.write(chunk)
        tmp.flush()
        with xarray.open_dataset(tmp.name) as ds:
            cropped = crop(ds).load()
    dest = year_path(oisst_dir, year)
    cropped.to_netcdf(dest + ".part", encoding={"sst": {"zlib": True, "complevel": 4}})
    os.replace(dest + ".part", dest)
    return cropped.time.size


def years_to_refresh(oisst_dir, today):
    """Years that are missing, plus the current (and recently ended) year."""
    recent = (today - dt.timedelta(days=PRELIMINARY_DAYS)).year
    return [
        y for y in range(FIRST_DAY.year, today.year + 1)
        if y >= recent or not os.path.exists(year_path(oisst_dir, y))
    ]


def _transient(exc):
    """PSL answers the odd 5xx or drops a connection during a long backfill."""
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code >= 500
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


def refresh(oisst_dir, today, session=None, logger=None, attempts=4, backoff=30):
    """Bring the per-year cache up to date. Returns the years downloaded."""
    done = []
    for year in years_to_refresh(oisst_dir, today):
        for attempt in range(1, attempts + 1):
            try:
                days = download_year(year, oisst_dir, session=session)
                break
            except requests.RequestException as exc:
                # early January: the new year's file does not exist yet
                if (year == today.year and isinstance(exc, requests.HTTPError)
                        and exc.response is not None and exc.response.status_code == 404):
                    days = None
                    break
                if not _transient(exc) or attempt == attempts:
                    raise
                if logger:
                    logger.warning(f"OISST {year}: {exc!r}, retrying ({attempt}/{attempts - 1})")
                time.sleep(backoff * attempt)
        if days is None:
            continue
        done.append(year)
        if logger:
            logger.info(f"OISST {year}: {days} days")
    return done


class Archive:
    """Read access to the per-year files."""

    def __init__(self, paths):
        if not paths:
            raise FileNotFoundError("no OISST files")
        self.paths = sorted(paths)

    @classmethod
    def from_dir(cls, oisst_dir):
        return cls(glob.glob(os.path.join(oisst_dir, "sst.day.mean.[0-9][0-9][0-9][0-9].nc")))

    def times(self):
        out = []
        for p in self.paths:
            with xarray.open_dataset(p) as ds:
                out.append(pd.DatetimeIndex(ds.time.values))
        return out[0].append(out[1:]).sort_values()

    def latest_date(self):
        return self.times().max()

    def missing_days(self):
        """Days without a grid between the first day of record and the latest day."""
        have = self.times().normalize()
        return pd.date_range(FIRST_DAY, have.max(), freq="D").difference(have)

    def ocean_pixels(self, day="1990-01-01"):
        """(lat, lon) of every non-null cell on `day`, in lat-major order, as float64."""
        for p in self.paths:
            with xarray.open_dataset(p) as ds:
                if np.datetime64(day) in ds.time.values:
                    grid = ds.sst.sel(time=day).to_series().dropna()
                    return np.array([[lat, lon] for lat, lon in grid.index], dtype="float64")
        raise LookupError(f"no OISST grid for {day}")

    def pixel_series(self, lat, lon):
        """Daily SST at one cell, indexed by day, NaN days dropped."""
        parts = []
        for p in self.paths:
            with xarray.open_dataset(p) as ds:
                parts.append(ds.sst.sel(lat=lat, lon=lon).to_series())
        s = pd.concat(parts).dropna().sort_index()
        s.index = pd.DatetimeIndex(s.index).normalize()
        return s[~s.index.duplicated(keep="last")]


def closest_pixel(lat, lon, pixels):
    """Nearest cell by squared degree distance, lon in 0-360 (as the original closest_point)."""
    node = np.array([lat, np.float32(360) + np.float32(lon)], dtype="float64")
    return tuple(pixels[np.argmin(np.sum((pixels - node) ** 2, axis=1))])
