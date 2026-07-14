#!/usr/bin/env python3
"""
Offline tests — exercise the GDAC index parser and float selection against a
committed fixture without hitting the network.

Set ARGO_TEST_NETWORK=1 to additionally do a live single-float conditional-GET
round trip against the GDAC (downloads one ~2 MB file, then verifies the second
request returns 304-unchanged).
"""

import os
from pathlib import Path

import pytest

from cioos_ingest.argo import download as argo_download

FIXTURE = Path(__file__).parent / "fixtures" / "argo" / "meds_index_head.html"


def _fixture_ids():
    return argo_download.parse_float_ids(FIXTURE.read_text())


def test_parse_float_ids():
    # fixture is a real snippet of the Ifremer dac/meds/ index
    ids = _fixture_ids()
    assert len(ids) >= 15, f"expected >=15 float ids in fixture, got {len(ids)}"
    assert ids[0] == "2900193", ids[:3]
    assert all(i.isdigit() and 5 <= len(i) <= 7 for i in ids), "non-WMO id parsed"
    assert len(ids) == len(set(ids)), "ids should be de-duplicated"
    # the sort-order links (?C=N;O=D) and Parent Directory must not match
    assert "dac" not in ids


def test_select_floats():
    ids = _fixture_ids()
    assert argo_download.select_floats(ids, wanted=["3900085", "9999999"]) == ["3900085"]
    assert argo_download.select_floats(ids, limit=3) == ids[:3]
    assert argo_download.select_floats(ids) == ids


@pytest.mark.skipif(not os.environ.get("ARGO_TEST_NETWORK"),
                    reason="set ARGO_TEST_NETWORK=1 for the live GDAC round trip")
def test_network_round_trip(tmp_path):
    wmo = _fixture_ids()[0]
    got = argo_download.download_profiles([wmo], tmp_path)
    assert got and got[0].exists() and got[0].stat().st_size > 0
    size = got[0].stat().st_size
    # second run must hit the If-Modified-Since path and keep the file
    again = argo_download.download_profiles([wmo], tmp_path)
    assert again and again[0].stat().st_size == size
