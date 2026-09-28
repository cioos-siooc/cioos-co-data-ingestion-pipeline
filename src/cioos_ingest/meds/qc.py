"""Sea-surface temperature (SSTP) QC for one moored buoy.

Vendored unchanged from cioos-siooc/cioos-pacific-pipeline (pipelines/buoy_qc/qc.py),
where it is regression-tested row for row against the legacy job's published flags.
Keep it in sync with that copy rather than editing it here.

Port of buoy_qc_operationalize/src/SSTP_daily_OISST_V4.py (a notebook export run once
per station). Same steps, thresholds, flag codes and order; the row-by-row pandas
`apply` calls are vectorised and the two order-dependent steps (7-day window
re-check, AVHRR drift run detection) stay as loops.

Steps, with the internal flag each can set:
  1. daily buoy mean vs OISST daily SST -> z-score -> 4 good, 3 medium, 2 bad; 1 = out of 0-30 C
  2. hourly: big 1-hour jump (z >= 4.5 vs monthly jump stats) -> 9
     (optional daily flags: >24 readings 20, very spiky day 19, medium spiky 12-15)
  3. days with >20% jumps are "unprecise" -> 41
  4. jumps re-checked against +-7 days of good data -> 30 (worse) / 31 (ok)
  5. >=10-day runs where buoy - AVHRR is anomalous (Savitzky-Golay smoothed) -> 35
  6. isolated spikes among good points (> 1.75 C both sides) -> 88
Internal flags are remapped to 1-16 (SSTP_flags, higher is better) and QARTOD (SSTP_UQL).

apply_daily_flags: the original looked up daily stats with a key type that never
matched (logging "No stats for ..." for every day), so step 2's daily flags were never
applied in production. False reproduces the published flags; True applies them as designed.
"""

import datetime as dt

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

COASTAL_STATIONS = {"46131", "C46131", "C46132", "C46146", "C46181"}
CLIMATOLOGY = (pd.Timestamp("1991-01-01"), pd.Timestamp("2021-01-01"))
AVHRR_CLIMATOLOGY = (pd.Timestamp("1981-01-01"), pd.Timestamp("2011-01-01"))
AVHRR_START = pd.Timestamp("1987-09-22")
LAST_AVHRR_DATE = pd.Timestamp("2020-12-31")
NO_AVHRR_STATIONS = {"C46181"}
ONE_HOUR = pd.Timedelta("1h")

FLAG_REMAPPING = {
    1: 1,  # temp chop (<= 0 or >= 30)
    35: 2,  # satellite (AVHRR) drift
    2: 3,  # big satellite diff
    20: 4,  # double data day
    19: 5,  # way too spiky day (over 6 sigma)
    41: 6,  # unprecise day
    9: 7,  # big hourly jump, not re-checked (too few nearby points)
    30: 8,  # downgraded by window
    31: 9,  # upgraded by window
    88: 10,  # extreme outlier
    13: 11,  # medium spiky day, medium satellite diff, big jumps
    15: 12,  # medium spiky day, small satellite diff, big jumps
    12: 13,  # medium spiky day, medium satellite diff
    14: 14,  # medium spiky day, small satellite diff
    3: 15,  # medium satellite diff
    4: 16,  # small satellite diff
}
# SSTP_flags -> QARTOD: 1 pass, 2 not evaluated, 3 suspect, 4 fail
FLAG_QARTOD = {1: 4, 2: 4, 3: 4, **{f: 3 for f in range(4, 16)}, 16: 1}


class QCError(Exception):
    """Input the algorithm cannot handle (the original script would crash here too)."""


def _lookup(values, keys, what):
    out = values.reindex(keys)
    missing = keys[out.isna().to_numpy()] if out.isna().any() else keys[:0]
    if len(missing):
        raise QCError(f"no {what} for {len(missing)} key(s), first {missing[0]}")
    return out.to_numpy()


def satellite_flag(zscore, coastal):
    """4 good, 3 medium, 2 bad (NaN counts as bad, as in the original)."""
    z = np.asarray(zscore, dtype="float64")
    if coastal:
        return np.select([z <= 2.5, z < 3, z < 5], [4, 4, 3], 2)
    return np.select([z <= 2.5, z < 3.5], [4, 3], 2)


