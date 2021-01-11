#!/bin/bash

cd setup
bash setup.sh
if [ -z "$WSL_DISTRO_NAME" ]; then
  sed -i -e 's/raspi[.]home/localhost/g' /etc/docker/infrastructure/nginx/nginx.conf
fi

cd ..
bash run.sh
docker restart nginx

