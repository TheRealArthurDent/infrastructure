#!/bin/bash

# global
export ARTIFACT=infrastructure
export ENV_FOLDER=/etc/docker/$ARTIFACT
export ENV_FILE=$ENV_FOLDER/.env

if [ "$1" == "local" ]; then
  mkdir -p $ENV_FOLDER
  cp .env $ENV_FILE
  chmod 600 $ENV_FILE
  #cp .outbound.env $ENV_FOLDER/.outbound.env
  #chmod 600 $ENV_FOLDER/.outbound.env
else
  if [ ! -f "$ENV_FILE" ]; then
    echo "WARNING: Global env file $ENV_FILE does not exist. Installation will not work."
    exit 1
  fi
fi

# Get some decent Diffie-Hellman keys https://weakdh.org/
DH_PARAM_FILE=/etc/ssl/certs/dhparam.pem
if [ ! -f "$DH_PARAM_FILE" ]; then
  "No $DH_PARAM_FILE found. Generating some decent Diffie-Hellman keys..."
  openssl dhparam -out $DH_PARAM_FILE 4096
fi

# nginx
cp -R ./nginx $ENV_FOLDER
find $ENV_FOLDER/nginx -type f -exec chmod 600 -- {} +

# self signed SSL certs
DOMAIN_NAME=raspi
CERT_FILE=$ENV_FOLDER/nginx/$DOMAIN_NAME.pem
KEY_FILE=$ENV_FOLDER/nginx/$DOMAIN_NAME.key
if [ ! -f "$CERT_FILE" ]; then
  read -n 1 -rp "No SSL certificate file has been found. Create new cert file? [Y/n]: " create
  echo
  if [ "$create" == "n" ]; then
    echo "No certificate files will be created."
  else
    openssl req -x509 -nodes -days 365 -subj "/C=DE/ST=BY/O=None/CN=$DOMAIN_NAME" -addext "subjectAltName=DNS:$DOMAIN_NAME" -newkey rsa:2048 -keyout $KEY_FILE -out $CERT_FILE;
  fi
fi

# portainer
mkdir -p $ENV_FOLDER/portainer/data

# pihole
mkdir -p $ENV_FOLDER/pihole/{pihole,dnsmasq.d}
find ./pihole -type f -exec chmod 644 -- {} +
cp -R ./pihole $ENV_FOLDER
