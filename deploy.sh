#!/bin/bash
set +x

ARTIFACT=infrastructure
DEFAULT_TARGET="raspi"
TARGET=$1
PORT=$2
USER=$3

# No default target: a bare ./deploy.sh used to deploy to production (raspi).
if [ -z "$TARGET" ]; then
  echo "usage: $0 <target> [ssh port] [ssh user]   e.g. $0 testbed 4242 testuser, or $0 $DEFAULT_TARGET" >&2
  exit 2
fi
if [ -z "$PORT" ]; then
  if [ "$TARGET" = "$DEFAULT_TARGET" ]; then
    PORT="1357"
  elif [ "$TARGET" = "himbeere" ]; then
    PORT="1357"
  elif [ "$TARGET" = "erdbeere" ]; then
    PORT="1358"
  else
    echo "unknown target - defaulting to ssh port 22"
    PORT="22"
  fi
fi
if [ -z "$USER" ]; then
  if [ "$TARGET" = "$DEFAULT_TARGET" ]; then
    USER="smarthome"
  elif [ "$TARGET" = "himbeere" ]; then
    USER="pi"
  elif [ "$TARGET" = "erdbeere" ]; then
    USER="held"
  else
    echo "unknown target - defaulting to ssh port 22"
    USER="pi"
  fi
fi

echo "Deploying $ARTIFACT to $TARGET..."

TARGET_FOLDER=dev/$ARTIFACT

ssh $USER@$TARGET -p $PORT 'mkdir -p ~/$TARGET_FOLDER'

echo "Deleting old tmp folders"
find /tmp -type d -regextype egrep -iregex "/tmp/$ARTIFACT-[a-z0-9]+" -mtime +30 -exec rm -rf {} + 2>/dev/null

(
  tmp_dir=$(mktemp -d -t $ARTIFACT-XXXXXXXXXX)
  echo "Created new tmp folder $tmp_dir"
  cp docker-compose.yaml  $tmp_dir
  cp run.sh               $tmp_dir
  cp -r setup             $tmp_dir
  rm $tmp_dir/setup/.env
  (
    cd $tmp_dir || exit
    grep -rl '%HOSTNAME%' | xargs -i@ sed -i "s/%HOSTNAME%/$TARGET/g" @
    if [ "$TARGET" != "DEFAULT_TARGET" ]; then
      sed -i "s/$DEFAULT_TARGET/$TARGET/g" docker-compose.yaml
    fi
    tar -cf install.tar *
    ssh $USER@$TARGET -p $PORT "mkdir -p $TARGET_FOLDER"
    scp -P $PORT install.tar $USER@$TARGET:$TARGET_FOLDER
    ssh $USER@$TARGET -p $PORT 'cd ~/dev/infrastructure && ls && tar xf install.tar && rm install.tar && cd setup && sudo bash setup.sh && cd .. && sudo bash run.sh && echo "restarting nginx ..." && sudo docker restart nginx'
  )
)
