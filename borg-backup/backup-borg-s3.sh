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

# Name to give this backup within the borg repo
BACKUP_NAME=backup-$(date +%Y-%m-%dT%H.%M)

printf "** Starting backup ${BACKUP_NAME} ...\n"

# Check environment vars are set
if [[ ! "$DOCKER_DIR" ]]; then
  printf "\n\n** Please provide with DOCKER_DIR on the environment\n"
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

# Local borg backup
printf "\n** Backing up ${DOCKER_DIR} with borg to repo ${BORG_REPO}...\n"
borg create ${BORG_REPO}::${BACKUP_NAME} ${DOCKER_DIR} --stats --exclude-from ${EXCLUDES_FILE} --compression zlib,6

# Define and store the backup's exit status
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up to ${BORG_REPO}"
	MESSAGE="Error backing up to ${BORG_REPO}"
	OPERATION_STATUS=1
fi

# External Drive borg backup
printf "\n** Backing up ${DOCKER_DIR} with borg to repo ${BORG_EXTDRIVE_REPO}...\n"
export BORG_PASSPHRASE=$BORG_EXTDRIVE_PASSPHRASE
borg create ${BORG_EXTDRIVE_REPO}::${BACKUP_NAME} ${DOCKER_DIR} --stats --exclude-from ${EXCLUDES_FILE} --compression zlib,6

# Define and store the backup's exit status
OPERATION_STATUS=$?
if [ $OPERATION_STATUS != 0 ]; then
	printf "\n** ERROR backing up to ${BORG_EXTDRIVE_REPO}"
	MESSAGE="Error backing up to ${BORG_REPO}"
	OPERATION_STATUS=1
fi

# Only continue if backup was actually successful
if [ $OPERATION_STATUS == 0 ]; then
	# Clean up old backups: keep last daily, last weekly and last monthly
	printf "\n** Pruning old backups...\n"
	borg prune -v --list --keep-daily=1 --keep-weekly=1 --keep-monthly=1

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
	BORG_STATS=$(borg info ::${BACKUP_NAME})
	AWS_STATS=$(aws s3 ls --profile=${BORG_S3_BACKUP_AWS_PROFILE} --summarize --recursive s3://${BORG_S3_BACKUP_BUCKET} | tail -1 | awk '{ printf "%.3f GB", $3/1024/1024/1024; }')
	NL=$'\n'
	
	STATUS_MESSAGE="Backup successful"
	MESSAGE="${BORG_STATS}${NL}AWS bucket size : ${AWS_STATS}"
else
	STATUS_MESSAGE="Backup failed"
	MESSAGE="Backup to S3 failed"
fi

# Stopping docker containers to ensure uncorrupted files
printf "\n\n** Starting docker containers...\n"
docker start $(docker ps -a -q)

# Send Pushover notification and exit appropriately
# https://api.pushover.net/1/messages.json?token=${}
printf "\n** Sending notification to pushover...\n"
if [ $OPERATION_STATUS == 0 ]; then
	curl -s "${PUSHOVER_URL}" -F "token=${PUSHOVER_TOKEN}" -F "user=${PUSHOVER_USER_TOKEN}" -F "title=${STATUS_MESSAGE}" -F "message=${MESSAGE}" -F "priority=0" > /dev/null
	# curl -s "${GOTIFY_URL}/message?token=${GOTIFY_TOKEN}" -F "title=${STATUS_MESSAGE}" -F "message=${MESSAGE}" -F "priority=5" > /dev/null
else
	curl -s "${PUSHOVER_URL}" -F "token=${PUSHOVER_TOKEN}" -F "user=${PUSHOVER_USER_TOKEN}" -F "title=${STATUS_MESSAGE}" -F "message=${MESSAGE}" -F "priority=0" > /dev/null
	# curl -s "${GOTIFY_URL}/message?token=${GOTIFY_TOKEN}" -F "title=${STATUS_MESSAGE}" -F "message=${MESSAGE}" -F "priority=5" > /dev/null
fi

# Same as above, but on stdout
printf "\n** ${STATUS_MESSAGE}\n"
exit ${OPERATION_STATUS}
