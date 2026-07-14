#!/usr/bin/env python3
"""Tests for the fsspec publish helper (file:// and memory:// backends)."""

import os
import time

import fsspec
import pytest

from cioos_ingest import publish


def _make_files(tmp_path, names):
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    paths = []
    for name in names:
        p = src_dir / name
        p.write_text(f"content of {name}")
        paths.append(p)
    return paths


def test_publish_url_default(monkeypatch):
    monkeypatch.delenv("PUBLISH_URL", raising=False)
    monkeypatch.delenv("MEDS_PUBLISH_URL", raising=False)
    url = publish.publish_url("meds")
    assert url.startswith("file://")
    assert url.endswith("/datasets")


def test_publish_url_precedence(monkeypatch):
    monkeypatch.setenv("PUBLISH_URL", "s3://bucket/base")
    monkeypatch.setenv("MEDS_PUBLISH_URL", "sftp://host/meds")
    assert publish.publish_url("argo") == "s3://bucket/base"
    assert publish.publish_url("meds") == "sftp://host/meds"
    assert publish.publish_url() == "s3://bucket/base"


def test_publish_local_preserves_mtime(tmp_path, monkeypatch):
    srcs = _make_files(tmp_path, ["a.csv", "b.csv"])
    old = time.time() - 1000
    os.utime(srcs[0], (old, old))
    monkeypatch.setenv("PUBLISH_URL", f"file://{tmp_path}/out")

    assert publish.publish_files(srcs, "DS") == 2
    dest = tmp_path / "out" / "DS" / "a.csv"
    assert dest.read_text() == "content of a.csv"
    # copy2 semantics: ERDDAP change detection depends on preserved mtimes
    assert dest.stat().st_mtime == pytest.approx(old)


def test_skip_unchanged_local(tmp_path, monkeypatch):
    (src,) = _make_files(tmp_path, ["c.nc"])
    monkeypatch.setenv("PUBLISH_URL", f"file://{tmp_path}/out")
    publish.publish_files([src], "DS", skip_unchanged=True)

    fs, root = fsspec.core.url_to_fs(publish.publish_url())
    dest = f"{root.rstrip('/')}/DS/{src.name}"
    assert publish._unchanged(fs, src, dest) is True

    # touching the source mtime must defeat the skip
    os.utime(src, (time.time() + 60, time.time() + 60))
    assert publish._unchanged(fs, src, dest) is False
    # and so must a size change
    src.write_text("different length content !!")
    assert publish._unchanged(fs, src, dest) is False


def test_publish_memory_backend(tmp_path, monkeypatch):
    (src,) = _make_files(tmp_path, ["x.nc"])
    monkeypatch.setenv("PUBLISH_URL", "memory://pub")

    assert publish.publish_files([src], "DS", skip_unchanged=True) == 1
    fs = fsspec.filesystem("memory")
    assert fs.cat("/pub/DS/x.nc") == b"content of x.nc"
    # re-publish: size matches and memory:// has no source mtime -> still counted
    assert publish.publish_files([src], "DS", skip_unchanged=True) == 1
