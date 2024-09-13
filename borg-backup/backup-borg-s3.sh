#!/usr/bin/env bash

ENV_FILE=$(dirname $0)/.env
if [ ! -f "${ENV_FILE}" ]; then
	printf "\n\n** Please create an env file with the required environment variables at '${ENV_FILE}'."
	exit 1
fi
# Export env variables
set -o allexport
source $ENV_FILE
set +o allexport

# Name to give these backups within the borg repo
DOCKER_BACKUP_PREFIX=docker-backup
ROUTER_BACKUP_PREFIX=router-backup
PIHOLE_BACKUP_PREFIX=pihole-backup
ALL_PREFIXES=($DOCKER_BACKUP_PREFIX $ROUTER_BACKUP_PREFIX $PIHOLE_BACKUP_PREFIX)
CURRENT_TIME=$(date +%Y-%m-%dT%H.%M)

DOCKER_BACKUP_NAME=${DOCKER_BACKUP_PREFIX}-${CURRENT_TIME}
ROUTER_BACKUP_NAME=${ROUTER_BACKUP_PREFIX}-${CURRENT_TIME}
PIHOLE_BACKUP_NAME=${PIHOLE_BACKUP_PREFIX}-${CURRENT_TIME}

printf "** Starting backup ${DOCKER_BACKUP_NAME} ...\n"

# Check environment vars are set
if [[ ! "$DOCKER_DIR" ]]; then
  printf "\n\n** Please provide with DOCKER_DIR on the environment\n"
  exit 1
fi

if [[ ! "$ROUTER_HOST" ]]; then
  printf "\n\n** Please provide with ROUTER_HOST on the environment\n"
  exit 1
fi

if [[ ! "$PIHOLE_HOST" ]]; then
  printf "\n\n** Please provide with PIHOLE_HOST on the environment\n"
  exit 1
fi

if [[ ! "$SSH_PRIVATE_KEY_PATH" ]]; then
  printf "\n\n** Please provide with SSH_PRIVATE_KEY_PATH on the environment\n"
  exit 1
fi

if [[ ! "$BORG_REPO" ]]; then
  printf "\n\n** Please provide with BORG_REPO on the environment\n"
  exit 1
fi

if [[ ! "$BORG_S3_BACKUP_BUCKET" ]]; then
  printf "\n\n** Please provide with BORG_S3_BACKUP_BUCKET on the environment\n"
  exit 1
fi

if [[ ! "$BORG_S3_BACKUP_AWS_PROFILE" ]]; then
  printf "\n\n** Please provide with BORG_S3_BACKUP_AWS_PROFILE on the environment (awscli profile)\n"
  exit 1
fi

if [[ ! "$PUSHOVER_URL" && ! "$PUSHOVER_TOKEN" && ! "$PUSHOVER_USER_TOKEN" ]]; then
  printf "\n\n** Please provide with PUSHOVER_URL and PUSHOVER_TOKEN and PUSHOVER_TOKEN on the environment\n"
  exit 1
fi

SYNC_COMMAND="aws s3 sync ${BORG_REPO} s3://${BORG_S3_BACKUP_BUCKET} --profile=${BORG_S3_BACKUP_AWS_PROFILE} --delete"

EXCLUDES_FILE=$(dirname $0)/excludes.txt
if [ ! -f "${EXCLUDES_FILE}" ]; then
	printf "\n\n** Please create an excludes file (even if empty) at '${EXCLUDES_FILE}'."
	exit 1
fi

# Stopping docker containers to ensure uncorrupted files
printf "\n** Stopping docker containers...\n"
docker stop $(docker ps -a -q)

# Docker borg backup
printf "\n** Backing up ${DOCKER_DIR} with borg to repo ${BORG_REPO}...\n"
borg create ${BORG_REPO}::${DOCKER_BACKUP_NAME} ${DOCKER_DIR} --stats --exclude-from ${EXCLUDES_FILE} --compression zlib,6

# Define and store the backup's exit status
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up docker to ${BORG_REPO}"
	MESSAGE="Error backing up docker to ${BORG_REPO}"
	OPERATION_STATUS=1
fi

# Pi-Hole borg backup
printf "\n** Backing up Pi-Hole with borg to repo ${BORG_REPO}...\n"

PIHOLE_BACKUP_DIR=pi-hole-backup

