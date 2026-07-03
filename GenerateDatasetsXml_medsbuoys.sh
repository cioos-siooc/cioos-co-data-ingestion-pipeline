#!/bin/bash
# Install the MEDS buoy ERDDAP dataset fragment into the cioos-national-erddap
# submodule's datasets.d/ directory, where the national sync
# (sync-erddap-datasets.py) merges it into datasets.xml.
#
# The curated fragment (MEDS_Buoy_Parser/erddap_config/MEDS.xml) is the source of
# truth: its <sourceName>s are already reconciled with the columns meds_fix.py
# writes and it carries the Q_FLAG comment block. By default this script simply
# installs that curated fragment.
#
# Pass --generate to instead run ERDDAP's GenerateDatasetsXml tool against the
# published CSVs and drop a fresh *draft* alongside (logs/MEDS.draft.xml) for
# comparison — useful when the column set changes and the curated fragment needs
# updating. The draft is NOT installed automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUBMODULE="$ROOT/cioos-national-erddap"
DATASETS_DIR="$SUBMODULE/datasets"
OUT_DIR="$SUBMODULE/datasets.d"
LOGS_DIR="$ROOT/logs"
CURATED="$ROOT/MEDS_Buoy_Parser/erddap_config/MEDS.xml"

mkdir -p "$OUT_DIR"

if [[ "${1:-}" == "--generate" ]]; then
  mkdir -p "$LOGS_DIR"
  # ERDDAP's EDStatic startup aborts unless flagKeyKey is non-default; any local
  # value works for generating a fragment (override via the environment).
  ERDDAP_flagKeyKey="${ERDDAP_flagKeyKey:-generate-datasets-xml-local}"
  docker run --rm \
    -e ERDDAP_flagKeyKey="$ERDDAP_flagKeyKey" \
    -v "$DATASETS_DIR:/datasets" \
    -v "$LOGS_DIR:/erddapData/logs" \
    -v "$SUBMODULE/erddap/content:/usr/local/tomcat/content/erddap" \
    axiom/docker-erddap:2.23-jdk17-openjdk \
    bash -c "cd webapps/erddap/WEB-INF/ && bash GenerateDatasetsXml.sh EDDTableFromAsciiFiles /datasets/MEDS_CSV .*\\.csv 1 2 , nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing"

  awk '/<dataset /{f=1} f{print} /<\/dataset>/{f=0}' \
    "$LOGS_DIR/GenerateDatasetsXml.out" > "$LOGS_DIR/MEDS.draft.xml"
  echo "Wrote draft (for review) to $LOGS_DIR/MEDS.draft.xml"
  echo "Curated fragment remains the source of truth; edit $CURATED if needed."
fi

# Install the curated fragment into the submodule's datasets.d/.
cp "$CURATED" "$OUT_DIR/MEDS.xml"
echo "Installed fragment to $OUT_DIR/MEDS.xml"
