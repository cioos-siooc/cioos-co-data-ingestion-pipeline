#!/usr/bin/env python3
"""
Publish pipeline outputs to a configurable destination via fsspec.

The destination is a URL: ``file://`` (default), ``s3://``, ``sftp://``, or any
other fsspec-supported backend. Credentials follow the backend's own
conventions — AWS_* environment variables (and AWS_ENDPOINT_URL for
MinIO-style endpoints) for s3, ``sftp://user:pass@host/path`` or standard SSH
keys for sftp — so there is no custom credential handling here.

Resolution order for the destination:
  1. <PIPELINE>_PUBLISH_URL   (e.g. MEDS_PUBLISH_URL)
  2. PUBLISH_URL
  3. file://<cwd>/datasets    (the docker-compose bind mount)
"""

import os
import shutil
from pathlib import Path

import fsspec
from fsspec.implementations.local import LocalFileSystem


def publish_url(pipeline=None):
    """Resolve the destination URL for a pipeline (see module docstring)."""
    if pipeline:
        url = os.environ.get(f"{pipeline.upper()}_PUBLISH_URL")
        if url:
            return url
    return os.environ.get("PUBLISH_URL", "file://" + str(Path("datasets").absolute()))


def publish_files(srcs, dataset, *, pipeline=None, skip_unchanged=False, logger=None):
    """
    Copy local files into ``<destination>/<dataset>/`` and return how many
    source files were handled (copied or already up to date).

    On the (default) local destination, copies preserve mtimes
    (``shutil.copy2``) — ERDDAP's updateEveryNMillis change detection and the
    pipelines' conditional-GET mirroring both rely on that.

    ``skip_unchanged``: skip files whose destination size and mtime already
    match the source. Backends that don't report an mtime (e.g. s3 reports
    LastModified of the upload, not the source mtime) fall back to a size-only
    comparison — a documented approximation.
    """
    url = publish_url(pipeline)
    fs, root = fsspec.core.url_to_fs(url)
    dest_dir = f"{root.rstrip('/')}/{dataset}"
    fs.makedirs(dest_dir, exist_ok=True)
    local = isinstance(fs, LocalFileSystem)

    total = copied = 0
    for src in srcs:
        src = Path(src)
        dest = f"{dest_dir}/{src.name}"
        total += 1
        if skip_unchanged and _unchanged(fs, src, dest):
            continue
        if local:
            shutil.copy2(src, dest)
        else:
            fs.put_file(str(src), dest)
        copied += 1

    if logger:
        logger.info(f"📤 published {copied}/{total} file(s) to {url.rstrip('/')}/{dataset}")
    return total


def _unchanged(fs, src, dest):
    """True if dest exists and matches src by size (and mtime, if reported)."""
    try:
        info = fs.info(dest)
    except FileNotFoundError:
        return False
    if info.get("size") != src.stat().st_size:
        return False
    mtime = _mtime_of(info)
    if mtime is None:
        return True  # size matches and the backend has no usable mtime
    return mtime == src.stat().st_mtime


def _mtime_of(info):
    value = info.get("mtime", info.get("LastModified"))
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return value.timestamp()  # datetime (e.g. s3 LastModified)
    except AttributeError:
        return None
