#!/bin/bash

ENV_FILE=/etc/docker/infrastructure/.env
if [ -f "$ENV_FILE" ]; then
    networkid=$(docker network ls -q -f 'name=nginx')
    if [ -z "$networkid" ]; then
      echo 'Network "nginx" does not exist. Creating it now...'
      docker network create nginx
    fi
    #docker-compose --env-file $ENV_FILE up -d
    docker-compose up -d
else
    echo "$ENV_FILE does not exist."
fi