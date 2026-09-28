"""OISST cache (PSL download faked) and the SST QC applied to fixed MEDS CSVs."""

import datetime as dt
import io

import numpy as np
import pandas as pd
import pytest
import requests
import xarray

from cioos_ingest.meds import fix as meds_fix
from cioos_ingest.meds import oisst, sst_qc

TODAY = dt.date(2026, 9, 25)


def psl_grid(start, end, land=((50.125, 230.125),)):
    """A small PSL-like yearly file, wider than the crop box, with some land (NaN) cells."""
    time = pd.date_range(start, end, freq="D")
    lat = np.arange(38.125, 62, 0.25, dtype="float32")
    lon = np.arange(218.125, 242, 0.25, dtype="float32")
    sst = np.broadcast_to(
        np.arange(len(time), dtype="float32")[:, None, None], (len(time), len(lat), len(lon))
    ).copy()
    ds = xarray.Dataset({"sst": (("time", "lat", "lon"), sst)}, coords={"time": time, "lat": lat, "lon": lon})
    for la, lo in land:
        ds["sst"].loc[dict(lat=la, lon=lo)] = np.nan
    return ds


class FakeSession:
    """Serves psl_grid for the year in the URL; `missing` years answer 404, `flaky` ones 504 once."""

    def __init__(self, tmp_path, missing=(), flaky=()):
        self.tmp_path, self.missing, self.flaky = tmp_path, set(missing), set(flaky)

    def get(self, url, stream=False, timeout=None):
        year = int(url.rsplit(".", 2)[-2])
        resp = requests.Response()
        resp.url, resp.raw = url, io.BytesIO()
        if year in self.missing:
            resp.status_code = 404
            return resp
        if year in self.flaky:
            self.flaky.discard(year)
            resp.status_code = 504
            return resp
        path = self.tmp_path / f"psl-{year}.nc"
        psl_grid(f"{year}-01-01", f"{year}-12-31").to_netcdf(path)
        resp.status_code, resp._content, resp._content_consumed = 200, path.read_bytes(), True
        return resp


def test_years_to_refresh(tmp_path):
    for y in (1981, 1982, 2024):
        (tmp_path / f"sst.day.mean.{y}.nc").touch()
    missing = list(range(1983, 2024))
    assert oisst.years_to_refresh(str(tmp_path), TODAY) == missing + [2025, 2026]
    # early in the year the previous year is refreshed too
    assert oisst.years_to_refresh(str(tmp_path), dt.date(2025, 1, 20)) == missing + [2024, 2025]


@pytest.fixture
def archive(tmp_path):
    session = FakeSession(tmp_path)
    for year in (1989, 1990):
        oisst.download_year(year, str(tmp_path / "oisst"), session=session)
    return oisst.Archive.from_dir(str(tmp_path / "oisst"))


def test_download_keeps_the_legacy_box(archive):
    assert archive.latest_date() == pd.Timestamp("1990-12-31")
    assert len(archive.missing_days()) > 0  # nothing before 1989 here
    with xarray.open_dataset(archive.paths[0]) as ds:
        assert ds.sizes["lat"] == 80 and ds.sizes["lon"] == 80
        assert float(ds.lat.min()) == 40.125 and float(ds.lon.max()) == 239.875


def test_ocean_pixels_and_closest(archive):
    pixels = archive.ocean_pixels()
    assert len(pixels) == 80 * 80 - 1  # the land cell is dropped
    assert oisst.closest_pixel(np.float32(49.91), np.float32(-124.99), pixels) == (49.875, 235.125)
    assert oisst.closest_pixel(np.float32(50.13), np.float32(-129.87), pixels) != (50.125, 230.125)


def test_pixel_series(archive):
    s = archive.pixel_series(49.875, 235.125)
    assert s.index[0] == pd.Timestamp("1989-01-01") and s.index[-1] == pd.Timestamp("1990-12-31")
    assert len(s) == 365 * 2


def test_refresh_skips_current_year_without_data(tmp_path):
    session = FakeSession(tmp_path, missing={2026})
    for y in range(1981, 2024):
        (tmp_path / f"sst.day.mean.{y}.nc").touch()
    assert oisst.refresh(str(tmp_path), dt.date(2026, 1, 2), session=session) == [2024, 2025]
    session = FakeSession(tmp_path, missing={2025})
    with pytest.raises(requests.HTTPError):
        oisst.refresh(str(tmp_path), dt.date(2026, 1, 2), session=session)


