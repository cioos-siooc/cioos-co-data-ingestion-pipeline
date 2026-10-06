#!/bin/bash
# Interactive ERDDAP DasDds check against the files in ./datasets (hydrate it
# from the bucket first — see scripts/generate-datasets-xml.sh).
#
# Do not mount ./erddap/content over the image's content dir: it carries no
# setup.xml, and hiding the image's copy makes EDStatic abort at startup.
#
# DasDds ships inside ERDDAP, so the official image is enough — no axiom image,
# and no need for the CIOOS-CO-ERDDAP one either (its only addition is the
# /datasets.d assembly, which this tool does not use). Keep the pin in step with
# the runtime ERDDAP so what you check here is what will load there.
ERDDAP_IMAGE="${ERDDAP_IMAGE:-erddap/erddap:${ERDDAP_VERSION:-v2.31.1}}"

# EDStatic (which DasDds triggers) refuses to start while flagKeyKey is the
# shipped default, and the image only auto-generates one when it is starting
# Tomcat — not for a command-line tool like this. Any local value will do.
ERDDAP_flagKeyKey="${ERDDAP_flagKeyKey:-dasdds-local}"

docker run --rm -it \
  -e ERDDAP_flagKeyKey="$ERDDAP_flagKeyKey" \
  -v "$(cd "$(dirname "$0")/.." && pwd)/datasets:/datasets" \
  -v "$(cd "$(dirname "$0")/.." && pwd)/logs:/erddapData/logs" \
  "$ERDDAP_IMAGE" \
  bash -c "cd webapps/erddap/WEB-INF/ && bash DasDds.sh"