# Run Teleport command remotely
ssh -i ${SSH_PRIVATE_KEY_PATH} pi@${PIHOLE_HOST} pihole -a -t

# Move to current local directory
scp -i ${SSH_PRIVATE_KEY_PATH} pi@${PIHOLE_HOST}:pi-hole* .

# Remove file remotely
ssh -i ${SSH_PRIVATE_KEY_PATH} pi@${PIHOLE_HOST} rm -f pi-hole*

# Make directory
mkdir ${PIHOLE_BACKUP_DIR}

# Untar it into directory
tar -xzvf pi-hole-raspberrypi-teleporter* -C ${PIHOLE_BACKUP_DIR}

# Create borg backup
borg create ${BORG_REPO}::${PIHOLE_BACKUP_NAME} ${PIHOLE_BACKUP_DIR} --stats --compression zlib,6
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up pihole to ${BORG_REPO}"
	MESSAGE="Error backing up pihole to ${BORG_REPO}"
	OPERATION_STATUS=1
fi

# OpenWrt.lan borg backup
printf "\n** Backing up OpenWrt.lan with borg to repo ${BORG_REPO}...\n"

ROUTER_BACKUP_DIR=openwrt-backup
ROUTER_TAR_NAME=openwrt.tar.gz

# Run tar command remotely
ssh -i ${SSH_PRIVATE_KEY_PATH} root@${ROUTER_HOST} tar -cvzf ${ROUTER_TAR_NAME} /etc

# Move to current local directory
scp -i ${SSH_PRIVATE_KEY_PATH} root@${ROUTER_HOST}:${ROUTER_TAR_NAME} .

# Remove file remotely
ssh -i ${SSH_PRIVATE_KEY_PATH} root@${ROUTER_HOST} rm -f ${ROUTER_TAR_NAME}

# Make directory
mkdir ${ROUTER_BACKUP_DIR}

# Untar it into directory
tar -xzvf ${ROUTER_TAR_NAME} -C ${ROUTER_BACKUP_DIR}

# Create borg backup
borg create ${BORG_REPO}::${ROUTER_BACKUP_NAME} ${ROUTER_BACKUP_DIR} --stats --compression zlib,6
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up OpenWrt to ${BORG_REPO}"
	MESSAGE="Error backing up OpenWrt to ${BORG_REPO}"
	OPERATION_STATUS=1
fi

# External Drive borg backup
printf "\n** Backing up ${DOCKER_DIR} with borg to repo ${BORG_EXTDRIVE_REPO}...\n"
export BORG_PASSPHRASE=$BORG_EXTDRIVE_PASSPHRASE
borg create ${BORG_EXTDRIVE_REPO}::${DOCKER_BACKUP_NAME} ${DOCKER_DIR} --stats --exclude-from ${EXCLUDES_FILE} --compression zlib,6

# Define and store the backup's exit status
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up docker to ${BORG_EXTDRIVE_REPO}"
	MESSAGE="Error backing up docker to ${BORG_EXTDRIVE_REPO}"
	OPERATION_STATUS=1
fi

printf "\n** Backing up pihole with borg to repo ${BORG_EXTDRIVE_REPO}...\n"
borg create ${BORG_EXTDRIVE_REPO}::${PIHOLE_BACKUP_NAME} ${PIHOLE_BACKUP_DIR} --stats --compression zlib,6

# Define and store the backup's exit status
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up pihole to ${BORG_EXTDRIVE_REPO}"
	MESSAGE="Error backing up pihole to ${BORG_EXTDRIVE_REPO}"
	OPERATION_STATUS=1
fi

printf "\n** Backing up OpenWrt with borg to repo ${BORG_EXTDRIVE_REPO}...\n"
borg create ${BORG_EXTDRIVE_REPO}::${ROUTER_BACKUP_NAME} ${ROUTER_BACKUP_DIR} --stats --compression zlib,6

# Define and store the backup's exit status
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up OpenWrt to ${BORG_EXTDRIVE_REPO}"
	MESSAGE="Error backing up OpenWrt to ${BORG_EXTDRIVE_REPO}"
	OPERATION_STATUS=1
fi

# Cleanup Pihole extraction
printf "\n** Running rm -rf pi* \n"
rm -rf pi*

# Cleanup Router extraction
printf "\n** Running rm -rf openwrt* \n"
rm -rf openwrt*

