#!/usr/bin/env python3
"""Tests for the ECCC SWOB parser's NCCSV header handling (issue #5)."""

import csv
import json
import shutil
from pathlib import Path

import pytest

from cioos_ingest.eccc.swob_parser import Marine_buoy_parser, validate_nccsv_header

FIXTURE_XML = Path(__file__).parent / "fixtures" / "eccc" / "swob_marine_44999.xml"
TYPES_FILE = Path(__file__).parents[1] / "config" / "eccc" / "ECCCbuoys_types.json"


@pytest.fixture
def parser(tmp_path, monkeypatch):
    """A parser pointed at tmp dirs, with the real curated types file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(TYPES_FILE, config_dir / "ECCCbuoys_types.json")
    monkeypatch.setenv("ECCC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECCC_CONFIG_DIR", str(config_dir))
    return Marine_buoy_parser()


def _read_header(csv_path):
    with open(csv_path, newline='') as fh:
        rows = list(csv.reader(fh))
    columns = rows[rows.index(['*END_METADATA*']) + 1]
    return rows, columns


def test_tocsv_writes_valid_header(parser):
    assert parser.parse_marine_xml(FIXTURE_XML) is not None
    csv_path = parser.toCSV()

    ok, reason = validate_nccsv_header(csv_path)
    assert ok, reason

    rows, columns = _read_header(csv_path)
    # the curated renames must be applied
    assert 'time' in columns
    assert 'latitude' in columns
    assert 'longitude' in columns
    assert 'date_tm' not in columns
    # every column carries units and *DATA_TYPE* rows
    with_dtype = {r[0] for r in rows if len(r) >= 3 and r[1] == '*DATA_TYPE*'}
    assert set(columns) <= with_dtype
    # one data row appended after the column-name row, MSNG mapped to ''
    data_row = rows[rows.index(columns) + 1]
    assert data_row[columns.index('stn_nam')] == 'Test Buoy'
    assert data_row[columns.index('avg_air_temp_pst10mts')] == '12.3'
    assert data_row[columns.index('avg_sea_sfc_temp_pst10mts')] == ''


def test_missing_types_file_is_fatal(parser):
    Path(parser.typesFile).unlink()
    assert parser.parse_marine_xml(FIXTURE_XML) is not None
    with pytest.raises(RuntimeError, match="types file"):
        parser.toCSV()
    assert not Path(parser.csvFolder, "44999.csv").exists()


def test_unknown_field_is_fatal_and_leaves_no_file(parser):
    with open(parser.typesFile) as f:
        types = json.load(f)
    del types["avg_air_temp_pst10mts"]
    with open(parser.typesFile, 'w') as f:
        json.dump(types, f)

    assert parser.parse_marine_xml(FIXTURE_XML) is not None
    with pytest.raises(RuntimeError, match="avg_air_temp_pst10mts"):
        parser.toCSV()
    assert not Path(parser.csvFolder, "44999.csv").exists()


def test_validator_rejects_incident_style_header(tmp_path):
    # Reproduces the 2026-07-15 incident: stn_nam column with no metadata rows.
    bad = tmp_path / "44137.csv"
    bad.write_text(
        '*GLOBAL*,Conventions,"COARDS, CF-1.6, ACDD-1.3, NCCSV-1.2"\r\n'
        'time,units,yyyy-MM-dd\'T\'HH:mm:ss.SSS\'Z\'\r\n'
        'time,*DATA_TYPE*,string\r\n'
        '*END_METADATA*\r\n'
        'time,stn_nam\r\n'
        '2026-07-15T12:00:00.000Z,Test Buoy\r\n'
    )
    ok, reason = validate_nccsv_header(bad)
    assert not ok
    assert 'stn_nam' in reason


def test_validator_rejects_missing_time_rename(tmp_path):
    bad = tmp_path / "44138.csv"
    bad.write_text(
        'date_tm,units,unitless\r\n'
        'date_tm,*DATA_TYPE*,string\r\n'
        '*END_METADATA*\r\n'
        'date_tm\r\n'
    )
    ok, reason = validate_nccsv_header(bad)
    assert not ok
    assert 'time' in reason


def test_validator_rejects_truncated_header(tmp_path):
    bad = tmp_path / "44139.csv"
    bad.write_text('*GLOBAL*,title,whatever\r\n')
    ok, reason = validate_nccsv_header(bad)
    assert not ok
    assert '*END_METADATA*' in reason
