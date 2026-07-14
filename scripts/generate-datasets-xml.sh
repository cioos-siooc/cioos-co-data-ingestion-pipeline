#!/bin/bash
# Generate a *draft* ERDDAP dataset fragment for one of the pipelines by
# running ERDDAP's GenerateDatasetsXml tool against the published files in
# ./datasets.
#
# The committed fragments in datasets.d/ are the source of truth — the erddap
# service assembles them into erddap/content/datasets.xml at container start.
# This script only produces logs/<NAME>.draft.xml for a human to diff against
# the committed fragment when a dataset's column set or file structure changes.
# (New stations/floats whose files match the existing fileNameRegex need no
# fragment change at all.)
#
# ArgoFloats_Ifremer.xml needs no generator: it is a pure EDDTableFromErddap
# redirect of the remote dataset and allows no addAttributes/dataVariable
# tuning — edit the committed fragment directly.
#
# Usage: scripts/generate-datasets-xml.sh {eccc|meds|argo}
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATASETS_DIR="$ROOT/datasets"
LOGS_DIR="$ROOT/logs"

# The batch argument sequences below are tuned for this generator image; the
# runtime erddap service tracks its own (newer) tag independently.
IMAGE="axiom/docker-erddap:2.23-jdk17-openjdk"

# ERDDAP's EDStatic startup (which GenerateDatasetsXml triggers) aborts unless
# flagKeyKey is set to a non-default value. Any local value works for
# generating a draft; override via the environment if desired.
ERDDAP_flagKeyKey="${ERDDAP_flagKeyKey:-generate-datasets-xml-local}"

usage() { echo "Usage: $0 {eccc|meds|argo}" >&2; exit 1; }
[[ $# -eq 1 ]] || usage

case "$1" in
  eccc)
    NAME="ECCC"
    ARGS="EDDTableFromNccsvFiles /datasets/ECCCbuoys .*\.csv nothing 1 nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing"
    ;;
  meds)
    NAME="MEDS"
    ARGS="EDDTableFromAsciiFiles /datasets/MEDS_CSV .*\\.csv 1 2 , nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing"
    ;;
  argo)
    NAME="ARGO_MEDS"
    # The generator needs one sample file; use the first published float.
    SAMPLE="$(find "$DATASETS_DIR/ARGO_MEDS" -name '*_prof.nc' 2>/dev/null | sort | head -1 || true)"
    if [[ -z "$SAMPLE" ]]; then
      echo "No *_prof.nc under $DATASETS_DIR/ARGO_MEDS — run the argo_meds_parser first." >&2
      exit 1
    fi
    SAMPLE="/datasets/ARGO_MEDS/$(basename "$SAMPLE")"
    # Args after the sample file: group, DimensionsCSV, reloadEveryNMinutes,
    # pre/post/extract regex + column (unused), removeMVRows,
    # sortFilesBySourceNames, infoUrl, institution, summary, title,
    # standardizeWhat, cacheFromUrl.
    ARGS="EDDTableFromMultidimNcFiles /datasets/ARGO_MEDS/ .*_prof\\.nc $SAMPLE nothing N_PROF,N_LEVELS 1440 nothing nothing nothing nothing true PLATFORM_NUMBER nothing nothing nothing nothing 0 nothing"
    ;;
  *) usage ;;
esac

mkdir -p "$LOGS_DIR"

docker run --rm \
  -e ERDDAP_flagKeyKey="$ERDDAP_flagKeyKey" \
  -v "$DATASETS_DIR:/datasets" \
  -v "$LOGS_DIR:/erddapData/logs" \
  -v "$ROOT/erddap/content:/usr/local/tomcat/content/erddap" \
  "$IMAGE" \
  bash -c "cd webapps/erddap/WEB-INF/ && bash GenerateDatasetsXml.sh $ARGS"

# Extract just the <dataset>...</dataset> block as the draft.
awk '/<dataset /{f=1} f{print} /<\/dataset>/{f=0}' \
  "$LOGS_DIR/GenerateDatasetsXml.out" > "$LOGS_DIR/$NAME.draft.xml"

echo "Wrote draft (for review) to logs/$NAME.draft.xml"
echo "Diff it against the committed datasets.d/$NAME.xml and update that file if needed."
