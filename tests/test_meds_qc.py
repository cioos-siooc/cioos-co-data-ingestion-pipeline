"""QC algorithm on a synthetic station, plus its building blocks (vendored with qc.py from cioos-pacific-pipeline)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from cioos_ingest.meds import qc

STATION = "C46207"  # not coastal, has AVHRR checks
# 2011 is outside the 1981-2010 AVHRR climatology, so drift there is judged against clean years
START, END = "2009-01-01", "2011-12-31 23:00"


def synthetic(seed=0, noise=0.05):
    """Three years of hourly SST with a seasonal cycle and small noise."""
    rng = np.random.default_rng(seed)
    t = pd.date_range(START, END, freq="h")
    doy = t.dayofyear.to_numpy()
    sst = 10 + 4 * np.sin(2 * np.pi * (doy - 100) / 365) + rng.normal(0, noise, len(t))
    obs = pd.DataFrame({"STN_ID": STATION, "time": t, "latitude": np.float32(50.88), "longitude": np.float32(-129.91), "SSTP": sst.astype("float32")})
    daily = obs.set_index("time")["SSTP"].resample("D").mean()
    sat = (daily + rng.normal(0, 0.2, len(daily))).astype("float32")
    avhrr = (daily + rng.normal(0, 0.2, len(daily))).astype("float64")
    return obs, sat, avhrr


def run(obs, sat, avhrr, today=dt.date(2026, 9, 25), **kw):
    return qc.qc_station(obs, sat, avhrr, STATION, today, **kw).set_index("time")


def test_clean_data_is_mostly_good():
    out = run(*synthetic())
    assert len(out) == 26280
    assert (out["SSTP_flags"] == 16).mean() > 0.95
    assert set(out.loc[out["SSTP_flags"] == 16, "SSTP_UQL"]) == {1}


def test_temp_chop_and_isolated_spike():
    obs, sat, avhrr = synthetic()
    obs.loc[obs["time"] == pd.Timestamp("2010-03-01 05:00"), "SSTP"] = 35.0  # out of range
    obs.loc[obs["time"] == pd.Timestamp("2010-03-02 05:00"), "SSTP"] = -1.0
    spike_at = pd.Timestamp("2010-06-01 12:00")
    obs.loc[obs["time"] == spike_at, "SSTP"] += 3
    out = run(obs, sat, avhrr)
    assert out.loc["2010-03-01 05:00", "SSTP_flags"] == 1
    assert out.loc["2010-03-02 05:00", "SSTP_flags"] == 1
    assert out.loc["2010-03-01 05:00", "SSTP_UQL"] == 4
    # a 3 C one-hour jump is caught by the jump test and then re-checked in its 7-day window
    assert out.loc[spike_at, "SSTP_flags"] in (7, 8)
    assert out.loc[spike_at, "qc_flag"] in (9, 30)


def test_isolated_outlier_among_good_points():
    # noisy enough that a 2.2 C step after a 2-hour gap is not a jump (z <= 6), on a day
    # that is "good" vs the satellite: only then does the > 1.75 C both-sides rule decide
    obs, sat, avhrr = synthetic(noise=0.5)
    clean = run(obs, sat, avhrr)
    july = clean.loc["2010-07"]
    day = july.index[july["buoy_sat_diff_zscore"] < 1].normalize()[0]  # stays "good" with the spike
    t = pd.Timestamp(day) + pd.Timedelta("12h")
    obs = obs[~obs["time"].isin([t - pd.Timedelta("1h"), t + pd.Timedelta("1h")])].copy()
    before = obs.loc[obs["time"] == t - pd.Timedelta("2h"), "SSTP"].iloc[0]
    obs.loc[obs["time"] == t + pd.Timedelta("2h"), "SSTP"] = before
    obs.loc[obs["time"] == t, "SSTP"] = before + 2.2
    out = run(obs, sat, avhrr)
    assert out.loc[t, "temp_diff_zscore"] <= 6
    assert out.loc[t, "SSTP_flags"] == 10  # internal 88


def test_satellite_mismatch_flags_bad_days():
    obs, sat, avhrr = synthetic()
    sat.loc["2010-08-01":"2010-08-03"] -= 5
    out = run(obs, sat, avhrr)
    assert (out.loc["2010-08-01":"2010-08-03", "SSTP_flags"] == 3).all()  # internal 2, big satellite diff


def test_avhrr_drift_run_is_flagged():
    obs, sat, avhrr = synthetic()
    avhrr.loc["2011-02-01":"2011-03-15"] -= 3
    out = run(obs, sat, avhrr)
    drift = out.loc["2011-02-10":"2011-03-05", "SSTP_flags"]
    assert (drift == 2).mean() > 0.9
    assert (out.loc[:"2010-12-31", "SSTP_flags"] == 2).sum() == 0


def test_recent_and_post_avhrr_readings_not_evaluated():
    obs, sat, avhrr = synthetic()
    out = run(obs, sat, avhrr, today=dt.date(2010, 12, 31))
    assert (out.loc["2010-12-17 01:00":, "SSTP_UQL"] == 2).all()
    assert (out.loc["2010-06-01":"2010-06-30", "SSTP_UQL"] != 2).all()
    # after the last AVHRR day (2020-12-31) nothing is evaluated either
    shift = pd.DateOffset(years=11)  # 2020-2022: still overlaps the 1991-2020 jump climatology
    later = run(obs.assign(time=obs["time"] + shift), sat.set_axis(sat.index + shift).groupby(level=0).first().asfreq("D").ffill(), None)
    assert (later.loc["2021-01-01":, "SSTP_UQL"] == 2).all()
    assert (later.loc["2020-06-01":"2020-06-30", "SSTP_UQL"] != 2).all()


def test_missing_oisst_day_is_an_error():
    obs, sat, avhrr = synthetic()
    with pytest.raises(qc.QCError, match="OISST day"):
        run(obs, sat.drop(pd.Timestamp("2010-05-05")), avhrr)


def test_daily_flags_option_marks_double_data_days():
    obs, sat, avhrr = synthetic()
    extra = obs[obs["time"].dt.date == dt.date(2010, 4, 10)].copy()
    extra["time"] += pd.Timedelta("30min")
    obs = pd.concat([obs, extra]).sort_values("time").reset_index(drop=True)
    assert (run(obs, sat, avhrr).loc["2010-04-10", "SSTP_flags"] == 4).sum() == 0
    flags = run(obs, sat, avhrr, apply_daily_flags=True).loc["2010-04-10", "SSTP_flags"]
    assert (flags == 4).all()  # internal 20, double data day


@pytest.mark.parametrize(
    "z, coastal, expected",
    [(2.5, False, 4), (3.4, False, 3), (3.5, False, 2), (2.9, True, 4), (4.9, True, 3), (5.0, True, 2), (np.nan, False, 2), (np.nan, True, 2)],
)
def test_satellite_flag(z, coastal, expected):
    assert qc.satellite_flag([z], coastal)[0] == expected


def days(n, start="2010-01-01"):
    return pd.date_range(start, periods=n, freq="D")


def test_drift_ranges():
    d = days(40)
    bad = np.array([False] * 5 + [True] * 12 + [False] * 10 + [True] * 13)
    assert qc.drift_ranges(d, bad, pd.Timestamp("2010-03-01")) == [
        (d[5], d[16]),
        (d[27], pd.Timestamp("2010-03-01")),  # still drifting at end of record
    ]


def test_drift_ranges_short_runs_and_brief_recoveries():
    d = days(30)
    assert qc.drift_ranges(d, np.array([True] * 9 + [False] * 21), d[-1]) == []
    # 6 bad, 3 good (not a recovery), 5 bad: one 11-day drift that ends on its last bad day
    bad = np.array([True] * 6 + [False] * 3 + [True] * 5 + [False] * 16)
    assert qc.drift_ranges(d, bad, d[-1]) == [(d[0], d[13])]


def test_window_recheck_is_sequential():
    t = pd.date_range("2010-01-01", periods=11, freq="h")
    df = pd.DataFrame({"time": t, "SSTP": [10.0] * 5 + [15.0] + [10.0] * 3 + [10.05, 10.0], "qc_flag": [4] * 5 + [9] + [4] * 3 + [9, 4]})
    rows = df["qc_flag"].eq(9).to_numpy()
    out = qc.window_recheck(df, rows, min_points=5)
    assert out[5] == 30  # the 15 C jump is far from its neighbours
    assert out[9] == 31  # evaluated after the first decision, without it in the window


def test_nan_savgol_matches_scipy_and_spreads_nan():
    from scipy.signal import savgol_filter

    x = np.sin(np.linspace(0, 6, 60))
    np.testing.assert_allclose(qc.nan_savgol(x), savgol_filter(x, 11, 2))
    y = x.copy()
    y[30] = np.nan
    out = qc.nan_savgol(y)
    assert np.isnan(out[25:36]).all()
    np.testing.assert_allclose(out[:25], savgol_filter(x, 11, 2)[:25])
    assert np.isnan(qc.nan_savgol(np.full(20, np.nan))).all()


def test_station_without_avhrr_comparison_runs():
    obs, sat, avhrr = synthetic()
    out = qc.qc_station(obs.assign(STN_ID="C46181"), sat, avhrr, "C46181", dt.date(2026, 9, 25))
    assert (out["SSTP_flags"] == 2).sum() == 0
