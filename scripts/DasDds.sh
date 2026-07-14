#!/bin/bash
docker run --rm -it \
  -v "$(cd "$(dirname "$0")/.." && pwd)/datasets:/datasets" \
  -v "$(cd "$(dirname "$0")/.." && pwd)/logs:/erddapData/logs" \
  -v "$(cd "$(dirname "$0")/.." && pwd)/erddap/content:/usr/local/tomcat/content/erddap" \
  axiom/docker-erddap:v2.28.1 \
  bash -c "cd webapps/erddap/WEB-INF/ && bash DasDds.sh"
