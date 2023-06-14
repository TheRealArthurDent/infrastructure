#!/bin/bash

(
  cd setup || exit
  bash setup.sh local
  if [ -z "$WSL_DISTRO_NAME" ]; then
    sed -i -e 's/%HOSTNAME%\.\*/localhost/g' /etc/docker/infrastructure/nginx/nginx.conf
    sed -i -e 's/%HOSTNAME% /localhost /g' /etc/docker/infrastructure/nginx/nginx.conf
    sed -i -e 's/%HOSTNAME%/Wohnzimmer/g' /etc/docker/infrastructure/nginx/snippets/ssl-certificates.conf
  fi
)
bash run.sh
docker restart nginx
