#!/bin/bash

ENV_FILE=/etc/docker/infrastructure/.env
if [ -f "$ENV_FILE" ]; then
    networkid=$(docker network ls -q -f 'name=siz')
    if [ -z "$networkid" ]; then
      echo 'Network "siz" does not exist. Creating it now...'
      #docker network create --subnet 172.30.2.0/24 --ip-range 172.30.2.128/25 --internal siz
      docker network create --subnet 172.30.2.0/24 --internal siz
    fi
    docker-compose up -d
else
    echo "$ENV_FILE does not exist."
fi