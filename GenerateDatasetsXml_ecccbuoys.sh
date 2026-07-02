#!/bin/bash
# Generate the ECCC buoy ERDDAP dataset fragment from the NCCSV files and write
# it into the cioos-national-erddap submodule's datasets.d/ directory, where the
# national sync (sync-erddap-datasets.py) merges it into datasets.xml.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUBMODULE="$ROOT/cioos-national-erddap"
DATASETS_DIR="$SUBMODULE/datasets"
OUT_DIR="$SUBMODULE/datasets.d"
LOGS_DIR="$ROOT/logs"

mkdir -p "$OUT_DIR" "$LOGS_DIR"

docker run --rm \
  -v "$DATASETS_DIR:/datasets" \
  -v "$LOGS_DIR:/erddapData/logs" \
  -v "$SUBMODULE/erddap/content:/usr/local/tomcat/content/erddap" \
  axiom/docker-erddap:2.23-jdk17-openjdk \
  bash -c "cd webapps/erddap/WEB-INF/ && bash GenerateDatasetsXml.sh EDDTableFromNccsvFiles /datasets/ECCCbuoys .*\.csv nothing 1 nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing"

# Extract just the <dataset>...</dataset> block into the submodule's datasets.d
awk '/<dataset /{f=1} f{print} /<\/dataset>/{f=0}' \
  "$LOGS_DIR/GenerateDatasetsXml.out" > "$OUT_DIR/ECCC.xml"

echo "Wrote fragment to $OUT_DIR/ECCC.xml"
