#!/bin/bash

ENV_FILE=/etc/docker/infrastructure/.env
if [ -f "$ENV_FILE" ]; then
    networkid=$(docker network ls -q -f 'name=siz')
    if [ -z "$networkid" ]; then
      echo 'Network "siz" does not exist. Creating it now...'
      #docker network create --subnet 172.30.2.0/24 --ip-range 172.30.2.128/25 --internal siz
      docker network create --subnet 172.30.2.0/24 --internal siz
    fi
    networkid=$(docker network ls -q -f 'name=outbound')
    if [ -z "$networkid" ]; then
      echo 'Network "outbound" does not exist. Creating it now...'
      docker network create --subnet 172.30.1.128/25 --ip-range 172.30.1.192/26 outbound
    fi
    export HOSTNAME
    export DUNGEON_IP; DUNGEON_IP=$(getent ahosts dungeon | awk '/STREAM/ {print $1; exit}')

    nice -n 10 docker compose config --services | while read service; do
      nice -n 10 docker compose pull "$service"
    done
    nice -n 10 docker compose up -d
else
    echo "$ENV_FILE does not exist."
fi
