#!/bin/bash

# global
mkdir -p /etc/docker/infrastructure
cp .env /etc/docker/infrastructure/.env
chmod 600 /etc/docker/infrastructure/.env

# Get some decent Diffie-Hellman keys https://weakdh.org/
DH_PARAM_FILE=/etc/ssl/certs/dhparam.pem
if [ ! -f "$DH_PARAM_FILE" ]; then
  "No $DH_PARAM_FILE found. Generating some decent Diffie-Hellman keys..."
  openssl dhparam -out $DH_PARAM_FILE 4096
fi

# nginx
cp -R ./nginx /etc/docker/infrastructure
find /etc/docker/infrastructure -type f -exec chmod 600 -- {} +

# self signed SSL certs
DOMAIN_NAME=raspi
CERT_FILE=/etc/docker/infrastructure/nginx/$DOMAIN_NAME.crt
KEY_FILE=/etc/docker/infrastructure/nginx/$DOMAIN_NAME.key
if [ ! -f "$CERT_FILE" ]; then
  # blocks the execution when run via ssh
  #read -n 1 -rp "No SSL certificate file has been found. Create new cert file? [Y/n]: " create
  #echo
  #if [ "$create" == "n" ]; then
  #  echo "No certificate files will be created."
  #else
    echo "WARNING: No SSL certificate file has been found. Creating self signed certificate."
    openssl req -x509 -nodes -days 365 -subj "/C=DE/ST=BY/O=None/CN=$DOMAIN_NAME" -addext "subjectAltName=DNS:$DOMAIN_NAME" -newkey rsa:2048 -keyout $KEY_FILE -out $CERT_FILE;
  #fi
fi

mkdir -p /etc/docker/infrastructure/portainer/data