def daily_means(good, sat_sst, coastal):
    """Daily buoy mean vs OISST, z-scored over the station's whole record."""
    daily = good["SSTP"].resample("D").mean()
    sat = pd.Series(_lookup(sat_sst, daily.index, "OISST day"), index=daily.index)
    means = pd.DataFrame({"time": daily.index, "SSTP": daily, "sat_sst": sat})
    means = means[means["SSTP"].notna()].copy()
    means["buoy_sat_diff"] = means["SSTP"] - means["sat_sst"]
    means["year"] = means["time"].dt.year
    m, s = means["buoy_sat_diff"].mean(), means["buoy_sat_diff"].std()
    means["buoy_sat_diff_zscore"] = (means["buoy_sat_diff"] - m).abs() / s
    means["qc_flag"] = satellite_flag(means["buoy_sat_diff_zscore"], coastal)
    return means


def in_period(times, period):
    return (times >= period[0]) & (times < period[1])


def monthly(stats_by_month, months, what):
    """stats_by_month.loc[month] for each month; a month without stats is fatal (as before)."""
    return _lookup(stats_by_month, pd.Index(months), what)


def window_recheck(df, rows, days=7, min_points=50, max_z=3):
    """Re-flag jumps against good/jump points within +-days, in time order.

    Each decision changes qc_flag before the next window is evaluated (as the original did).
    """
    times = df["time"].to_numpy()
    sstp = df["SSTP"].to_numpy(dtype="float64")
    qc = df["qc_flag"].to_numpy().copy()
    span = np.timedelta64(days, "D")
    for i in np.flatnonzero(rows):
        lo = np.searchsorted(times, times[i] - span, side="right")
        hi = np.searchsorted(times, times[i] + span, side="left")
        sel = (qc[lo:hi] == 9) | (qc[lo:hi] == 4)
        vals = sstp[lo:hi][sel]
        if len(vals) < min_points:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            z = abs(sstp[i] - vals.mean()) / vals.std(ddof=1)
        qc[i] = 30 if z > max_z else 31
    return qc


# TODO(science): a run still open when AVHRR ends (2020-12-31) extends to last_date, flagging every later
# reading (C46131 since 2020-07). Decision pending, see README "Buoy QC science decisions".
def drift_ranges(dates, bad, last_date, min_days=10, recovery_days=10):
    """(start, end) date runs of >= min_days bad AVHRR days, ended by recovery_days good days."""
    ranges = []
    drifting, drift_count, good_count, start, end = False, 0, 0, None, None
    for day, is_bad in zip(dates, bad):
        if is_bad:
            good_count = 0
            if not drifting:
                start, drifting, drift_count = day, True, 0
            drift_count += 1
            end = day
        else:
            good_count += 1
            if good_count == recovery_days:
                if drift_count >= min_days:
                    ranges.append((start, end))
                drift_count, drifting = 0, False
    if drift_count >= min_days:
        ranges.append((start, last_date))
    return ranges


def nan_savgol(values, window_length=11, polyorder=2):
    """savgol_filter that lets NaN spread to every output whose window touches it.

    Older SciPy did this implicitly; current SciPy raises on NaN input instead.
    """
    x = np.asarray(values, dtype="float64")
    bad = ~np.isfinite(x)
    if not bad.any():
        return savgol_filter(x, window_length, polyorder)
    out = savgol_filter(np.where(bad, 0.0, x), window_length, polyorder)
    half = window_length // 2
    touched = np.convolve(bad, np.ones(window_length), mode="same") > 0
    # mode="interp": the first/last half-windows are fitted on the first/last full window
    touched[:half] = bad[:window_length].any()
    touched[-half:] = bad[-window_length:].any()
    out[touched] = np.nan
    return out


def avhrr_drift(means, avhrr, station):
    """Daily buoy-AVHRR comparison with the smoothed anomaly score and drift flag."""
    d = means[["SSTP"]].join(avhrr.rename("avhrr_sst"), how="inner")
    d["avhrr_buoy_diff"] = d["SSTP"] - d["avhrr_sst"]
    base = d[in_period(d.index, AVHRR_CLIMATOLOGY)]
    stats = base.groupby(base.index.month)["avhrr_buoy_diff"].agg(["std", "mean"])
    if station in NO_AVHRR_STATIONS:
        d["score"] = np.nan
    else:
        months = d.index.month
        d["score"] = (d["avhrr_buoy_diff"] - monthly(stats["mean"], months, "AVHRR month")).abs() / monthly(
            stats["std"], months, "AVHRR month"
        )
    d["smoothed"] = nan_savgol(d["score"])
    d["bad"] = d["smoothed"] > 2
    return d


