#!/bin/bash
# Install the federated Ifremer ArgoFloats fragment (demo version 1) into the
# cioos-national-erddap submodule's datasets.d/ directory.
#
# EDDTableFromErddap needs no generation step — it is a redirect mirror of the
# remote dataset and allows no addAttributes/dataVariable tuning. See the
# comments in the fragment for why this federates the ENTIRE global Argo
# dataset (a fixed data_center="ME" subset is not possible with ERDDAP
# federation; the Canadian-only dataset is ARGO_MEDS, demo version 2).
#
# Installed as a manual datasets.d/ fragment on purpose: adding Ifremer to
# erddap_servers.yaml instead would harvest their whole catalogue.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$ROOT/cioos-national-erddap/datasets.d"
CURATED="$ROOT/Argo_MEDS_Parser/erddap_config/ArgoFloats_Ifremer.xml"

mkdir -p "$OUT_DIR"
cp "$CURATED" "$OUT_DIR/ArgoFloats_Ifremer.xml"
echo "Installed fragment to $OUT_DIR/ArgoFloats_Ifremer.xml"
