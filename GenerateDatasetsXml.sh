#!/bin/bash
docker run --rm -it \
  -v "$(pwd)/datasets:/datasets" \
  -v "$(pwd)/logs:/erddapData/logs" \
   -v $(pwd)/erddap/content:/usr/local/tomcat/content/erddap \
  axiom/docker-erddap:v2.28.1\
  bash -c "cd webapps/erddap/WEB-INF/ && bash GenerateDatasetsXml.sh"
