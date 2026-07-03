#!/usr/bin/env python3
"""
Offline driver (not a pytest suite) — exercises header normalisation + the data
fix against the committed sample CSVs without hitting the network.

    cd MEDS_Buoy_Parser/app
    uv run python tests.py

It copies test/sample_csv into a temp dir, runs meds_download.normalize_headers
and meds_fix.fix_all (with fake deployment metadata), and asserts the output has
the unified column set, negative longitudes, ISO dates, and empty met columns for
the historic buoys.
"""

import shutil
import tempfile
from pathlib import Path

import pandas as pd

import meds_download
import meds_fix

HERE = Path(__file__).parent
SAMPLES = HERE / "test" / "sample_csv"


def _fake_metadata():
    # Stand in for the MEDS inventory JSON (b_pw_inv.json). In production every
    # station has a deployment record; MEDS210 in particular carries multiple
    # in-file coordinates and so *requires* a fixed deployment coord.
    df = pd.DataFrame(
        [
            {"station": "C44131", "lat": 45.9, "lon": -51.0},
            {"station": "MEDS210", "lat": 44.38, "lon": -58.03},
            {"station": "WEL233", "lat": 43.85, "lon": -60.64},
        ],
    ).set_index("station", drop=False)
    return df


def main():
    tmp = Path(tempfile.mkdtemp(prefix="meds-test-"))
    csv_dir = tmp / "csv"
    out_dir = tmp / "csv-fixed"
    shutil.copytree(SAMPLES, csv_dir)

    meds_download.normalize_headers(csv_dir)
    meds_fix.fix_all(csv_dir, out_dir, df_metadata=_fake_metadata())

    # --- assertions ---
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

    print("outputs:", sorted(p.name for p in out_dir.glob("*.csv")))
    print(f"C44131 rows={len(c)}  MEDS210 rows={len(m)}  WEL233 rows={len(w)}")
    print("✅ all assertions passed")
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
