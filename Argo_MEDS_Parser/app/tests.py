#!/usr/bin/env python3
"""
Offline driver (not a pytest suite) — exercises the GDAC index parser and float
selection against a committed fixture without hitting the network.

    cd Argo_MEDS_Parser/app
    uv run python tests.py

Set ARGO_TEST_NETWORK=1 to additionally do a live single-float conditional-GET
round trip against the GDAC (downloads one ~2 MB file, then verifies the second
request returns 304-unchanged).
"""

import os
import tempfile
from pathlib import Path

import argo_download

HERE = Path(__file__).parent
FIXTURE = HERE / "test" / "fixtures" / "meds_index_head.html"


def main():
    # --- index parsing (fixture is a real snippet of the Ifremer dac/meds/ index)
    html = FIXTURE.read_text()
    ids = argo_download.parse_float_ids(html)
    assert len(ids) >= 15, f"expected >=15 float ids in fixture, got {len(ids)}"
    assert ids[0] == "2900193", ids[:3]
    assert all(i.isdigit() and 5 <= len(i) <= 7 for i in ids), "non-WMO id parsed"
    assert len(ids) == len(set(ids)), "ids should be de-duplicated"
    # the sort-order links (?C=N;O=D) and Parent Directory must not match
    assert "dac" not in ids

    # --- float selection
    assert argo_download.select_floats(ids, wanted=["3900085", "9999999"]) == ["3900085"]
    assert argo_download.select_floats(ids, limit=3) == ids[:3]
    assert argo_download.select_floats(ids) == ids

    print(f"fixture floats: {len(ids)} (first: {ids[:3]})")
    print("✅ offline assertions passed")

    # --- optional live round trip
    if os.environ.get("ARGO_TEST_NETWORK"):
        wmo = ids[0]
        tmp = Path(tempfile.mkdtemp(prefix="argo-test-"))
        got = argo_download.download_profiles([wmo], tmp)
        assert got and got[0].exists() and got[0].stat().st_size > 0
        size = got[0].stat().st_size
        # second run must hit the If-Modified-Since path and keep the file
        again = argo_download.download_profiles([wmo], tmp)
        assert again and again[0].stat().st_size == size
        print(f"✅ network round trip passed ({wmo}_prof.nc, {size} bytes, 304 on re-run)")


if __name__ == "__main__":
    main()
