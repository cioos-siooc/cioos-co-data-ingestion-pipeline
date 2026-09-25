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


def _variant(tmp_path, name, **replace):
    """Write a copy of the fixture with literal text substitutions."""
    text = FIXTURE_XML.read_text()
    for old, new in replace.items():
        assert old in text, old
        text = text.replace(old, new)
    path = tmp_path / name
    path.write_text(text)
    return path


def _dtypes(rows):
    return {r[0]: r[2] for r in rows if len(r) >= 3 and r[1] == '*DATA_TYPE*'}


def test_coordinates_are_double(parser, tmp_path):
    # float (7 significant digits) rounded -132.443889 to -132.4439 in ERDDAP
    xml = _variant(tmp_path, "precise.xml",
                   **{'value="-63.41"': 'value="-132.443889"'})
    parser.parse_marine_xml(xml)
    rows, columns = _read_header(parser.toCSV())
    assert _dtypes(rows)['latitude'] == 'double'
    assert _dtypes(rows)['longitude'] == 'double'
    assert rows[-1][columns.index('longitude')] == '-132.443889'


def test_field_first_seen_later_is_added_and_old_rows_padded(parser, tmp_path):
    parser.parse_marine_xml(FIXTURE_XML)
    csv_path = parser.toCSV()

    # a later message from the same station carries a new sensor
    xml = _variant(
        tmp_path, "later.xml",
        **{'2026-07-15T12:00:00.000Z': '2026-07-15T13:00:00.000Z',
           '<po:element name="avg_air_temp_pst10mts"':
               '<po:element name="avg_wave_hgt_pst20mts" uom="m" value="0.9"/>\n'
               '          <po:element name="avg_air_temp_pst10mts"'})
    parser.parse_marine_xml(xml)
    parser.toCSV()

    ok, reason = validate_nccsv_header(csv_path)
    assert ok, reason
    rows, columns = _read_header(csv_path)
    data = rows[rows.index(columns) + 1:]
    assert [len(r) for r in data] == [len(columns)] * 2
    i = columns.index('avg_wave_hgt_pst20mts')
    assert [r[i] for r in data] == ['', '0.9']
    assert data[0][columns.index('avg_air_temp_pst10mts')] == '12.3'


def test_stale_header_is_regenerated_without_losing_rows(parser):
    parser.parse_marine_xml(FIXTURE_XML)
    csv_path = Path(parser.toCSV())
    # simulate a file written before the types file said double
    csv_path.write_text(csv_path.read_text().replace(
        'latitude,*DATA_TYPE*,double', 'latitude,*DATA_TYPE*,float'))

    rows_before, columns = _read_header(csv_path)
    parser.toCSV()  # same observation again: header fixed, row not duplicated
    rows, _ = _read_header(csv_path)
    assert _dtypes(rows)['latitude'] == 'double'
    assert rows[rows.index(columns) + 1:] == rows_before[rows_before.index(columns) + 1:]


def test_redelivered_observation_is_not_appended_twice(parser):
    parser.parse_marine_xml(FIXTURE_XML)
    parser.toCSV()
    parser.parse_marine_xml(FIXTURE_XML)
    rows, columns = _read_header(parser.toCSV())
    assert len(rows) - rows.index(columns) - 1 == 1


def test_logger_type_kept_and_missing_metadata_blank(parser, tmp_path):
    xml = _variant(
        tmp_path, "logr.xml",
        **{'<po:element name="stn_typ" uom="unitless" value="0"/>':
               '<po:element name="stn_typ" uom="unitless" value="MSNG"/>\n'
               '            <po:element name="logr_typ" uom="unitless" value="WM500"/>'})
    parser.parse_marine_xml(xml)
    rows, columns = _read_header(parser.toCSV())
    assert rows[-1][columns.index('logr_typ')] == 'WM500'
    assert rows[-1][columns.index('stn_typ')] == ''
