#!/usr/bin/env python3
"""Tests for the fsspec publish helper (file://, memory:// and s3:// backends)."""

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
    # copy2 semantics: the pipelines' conditional-GET mirroring relies on
    # preserved mtimes
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


# --- s3 credential preflight -------------------------------------------------

AWS_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


@pytest.mark.parametrize("present", [(), ("AWS_ACCESS_KEY_ID",),
                                     ("AWS_SECRET_ACCESS_KEY",)])
def test_check_credentials_rejects_s3_without_keys(monkeypatch, present):
    for var in AWS_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in present:
        monkeypatch.setenv(var, "x")

    with pytest.raises(RuntimeError, match="needs S3 credentials"):
        publish.check_credentials("s3://cioos-juno-buoy-data/datasets")


def test_check_credentials_accepts_s3_with_keys(monkeypatch):
    for var in AWS_VARS:
        monkeypatch.setenv(var, "x")
    publish.check_credentials("s3://cioos-juno-buoy-data/datasets")  # no raise


@pytest.mark.parametrize("url", ["file:///tmp/out", "memory://pub",
                                 "sftp://host/path"])
def test_check_credentials_ignores_non_s3(monkeypatch, url):
    for var in AWS_VARS:
        monkeypatch.delenv(var, raising=False)
    publish.check_credentials(url)  # no raise


def test_publish_files_preflights_s3(tmp_path, monkeypatch):
    """The guard must fire from publish_files, before any network call."""
    (src,) = _make_files(tmp_path, ["a.csv"])
    monkeypatch.setenv("PUBLISH_URL", "s3://cioos-juno-buoy-data/datasets")
    for var in AWS_VARS:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(RuntimeError, match="needs S3 credentials"):
        publish.publish_files([src], "DS")


# --- opt-in live round trip against the real bucket --------------------------

@pytest.mark.skipif(not os.environ.get("JUNO_TEST_BUCKET"),
                    reason="set JUNO_TEST_BUCKET=1 (needs real credentials)")
def test_juno_bucket_round_trip(tmp_path, monkeypatch):
    """
    Publish -> list -> delete against s3://cioos-juno-buoy-data/_smoketest/.

    This is what proves the Ceph gateway works end to end: path-style
    addressing (a virtual-host client fails with a DNS/TLS error naming
    cioos-juno-buoy-data.objets...), signature v4, and the credentials.

    The bucket is versioned, so the cleanup delete writes a delete marker and
    leaves the object as a noncurrent version rather than reclaiming space.
    """
    monkeypatch.setenv("AWS_ENDPOINT_URL", os.environ.get(
        "AWS_ENDPOINT_URL", "https://objets.juno.calculquebec.ca"))
    monkeypatch.setenv("AWS_DEFAULT_REGION",
                       os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    monkeypatch.setenv("PUBLISH_URL", "s3://cioos-juno-buoy-data/_smoketest")

    name = f"round-trip-{os.getpid()}-{int(time.time())}.txt"
    (src,) = _make_files(tmp_path, [name])
    dataset = "publish-test"

    assert publish.publish_files([src], dataset) == 1

    fs, root = fsspec.core.url_to_fs(publish.publish_url())
    key = f"{root.rstrip('/')}/{dataset}/{name}"
    try:
        assert fs.cat(key) == f"content of {name}".encode()
    finally:
        fs.rm(key)


# --- s3 must never attempt to create the bucket ------------------------------

class _RecordingFS:
    """Minimal fake filesystem recording the calls publish_files makes."""

    def __init__(self, protocol):
        self.protocol = protocol
        self.calls = []

    def makedirs(self, path, exist_ok=False):
        self.calls.append(("makedirs", path))

    def put_file(self, src, dest):
        self.calls.append(("put_file", dest))

    def info(self, path):
        raise FileNotFoundError(path)


@pytest.mark.parametrize("protocol,expected", [
    (("s3", "s3a"), True), ("s3", True), ("s3a", True),
    (("file", "local"), False), ("memory", False), ("sftp", False),
])
def test_is_s3(protocol, expected):
    assert publish._is_s3(_RecordingFS(protocol)) is expected


def test_s3_publish_does_not_makedirs(tmp_path, monkeypatch):
    """
    s3fs's makedirs creates the *bucket* when it can't see one, and a revoked
    key or misspelled bucket both look like that (NoSuchBucket/404). put_file
    creates the key on its own, so no makedirs must be issued on s3.
    """
    (src,) = _make_files(tmp_path, ["a.csv"])
    fake = _RecordingFS(("s3", "s3a"))
    monkeypatch.setattr(fsspec.core, "url_to_fs",
                        lambda url: (fake, "cioos-juno-buoy-data/datasets"))
    monkeypatch.setenv("PUBLISH_URL", "s3://cioos-juno-buoy-data/datasets")
    for var in AWS_VARS:
        monkeypatch.setenv(var, "x")

    assert publish.publish_files([src], "DS") == 1
    assert fake.calls == [
        ("put_file", "cioos-juno-buoy-data/datasets/DS/a.csv"),
    ]


def test_non_s3_publish_still_makedirs(tmp_path, monkeypatch):
    """sftp and friends do have real directories and still need them created."""
    (src,) = _make_files(tmp_path, ["a.csv"])
    fake = _RecordingFS("sftp")
    monkeypatch.setattr(fsspec.core, "url_to_fs",
                        lambda url: (fake, "/srv/data"))
    monkeypatch.setenv("PUBLISH_URL", "sftp://host/srv/data")

    assert publish.publish_files([src], "DS") == 1
    assert fake.calls == [
        ("makedirs", "/srv/data/DS"),
        ("put_file", "/srv/data/DS/a.csv"),
    ]
