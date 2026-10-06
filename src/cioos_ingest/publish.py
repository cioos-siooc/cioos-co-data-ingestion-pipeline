#!/usr/bin/env python3
"""
Publish pipeline outputs to a configurable destination via fsspec.

The destination is a URL: ``s3://`` (what the compose stack sets — the CIOOS
Juno buoy bucket), ``file://`` (the code default), ``sftp://``, or any other
fsspec-supported backend. Credentials follow the backend's own conventions —
AWS_* environment variables (with AWS_ENDPOINT_URL for the non-AWS gateway)
for s3, ``sftp://user:pass@host/path`` or standard SSH keys for sftp — so
there is no custom credential handling here, only the s3 preflight below.

Resolution order for the destination:
  1. <PIPELINE>_PUBLISH_URL   (e.g. MEDS_PUBLISH_URL)
  2. PUBLISH_URL
  3. file://<cwd>/datasets    (local fallback)
"""

import os
import shutil
import urllib.parse
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


def check_credentials(url):
    """
    Fail early and legibly on an s3 destination with no credentials.

    The Juno gateway answers an unauthenticated request with ``NoSuchBucket``
    (404), not ``AccessDenied`` — it won't confirm that a bucket it won't serve
    exists. Without this check a missing key looks exactly like a deleted
    bucket, which is the most misleading failure mode in this pipeline.
    """
    if urllib.parse.urlsplit(url).scheme not in ("s3", "s3a"):
        return
    missing = [v for v in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
               if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"{url} needs S3 credentials but {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} unset. Set them in .env "
            "(local) or the Coolify environment (deployed). Without them the "
            "gateway returns a 404 that looks like a missing bucket.")


def _is_s3(fs):
    """True for an s3 filesystem (``protocol`` may be a str or a tuple)."""
    protocol = fs.protocol
    if isinstance(protocol, str):
        protocol = (protocol,)
    return bool({"s3", "s3a"}.intersection(protocol))


def publish_files(srcs, dataset, *, pipeline=None, skip_unchanged=False, logger=None):
    """
    Copy local files into ``<destination>/<dataset>/`` and return how many
    source files were handled (copied or already up to date).

    On a local destination, copies preserve mtimes (``shutil.copy2``), which
    the pipelines' conditional-GET mirroring relies on. Object storage cannot
    carry a source mtime, so see ``skip_unchanged`` below.

    ``skip_unchanged``: skip files whose destination size and mtime already
    match the source. Backends that don't report an mtime (e.g. s3 reports
    LastModified of the upload, not the source mtime) fall back to a size-only
    comparison — a documented approximation.
    """
    url = publish_url(pipeline)
    check_credentials(url)
    fs, root = fsspec.core.url_to_fs(url)
    dest_dir = f"{root.rstrip('/')}/{dataset}"
    local = isinstance(fs, LocalFileSystem)

    # Object stores have no real directories, and s3fs's makedirs *creates the
    # bucket* when it can't see one — which is exactly what a revoked key or a
    # misspelled bucket looks like on this gateway (NoSuchBucket/404). Since
    # put_file creates the key by itself, skip the makedirs on s3 rather than
    # risk writing a stray bucket into the CIOOS project.
    if not _is_s3(fs):
        fs.makedirs(dest_dir, exist_ok=True)

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