def test_refresh_retries_transient_errors(tmp_path):
    for y in range(1981, 2025):
        (tmp_path / f"sst.day.mean.{y}.nc").touch()
    session = FakeSession(tmp_path, flaky={2025})
    assert oisst.refresh(str(tmp_path), dt.date(2025, 6, 1), session=session, backoff=0) == [2025]


class FakeArchive:
    """Stands in for oisst.Archive: one pixel whose SST tracks the buoy."""

    def __init__(self, sat):
        self.sat = sat

    def latest_date(self):
        return self.sat.index.max()

    def pixel_series(self, lat, lon):
        assert (lat, lon) == (50.875, 230.125)
        return self.sat


def fixed_csv(path, station="C46207", sstp=True):
    """Three hourly years in the fixed-CSV layout, and the matching OISST/AVHRR series."""
    rng = np.random.default_rng(0)
    t = pd.date_range("2009-01-01", "2011-12-31 23:00", freq="h")
    doy = t.dayofyear.to_numpy()
    sst = 10 + 4 * np.sin(2 * np.pi * (doy - 100) / 365) + rng.normal(0, 0.05, len(t))
    df = pd.DataFrame({
        "STN_ID": station,
        "DATE": t.strftime(meds_fix.DATE_FORMAT),
        "Q_FLAG": "1",
        "LATITUDE": "50.88",
        "LONGITUDE": "-129.91",
        "SSTP": np.round(sst, 2) if sstp else np.nan,
        "WSPD": "10000.0",
    }).reindex(columns=meds_fix.COLUMN_ORDER)
    df.to_csv(path, index=False)
    daily = pd.Series(sst, index=t).resample("D").mean()
    return (daily + rng.normal(0, 0.2, len(daily))), (daily + rng.normal(0, 0.2, len(daily)))


def test_flag_station_csv(tmp_path):
    path = tmp_path / "C46207.csv"
    sat, avhrr = fixed_csv(path)
    before = pd.read_csv(path, dtype=str, keep_default_na=False)
    sat = sat[:"2011-06-30"]  # later readings are past the last OISST day
    pixels = np.array([[50.875, 230.125], [45.125, 225.125]])

    rows = sst_qc.flag_station_csv(path, FakeArchive(sat), pixels, {"C46207": avhrr}, TODAY)

    after = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(after.columns) == meds_fix.COLUMN_ORDER
    # every other cell is untouched
    pd.testing.assert_frame_equal(after.drop(columns=list(sst_qc.FLAG_COLUMNS)),
                                  before.drop(columns=list(sst_qc.FLAG_COLUMNS)))
    # as in the legacy job: readings up to 00:00 of the last OISST day
    evaluated = after["DATE"] <= "2011-06-30T00:00:00Z"
    assert rows == evaluated.sum()
    assert (after.loc[evaluated, "SSTP_flags"] != "").all()
    assert (after.loc[~evaluated, list(sst_qc.FLAG_COLUMNS)] == "").all().all()
    flags = after.loc[evaluated, "SSTP_flags"].astype(int)
    assert (flags == 16).mean() > 0.95
    assert set(after.loc[evaluated & (after["SSTP_flags"] == "16"), "SSTP_UQL"]) == {"1"}


def test_station_without_sstp_keeps_flags_empty(tmp_path):
    path = tmp_path / "C46207.csv"
    sat, avhrr = fixed_csv(path, sstp=False)
    assert sst_qc.flag_station_csv(path, FakeArchive(sat), np.array([[50.875, 230.125]]), {}, TODAY) == 0
    after = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert (after[list(sst_qc.FLAG_COLUMNS)] == "").all().all()


def test_flow_publishes_non_qc_stations_when_qc_fails(tmp_path, monkeypatch):
    from prefect.testing.utilities import prefect_test_harness

    from cioos_ingest.meds import flow

    fixed = tmp_path / "fixed"
    fixed.mkdir()
    for name in ("C46207", "C44131"):  # a QC station and a non-QC one
        (fixed / f"{name}.csv").write_text("STN_ID\n")
    monkeypatch.setattr(flow, "FIXED_DIR", fixed)
    monkeypatch.setattr(flow, "download", lambda stations=None: None)
    monkeypatch.setattr(flow, "fix", lambda: fixed)

    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("PSL down")

    monkeypatch.setattr(flow.oisst, "refresh", unavailable)
    monkeypatch.setattr(flow.qc_sst, "retries", 0)
    monkeypatch.setenv("MEDS_PUBLISH_URL", f"file://{tmp_path / 'out'}")

    with prefect_test_harness(), pytest.raises(RuntimeError, match="C46207"):
        flow.meds_pipeline()
    assert [p.name for p in (tmp_path / "out" / flow.DATASET_NAME).iterdir()] == ["C44131.csv"]
