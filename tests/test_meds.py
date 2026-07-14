#!/usr/bin/env python3
"""
Offline tests — exercise header normalisation + the data fix against the
committed sample CSVs without hitting the network.

Fake deployment metadata stands in for the MEDS inventory JSON (b_pw_inv.json).
In production every station has a deployment record; MEDS210 in particular
carries multiple in-file coordinates and so *requires* a fixed deployment coord.
"""

import shutil
from pathlib import Path

import pandas as pd

from cioos_ingest.meds import download as meds_download
from cioos_ingest.meds import fix as meds_fix

SAMPLES = Path(__file__).parent / "fixtures" / "meds" / "sample_csv"


def _fake_metadata():
    df = pd.DataFrame(
        [
            {"station": "C44131", "lat": 45.9, "lon": -51.0},
            {"station": "MEDS210", "lat": 44.38, "lon": -58.03},
            {"station": "WEL233", "lat": 43.85, "lon": -60.64},
        ],
    ).set_index("station", drop=False)
    return df


def test_normalize_and_fix(tmp_path):
    csv_dir = tmp_path / "csv"
    out_dir = tmp_path / "csv-fixed"
    shutil.copytree(SAMPLES, csv_dir)

    meds_download.normalize_headers(csv_dir)
    meds_fix.fix_all(csv_dir, out_dir, df_metadata=_fake_metadata())

    c = pd.read_csv(out_dir / "C44131.csv")
    assert list(c.columns) == meds_fix.COLUMN_ORDER, c.columns.tolist()
    assert (c["LONGITUDE"] < 0).all(), "C longitudes should be negative (degrees east)"
    assert c["SSTP"].notna().any(), "C buoy should carry SST"
    assert c["DATE"].iloc[0].endswith("Z"), c["DATE"].iloc[0]

    m = pd.read_csv(out_dir / "MEDS210.csv")
    assert list(m.columns) == meds_fix.COLUMN_ORDER
    assert m["SSTP"].isna().all(), "historic buoy should have empty SST"
    assert m["WDIR"].isna().all(), "historic buoy should have empty wind"
    assert (m["LONGITUDE"] < 0).all()

    w = pd.read_csv(out_dir / "WEL233.csv")
    assert list(w.columns) == meds_fix.COLUMN_ORDER
