#!/bin/bash

ARTIFACT=infrastructure

echo "Deploying $ARTIFACT to raspberry pi..."

TARGET_FOLDER=dev/$ARTIFACT

ssh pi@raspi -p 1357 'mkdir -p ~/dev/infrastructure'

(
  cd .. || exit
  tar -cf install.tar docker-compose.yaml run.sh setup
  scp -P 1357 install.tar pi@raspi:$TARGET_FOLDER
  rm install.tar
  ssh pi@raspi -p 1357 'cd ~/dev/infrastructure && ls && tar xf install.tar && rm install.tar && cd setup && sudo bash setup.sh && cd .. && sudo bash run.sh && echo "restarting nginx ..." && sudo docker restart nginx'
)
