#!/bin/bash
# Install the Argo MEDS ERDDAP dataset fragment into the cioos-national-erddap
# submodule's datasets.d/ directory, where the national sync
# (sync-erddap-datasets.py) merges it into datasets.xml.
#
# The curated fragment (Argo_MEDS_Parser/erddap_config/ARGO_MEDS.xml) is the
# source of truth: its <sourceName>s/dtypes/units are reconciled against real
# GDAC <WMO>_prof.nc files and it carries the CIOOS/CDE-specific reshaping
# (TimeSeriesProfile + cf_role assignments — see the comments in the fragment).
# By default this script simply installs that curated fragment.
#
# Pass --generate to instead run ERDDAP's GenerateDatasetsXml tool
# (EDDTableFromMultidimNcFiles over N_PROF,N_LEVELS) against the published
# NetCDF files and drop a fresh *draft* alongside (logs/ARGO_MEDS.draft.xml)
# for comparison — useful when the Argo format version changes and the curated
# fragment needs updating. The draft is NOT installed automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUBMODULE="$ROOT/cioos-national-erddap"
DATASETS_DIR="$SUBMODULE/datasets"
OUT_DIR="$SUBMODULE/datasets.d"
LOGS_DIR="$ROOT/logs"
CURATED="$ROOT/Argo_MEDS_Parser/erddap_config/ARGO_MEDS.xml"

mkdir -p "$OUT_DIR"

if [[ "${1:-}" == "--generate" ]]; then
  mkdir -p "$LOGS_DIR"

  # The generator needs one sample file; use the first published float.
  SAMPLE="$(find "$DATASETS_DIR/ARGO_MEDS" -name '*_prof.nc' | sort | head -1 || true)"
  if [[ -z "$SAMPLE" ]]; then
    echo "No *_prof.nc under $DATASETS_DIR/ARGO_MEDS — run the argo_meds_parser first." >&2
    exit 1
  fi
  SAMPLE="/datasets/ARGO_MEDS/$(basename "$SAMPLE")"

  # ERDDAP's EDStatic startup aborts unless flagKeyKey is non-default; any local
  # value works for generating a fragment (override via the environment).
  ERDDAP_flagKeyKey="${ERDDAP_flagKeyKey:-generate-datasets-xml-local}"
  # Args after the sample file: group, DimensionsCSV, reloadEveryNMinutes,
  # pre/post/extract regex + column (unused), removeMVRows, sortFilesBySourceNames,
  # infoUrl, institution, summary, title, standardizeWhat, cacheFromUrl.
  docker run --rm \
    -e ERDDAP_flagKeyKey="$ERDDAP_flagKeyKey" \
    -v "$DATASETS_DIR:/datasets" \
    -v "$LOGS_DIR:/erddapData/logs" \
    -v "$SUBMODULE/erddap/content:/usr/local/tomcat/content/erddap" \
    axiom/docker-erddap:2.23-jdk17-openjdk \
    bash -c "cd webapps/erddap/WEB-INF/ && bash GenerateDatasetsXml.sh EDDTableFromMultidimNcFiles /datasets/ARGO_MEDS/ .*_prof\\.nc $SAMPLE nothing N_PROF,N_LEVELS 1440 nothing nothing nothing nothing true PLATFORM_NUMBER nothing nothing nothing nothing 0 nothing"

  awk '/<dataset /{f=1} f{print} /<\/dataset>/{f=0}' \
    "$LOGS_DIR/GenerateDatasetsXml.out" > "$LOGS_DIR/ARGO_MEDS.draft.xml"
  echo "Wrote draft (for review) to $LOGS_DIR/ARGO_MEDS.draft.xml"
  echo "Curated fragment remains the source of truth; edit $CURATED if needed."
fi

# Install the curated fragment into the submodule's datasets.d/.
cp "$CURATED" "$OUT_DIR/ARGO_MEDS.xml"
echo "Installed fragment to $OUT_DIR/ARGO_MEDS.xml"
