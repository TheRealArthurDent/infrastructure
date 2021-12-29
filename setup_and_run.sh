#!/bin/bash

(
  cd setup || exit
  bash setup.sh local
  if [ -z "$WSL_DISTRO_NAME" ]; then
    sed -i -e 's/raspi\.\*/localhost/g' /etc/docker/infrastructure/nginx/nginx.conf
  fi
)
bash run.sh
docker restart nginx

