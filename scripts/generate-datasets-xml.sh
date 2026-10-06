#!/bin/bash
# Generate a *draft* ERDDAP dataset fragment for one of the pipelines by
# running ERDDAP's GenerateDatasetsXml tool against the published files in
# ./datasets.
#
# Since the pipelines publish to the Juno bucket rather than to ./datasets,
# that directory is empty on a fresh checkout and must be hydrated first:
#
#   . ~/.juno-rclone.sh   # see "With rclone" in the README
#   rclone copy juno:cioos-juno-buoy-data/datasets ./datasets
#
# (Or re-run a pipeline with PUBLISH_URL=file:///app/datasets.)
#
# The dataset fragments are the source of truth and live in the cioos-co-erddap
# repo's datasets.d/ (not here). This script only produces logs/<NAME>.draft.xml
# for a human to diff against that fragment when a dataset's column set or file
# structure changes.
# (New stations/floats whose files match the existing fileNameRegex need no
# fragment change at all.)
#
# Usage: scripts/generate-datasets-xml.sh {eccc|meds|argo}
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATASETS_DIR="$ROOT/datasets"
LOGS_DIR="$ROOT/logs"

# The official ERDDAP image: GenerateDatasetsXml ships inside ERDDAP itself, so
# nothing here needs the axiom image (or the CIOOS-CO-ERDDAP one, which adds only
# the /datasets.d assembly this script has no use for).
#
# The batch argument sequences below are the prompt order of THIS ERDDAP version
# — they are positional, and the tool silently accepts a wrong-length sequence by
# shifting every answer into the next question. Prompt order changes between
# releases (2.31 added a sample-file and a charset question to
# EDDTableFromAsciiFiles and dropped "group" from EDDTableFromMultidimNcFiles),
# so on a version bump re-check the sequences against
# WEB-INF/classes/gov/noaa/pfel/erddap/GenerateDatasetsXml.java in the ERDDAP
# source and confirm each prompt echoed in the output holds the value you meant.
IMAGE="${ERDDAP_IMAGE:-erddap/erddap:${ERDDAP_VERSION:-v2.31.1}}"

# ERDDAP's EDStatic startup (which GenerateDatasetsXml triggers) aborts unless
# flagKeyKey is set to a non-default value. Any local value works for
# generating a draft; override via the environment if desired.
ERDDAP_flagKeyKey="${ERDDAP_flagKeyKey:-generate-datasets-xml-local}"

usage() { echo "Usage: $0 {eccc|meds|argo}" >&2; exit 1; }
[[ $# -eq 1 ]] || usage

case "$1" in
  eccc)
    NAME="ECCC"
    FRAGMENT="ECCC_MSC_BUOYS"
    # dir, regex, sampleFile, reload, pre/post/extract regex, extract column,
    # sortFilesBy, infoUrl, institution, summary, title, standardizeWhat,
    # cacheFromUrl (15). reload=1 matches the committed fragment: ECCC is
    # realtime.
    #
    # Do not touch the regex escaping in any of these three: $ARGS is expanded
    # into a `bash -c` string, so the backslashes are consumed and the tool
    # receives .*.csv, not .*\.csv. That looser regex is what the original
    # ECCC fragment was generated from, so leave it as-is to keep drafts
    # comparable.
    ARGS="EDDTableFromNccsvFiles /datasets/ECCCbuoys .*\.csv nothing 1 nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing"
    ;;
  meds)
    NAME="MEDS"
    FRAGMENT="DFO_MEDS_BUOYS"
    # dir, regex, sampleFile, charset, columnNamesRow, firstDataRow, separator,
    # reload, pre/post/extract regex, extract column, sortedColumn,
    # sortFilesBy, infoUrl, institution, summary, title, standardizeWhat,
    # cacheFromUrl (20). charset is a 2.31 addition whose default is
    # ISO-8859-1; UTF-8 is what the committed fragment uses, and a wrong
    # charset mangles the parsed columns.
    ARGS="EDDTableFromAsciiFiles /datasets/MEDS_CSV .*\\.csv nothing UTF-8 1 2 , nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing nothing"
    ;;
  argo)
    NAME="ARGO_MEDS"
    FRAGMENT="DFO_MEDS_ARGO"
    # The generator needs one sample file; use the first published float.
    SAMPLE="$(find "$DATASETS_DIR/ARGO_MEDS" -name '*_prof.nc' 2>/dev/null | sort | head -1 || true)"
    if [[ -z "$SAMPLE" ]]; then
      echo "No *_prof.nc under $DATASETS_DIR/ARGO_MEDS — run the argo_meds_parser first." >&2
      exit 1
    fi
    SAMPLE="/datasets/ARGO_MEDS/$(basename "$SAMPLE")"
    # Args after the sample file: DimensionsCSV, reloadEveryNMinutes,
    # pre/post/extract regex + column (unused), removeMVRows,
    # sortFilesBySourceNames, infoUrl, institution, summary, title,
    # standardizeWhat, treatDimensionsAs, cacheFromUrl (18 in total). 2.31
    # dropped the "group" prompt that used to follow the sample file and added
    # treatDimensionsAs before cacheFromUrl.
    ARGS="EDDTableFromMultidimNcFiles /datasets/ARGO_MEDS/ .*_prof\\.nc $SAMPLE N_PROF,N_LEVELS 1440 nothing nothing nothing nothing true PLATFORM_NUMBER nothing nothing nothing nothing 0 nothing nothing"
    ;;
  *) usage ;;
esac

mkdir -p "$LOGS_DIR"

# An empty ./datasets otherwise surfaces as an opaque ERDDAP error about
# finding no matching files.
if ! find "$DATASETS_DIR" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
  echo "$DATASETS_DIR is empty — the pipelines publish to the bucket now." >&2
  echo "Hydrate it first (see the header of this script):" >&2
  echo "  . ~/.juno-rclone.sh && rclone copy juno:cioos-juno-buoy-data/datasets $DATASETS_DIR" >&2
  exit 1
fi

docker run --rm \
  -e ERDDAP_flagKeyKey="$ERDDAP_flagKeyKey" \
  -v "$DATASETS_DIR:/datasets" \
  -v "$LOGS_DIR:/erddapData/logs" \
  "$IMAGE" \
  bash -c "cd webapps/erddap/WEB-INF/ && bash GenerateDatasetsXml.sh $ARGS"

# Extract just the <dataset>...</dataset> block as the draft.
awk '/<dataset /{f=1} f{print} /<\/dataset>/{f=0}' \
  "$LOGS_DIR/GenerateDatasetsXml.out" > "$LOGS_DIR/$NAME.draft.xml"

echo "Wrote draft (for review) to logs/$NAME.draft.xml"
echo "Diff it against datasets.d/$FRAGMENT.xml in cioos-co-erddap and update that file if needed."