# Only continue if backup was actually successful
if [ $OPERATION_STATUS == 0 ]; then
	# Clean up old backups: keep last daily, last weekly and last monthly
	printf "\n** Pruning old backups from repo ${BORG_EXTDRIVE_REPO}...\n"
	for p in ${ALL_PREFIXES[@]}; do
		borg prune -v -P ${p} --list --keep-daily=1 --keep-weekly=1 --keep-monthly=1 ${BORG_EXTDRIVE_REPO}
	done
	
	# Reset variables to get first backup's passphrase
	set -o allexport
	source $ENV_FILE
	set +o allexport

	printf "\n** Pruning old backups from repo ${BORG_REPO}...\n"
	for p in ${ALL_PREFIXES[@]}; do
		borg prune -v -P ${p} --list --keep-daily=1 --keep-weekly=1 --keep-monthly=1 ${BORG_REPO}
	done

	# Check and compare backup size with threshold
	if [[ "$BACKUP_THRESHOLD" && $BACKUP_THRESHOLD != 0 ]]; then
	  BACKUP_SIZE=$(borg info --json | jq .cache.stats.unique_csize | awk '{ printf "%d", $1/1024/1024/1024; }')
	  if [[ $BACKUP_SIZE -gt $BACKUP_THRESHOLD ]]; then
	     printf "Backup size ${BACKUP_SIZE} GB is larger than the threshold ${BACKUP_THRESHOLD} GB"
	     OPERATION_STATUS=1
	     MESSAGE="Backup size ${BACKUP_SIZE} GB is larger than the threshold ${BACKUP_THRESHOLD} GB"
	  fi
	fi
fi

# Sync to AWS if the backup size if lower than the threshold
if [ $OPERATION_STATUS == 0 ]; then
	# Sync borg repo to s3
	printf "\n** Syncing to s3 bucket ${BORG_S3_BACKUP_BUCKET}...\n"
	borg with-lock ${BORG_REPO} ${SYNC_COMMAND}

	# We do care about s3 sync succeeding though
	OPERATION_STATUS=$?
fi

if [ $OPERATION_STATUS == 0 ]; then
	# Create Pushover stats
	BORG_NAS_STATS=$(borg info ${BORG_REPO}::${DOCKER_BACKUP_NAME})
	
	export BORG_PASSPHRASE=$BORG_EXTDRIVE_PASSPHRASE
	BORG_EXT_STATS=$(borg info ${BORG_EXTDRIVE_REPO}::${DOCKER_BACKUP_NAME})
	
	BORG_STATS="${BORG_NAS_STATS}"
	AWS_STATS=$(aws s3 ls --profile=${BORG_S3_BACKUP_AWS_PROFILE} --summarize --recursive s3://${BORG_S3_BACKUP_BUCKET} | tail -1 | awk '{ printf "%.3f GB", $3/1024/1024/1024; }')
	NL=$'\n'
	
	STATUS_MESSAGE="Backup successful"
	MESSAGE="${BORG_STATS}${NL}AWS bucket size : ${AWS_STATS}"
	printf "\n** Borg NAS Stats: ${BORG_NAS_STATS}"
	printf "\n** Borg Ext Drive Stats: ${BORG_EXT_STATS}"
	printf "\n** AWS bucket size: ${AWS_STATS}"
else
	STATUS_MESSAGE="Backup failed"
	MESSAGE="Backup to S3 failed"
fi

# Stopping docker containers to ensure uncorrupted files
printf "\n\n** Starting docker containers...\n"
docker start $(docker ps -a -q)

# Send Pushover notification and exit appropriately
printf "\n** Sending notification to pushover...\n"
if [ $OPERATION_STATUS == 0 ]; then
	curl -s "${PUSHOVER_URL}" -F "token=${PUSHOVER_TOKEN}" -F "user=${PUSHOVER_USER_TOKEN}" -F "title=${STATUS_MESSAGE}" -F "message=${MESSAGE}" -F "priority=0" > /dev/null
else
	curl -s "${PUSHOVER_URL}" -F "token=${PUSHOVER_TOKEN}" -F "user=${PUSHOVER_USER_TOKEN}" -F "title=${STATUS_MESSAGE}" -F "message=${MESSAGE}" -F "priority=0" > /dev/null
fi

# Same as above, but on stdout
printf "\n** ${STATUS_MESSAGE}\n"
exit ${OPERATION_STATUS}
