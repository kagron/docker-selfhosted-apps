#!/usr/bin/env bash
cd /home/kyle/docker-selfhosted-apps/borg-backup
source ./.venv/bin/activate

python3 backup-borg-s3.py

deactivate