def qc_station(obs, sat_sst, avhrr, station, today, apply_daily_flags=False):
    """Flag every SSTP reading of one station.

    obs:     STN_ID, time, latitude, longitude, SSTP (float32), time-ordered, unique times,
             already cut to the last OISST day
    sat_sst: OISST at the station's pixel, indexed by day
    avhrr:   AVHRR SST at the station, indexed by day (None/empty: no drift check)
    Returns one row per non-null SSTP with SSTP_flags, SSTP_UQL and the diagnostics.
    """
    coastal = station in COASTAL_STATIONS
    df = obs[obs["SSTP"].notna()].copy()
    df.index = pd.DatetimeIndex(df["time"], name="time")
    if df.empty:
        raise QCError(f"no SSTP data for {station}")
    good = df[(df["SSTP"] > 0) & (df["SSTP"] < 30)]
    means = daily_means(good, sat_sst, coastal)
    day = df.index.normalize()

    # 1. satellite comparison; temp chop wins
    chop = ((df["SSTP"] <= 0) | (df["SSTP"] >= 30)).to_numpy()
    flag = np.ones(len(df), dtype="int64")
    flag[~chop] = _lookup(means["qc_flag"], day[~chop], "daily mean")
    df["qc_flag"] = flag
    df["sat_sst"] = _lookup(sat_sst, day, "OISST day")
    df["qc_flag_orig"] = df["qc_flag"]
    df["time_diff"] = df["time"].diff()
    df["temp_diff"] = df["SSTP"].diff().abs()
    one_hour = (df["time_diff"] == ONE_HOUR).to_numpy()
    climatology = in_period(df.index, CLIMATOLOGY)
    month = df.index.month

    # 2. hourly jump statistics per calendar month (good data, climatology, 1-hour gaps)
    base = df[(df["qc_flag"] == 4).to_numpy() & climatology & one_hour]
    jump_stats = base.groupby(base.index.month)["temp_diff"].agg(["std", "mean"])
    max_jump = df.loc[one_hour, "temp_diff"].resample("D").max()
    max_jump_z = (max_jump - monthly(jump_stats["mean"], max_jump.index.month, "jump month")).abs() / monthly(
        jump_stats["std"], max_jump.index.month, "jump month"
    )

    # daily spikiness: day's std vs the month's typical std
    valid = df[df["qc_flag"] > 1]
    spiky = valid["SSTP"].resample("D").agg(["std", "count"])
    calc = df[(df["qc_flag"] == 4).to_numpy() & climatology]["SSTP"].resample("D").agg(["std", "count"])
    calc = calc[(calc["count"] >= 6) & (calc["count"] <= 24)]
    monthly_std = calc.groupby(calc.index.month)["std"].mean()
    diff_to_monthly = (spiky["std"] - monthly(monthly_std, spiky.index.month, "spikiness month")).abs()
    spiky_z = (diff_to_monthly - diff_to_monthly.mean()).abs() / diff_to_monthly.std()

    days = pd.DatetimeIndex(day.unique())
    daily = pd.DataFrame(index=days)
    daily["buoy_sat_diff_zscore"] = means["buoy_sat_diff_zscore"].reindex(days)
    daily["count"] = spiky["count"].reindex(days)
    daily["daily_spikyness_zscore"] = spiky_z.reindex(days)
    daily["max_temp_jump_zscore"] = max_jump_z.reindex(days)
    # TODO(science): default stays False to match the published flags; see README "Buoy QC science decisions"
    if apply_daily_flags:
        c, z, j = daily["count"], daily["daily_spikyness_zscore"], daily["max_temp_jump_zscore"]
        daily["flag"] = np.select([c > 24, z > 6, (z > 4.5) & (j > 3), z > 4.5], [10, 9, 7, 6], 0)
    else:
        daily["flag"] = 0
    per_row = daily.reindex(day)
    dflag = per_row["flag"].to_numpy()
    orig = df["qc_flag_orig"].to_numpy()

    # jump z-score only where the original computed it (flag 3/4, no overriding daily flag)
    rest = (orig > 2) & (dflag != 10) & (dflag != 9)
    temp_diff_zscore = np.full(len(df), -1.0)
    m = month[rest]
    temp_diff_zscore[rest] = np.abs(
        df["temp_diff"].to_numpy(dtype="float64")[rest] - monthly(jump_stats["mean"], m, "jump month")
    ) / monthly(jump_stats["std"], m, "jump month")
    big_jump = rest & one_hour & (temp_diff_zscore >= 4.5)
    medium_spiky = rest & ~big_jump & (dflag > 0)
    df["qc_flag"] = np.select(
        [orig == 1, orig == 2, dflag == 10, dflag == 9, big_jump, medium_spiky & (orig == 3), medium_spiky],
        [1, 2, 20, 19, 9, orig + dflag + 3, orig + dflag + 4],
        orig,
    )
    df["readings_per_day"] = per_row["count"].to_numpy()
    df["daily_spikyness_zscore"] = per_row["daily_spikyness_zscore"].to_numpy()
    df["buoy_sat_diff_zscore"] = per_row["buoy_sat_diff_zscore"].to_numpy()
    df["temp_diff_zscore"] = temp_diff_zscore

    # 3. unprecise days: > 20% of the day's readings are jumps
    jumps = df[df["qc_flag"] == 9]
    by_day = jumps.groupby(jumps.index.normalize()).agg(
        count_9=("qc_flag", "count"), readings=("readings_per_day", "median")
    )
    unprecise = by_day.index[(by_day["count_9"] / by_day["readings"]) > 0.2]
    df["is_unprecise_day"] = day.isin(unprecise)

    # 4. which points get the window re-check (decided before the unprecise flag is applied)
    qc, rpd = df["qc_flag"], df["readings_per_day"]
    df["run_windowing_function"] = ((qc == 9) & ~df["is_unprecise_day"] & (rpd <= 24)) | (
        qc.isin([3, 4]) & (df["time_diff"] < pd.Timedelta("1D")) & (df["temp_diff_zscore"] > 6) & (rpd <= 24)
    )
    df.loc[(df["qc_flag"] > 2) & df["is_unprecise_day"], "qc_flag"] = 41
    df["qc_flag"] = window_recheck(df, df["run_windowing_function"].to_numpy())

    # 5. AVHRR drift
    if avhrr is not None and len(avhrr):
        drift = avhrr_drift(means, avhrr, station)
        for start, end in drift_ranges(drift.index, drift["bad"].to_numpy(), day.max()):
            in_range = (df.index >= start) & (df.index < end + pd.Timedelta("1D"))
            df.loc[in_range & (df["qc_flag"] > 1).to_numpy(), "qc_flag"] = 35

    # 6. isolated spikes among good points
    q4 = df.loc[df["qc_flag"] == 4, "SSTP"]
    outlier = (q4.diff().abs() > 1.75) & (q4.diff(-1).abs() > 1.75)
    df.loc[outlier[outlier].index, "qc_flag"] = 88

    unknown = set(df["qc_flag"].unique()) - set(FLAG_REMAPPING)
    if unknown:
        raise QCError(f"unmapped internal flags {sorted(unknown)}")
    df["SSTP_flags"] = df["qc_flag"].map(FLAG_REMAPPING)
    df["SSTP_UQL"] = df["SSTP_flags"].map(FLAG_QARTOD)
    two_weeks_prior = pd.Timestamp(today - dt.timedelta(weeks=2))
    df.loc[(df["time"] > LAST_AVHRR_DATE) | (df["time"] > two_weeks_prior), "SSTP_UQL"] = 2
    return df.reset_index(drop=True)


def load_avhrr(path):
    """AVHRR daily SST per station (static, ends 2020-12-31): {station: Series by day}."""
    a = pd.read_csv(path, parse_dates=["date"])
    a = a[a["date"] >= AVHRR_START].sort_values(["station", "date"])
    return {s: g.set_index("date")["sea_surface_temperature"] for s, g in a.groupby("station")}


def prepare(obs, latest_oisst_day):
    """Cut to the last OISST day, time-order and de-duplicate, as the QC expects."""
    obs = obs[obs["time"] <= latest_oisst_day]
    obs = obs.sort_values("time", kind="stable")
    return obs.drop_duplicates(subset=["STN_ID", "time"], keep="first").reset_index(drop=True)


FLAG_COLUMNS = ["STN_ID", "time", "SSTP_flags", "SSTP_UQL"]
