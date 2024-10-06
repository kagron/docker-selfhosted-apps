#!/usr/bin/env python3
import os
import subprocess
import logging
from datetime import datetime
from dotenv import load_dotenv

# Get environment variables from .env 
load_dotenv()

LOG_PATH = os.environ.get("LOG_PATH")
LOG_LEVEL = os.environ.get("LOG_LEVEL")
NUM_LOG_LEVEL = getattr(logging, LOG_LEVEL.upper(), None)

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(message)s', datefmt = '%m/%d/%Y %I:%M:%S %p', filename = LOG_PATH, level = NUM_LOG_LEVEL)
logger = logging.getLogger(__name__)

HOME_BACKUP_PREFIX = "home-backup"
ROUTER_BACKUP_PREFIX = "router-backup"
PIHOLE_BACKUP_PREFIX = "pihole-backup"
ETC_BACKUP_PREFIX = "etc-backup"
ALL_PREFIXES = (HOME_BACKUP_PREFIX, 
                ROUTER_BACKUP_PREFIX, 
                PIHOLE_BACKUP_PREFIX, 
                ETC_BACKUP_PREFIX)
CURRENT_TIME = datetime.now().strftime("%Y-%m-%dT%H.%M")
PIHOLE_BACKUP_DIR="pi-hole-backup"
ROUTER_BACKUP_DIR="openwrt-backup"
ROUTER_TAR_NAME="openwrt.tar.gz"
DEBUG=False

ENV_VARS = (
   # "DOCKER_DIR",
   "ROUTER_HOST",
   "PIHOLE_HOST",
   "SSH_PRIVATE_KEY_PATH",
   "BORG_REPO",
   "BORG_EXTDRIVE_REPO",
   "BORG_S3_BACKUP_BUCKET",
   "BORG_S3_BACKUP_AWS_PROFILE",
   "PUSHOVER_URL",
   "PUSHOVER_TOKEN",
   "PUSHOVER_USER_TOKEN"
)

def main():
   """Backup all the goodies"""
   logger.info(f"Starting backup {CURRENT_TIME}")

   # Verify all required variables are set
   for env_var in ENV_VARS:
      if not os.environ.get(env_var):
         logger.error(f"Please provide {env_var} in .env file")
         exit(1)

   # Prepare backup directories
   create_router_archive = not get_router_backup()
   create_pihole_archive = not get_pihole_backup()

   stop_docker()
   
   borg_repo = os.environ.get("BORG_REPO")
   status = backup_to_repo(borg_repo = borg_repo,
                  create_router_archive = create_router_archive,
                  create_pihole_archive = create_pihole_archive)
   
   borg_info = ""
   if status == 0:
      prune_repo(borg_repo = borg_repo)
      backup_to_aws(borg_repo)
      borg_info = get_repo_info(borg_repo = borg_repo)
   
   # # Change passphrase for next repo
   os.environ['BORG_PASSPHRASE'] = os.environ.get("BORG_EXTDRIVE_PASSPHRASE")
   borg_ext_repo = os.environ.get("BORG_EXTDRIVE_REPO")

   status = backup_to_repo(borg_repo = borg_ext_repo,
                  create_router_archive = create_router_archive,
                  create_pihole_archive = create_pihole_archive)
   if status == 0:
      prune_repo(borg_repo = borg_ext_repo)

   cleanup()

   start_docker()

   aws_bucket_size = get_aws_bucket_size()

   logger.info(f"Borg repo {borg_repo} stats: \n{borg_info}")
   logger.info(f"AWS bucket size: {aws_bucket_size}")

   if status == 0:
      send_notification(title="Backup Successful", message=f"Borg NAS Stats: \n{borg_info}\nAWS bucket size: {aws_bucket_size}")
   else:
      send_notification(title="Backup failed", message=f"Exit code {status}")

def borg_create(borg_repo: str, 
           backup_name: str, 
           backup_dir: str, 
           excludes_file: str, 
           dry_run = False):
   """Creates a borg archive"""
   logger.info(f"Backing up {backup_dir} with borg to {borg_repo}::{backup_name}")
   cmd = [
      "borg create " +
      ("--dry-run " if dry_run else "") +
      f"{borg_repo}::{backup_name} " +
      f"{backup_dir} " +
      ("--stats " if not dry_run else "-v ") +
      f"--exclude-from {excludes_file} " +
      "--compression zlib,6"
   ]
   result = subprocess.run(cmd, check=True, shell=True)
   logger.debug(result)
   return result

def ssh(host: str, command: str):
   """Runs a ssh command"""
   logger.info(f"Initiating ssh command: {host} {command}")
   private_key_path = os.environ.get("SSH_PRIVATE_KEY_PATH")

   return subprocess.run([f"ssh -i {private_key_path} {host} {command}"], check=not DEBUG, shell=True)

def scp(host: str, remote_path: str, local_path: str):
   """Runs a scp command"""
   private_key_path = os.environ.get("SSH_PRIVATE_KEY_PATH")
   logger.info(f"Initiating scp command: {host}:{remote_path} {local_path}")

   return subprocess.run([f"scp -i {private_key_path} {host}:{remote_path} {local_path}"], check=not DEBUG, shell=True)

def get_router_backup():
   """Retrieves /etc config files from router.  Returns 0 when successful"""
   logger.info("Retrieving Openwrt.lan backup")

   router_host = os.environ.get("ROUTER_HOST")
   user_and_host = f"root@{router_host}"

   try:
      result = ssh(user_and_host, f"tar -cvzf {ROUTER_TAR_NAME} /etc")
      result = scp(user_and_host, ROUTER_TAR_NAME, ".")
      result = ssh(user_and_host, f"rm -rf {ROUTER_TAR_NAME}")
   except subprocess.CalledProcessError as error:
      logger.debug(result)
      send_notification(title="Error retrieving Openwrt.lan backup", message=error)
      return 1;

   os.mkdir(ROUTER_BACKUP_DIR)

   result = subprocess.run(["tar", "xzvf", ROUTER_TAR_NAME, "-C", ROUTER_BACKUP_DIR])
   logger.debug(result)
   return result.returncode
   


def get_pihole_backup():
   """Retrieves /etc config files from pihole.  Returns 0 when successful"""
   logger.info("Retrieving Pi-Hole backup")
   
   pihole_host = os.environ.get("PIHOLE_HOST")
   user_and_host = f"pi@{pihole_host}"

   try:
      result = ssh(user_and_host, "pihole -a -t")
      result = scp(user_and_host, "pi-hole*", ".")
      result = ssh(user_and_host, f"rm -rf pi-hole*")
   except subprocess.CalledProcessError as error:
      logger.debug(result)
      send_notification(title="Error retrieving Pi-Hole backup", message=error)
      return 1

   os.mkdir(PIHOLE_BACKUP_DIR)

   result = subprocess.run([f"tar xzvf pi-hole-raspberrypi-teleporter* -C {PIHOLE_BACKUP_DIR}"], shell=True)
   logger.debug(result)
   return result.returncode


def backup_to_repo(borg_repo: str, create_router_archive: bool, create_pihole_archive: bool):
   """Performs the backups to repo"""
   logger.info(f"Backing up to repo {borg_repo}")
   excludes = "excludes.txt"

   # Docker
   result = borg_create(
      borg_repo = borg_repo,
      backup_name = f"{HOME_BACKUP_PREFIX}-{CURRENT_TIME}",
      backup_dir = "/home",
      excludes_file = excludes,
      dry_run = DEBUG)

   # Router
   if create_router_archive:
      result = borg_create(
         borg_repo = borg_repo,
         backup_name = f"{ROUTER_BACKUP_PREFIX}-{CURRENT_TIME}",
         backup_dir = ROUTER_BACKUP_DIR,
         excludes_file = excludes,
         dry_run = DEBUG)

   # Pihole
   if create_pihole_archive:
      result = borg_create(
         borg_repo = borg_repo,
         backup_name = f"{PIHOLE_BACKUP_PREFIX}-{CURRENT_TIME}",
         backup_dir = PIHOLE_BACKUP_DIR,
         excludes_file = excludes,
         dry_run = DEBUG)

   # /etc
   result = borg_create(
      borg_repo = borg_repo,
      backup_name = f"{ETC_BACKUP_PREFIX}-{CURRENT_TIME}",
      backup_dir = "/etc",
      excludes_file = excludes,
      dry_run = DEBUG)
   
   return result.returncode

def stop_docker():
   """Stops all running docker containers"""
   logger.info("Stopping docker containers")

   result = subprocess.run(["docker stop $(docker ps -a -q)"], shell=True)
   logger.debug(result)

def start_docker():
   """Starts all docker containers"""
   logger.info("Starting docker containers")

   result = subprocess.run(["docker start $(docker ps -a -q)"], shell=True)
   logger.debug(result)

def send_notification(title: str, message: str, priority = 0):
   """Sends notification to pushover"""
   logger.info("Sending notification to pushover")
   pushover_url = os.environ.get("PUSHOVER_URL")
   pushover_token = os.environ.get("PUSHOVER_TOKEN")
   pushover_user_token = os.environ.get("PUSHOVER_USER_TOKEN")

   cmd = [f"curl -s {pushover_url} " +
          f"-F \"token={pushover_token}\" " +
          f"-F \"user={pushover_user_token}\" " +
          f"-F \"title={title}\" " +
          f"-F \"message={message}\" " +
          f"-F \"priority={priority}\""]
   result = subprocess.run(cmd, shell=True)
   logger.debug(result)

def prune_repo(borg_repo: str):
   """Prune old archives from borg repo"""
   logger.info(f"Pruning old backups from repo {borg_repo}")

   for prefix in ALL_PREFIXES:
      result = subprocess.run([
         f"borg prune -v -P {prefix} --list --keep-daily=1 --keep-weekly=1 --keep-monthly=1 {borg_repo}"
      ], shell=True)
      logger.debug(result)

def get_repo_info(borg_repo: str, backup_name = "", json = False):
   """Runs a borg info command"""
   logger.info(f"Running borg info {borg_repo}")
   result = subprocess.run(["borg info " + 
                   ("--json " if json else "") + 
                   borg_repo + 
                   (f"::{backup_name}" if backup_name != "" else "")
                   ], capture_output=True, text=True, shell=True)
   logger.debug(result)
   return result.stdout if not result.returncode else ""

def get_backup_size(borg_repo: str, backup_name = ""):
   """Gets backup size.  Total backup size if no backup_name specified"""
   logger.info(f"Getting borg backup size for: {borg_repo}" + (f"::{backup_name}" if backup_name != "" else ""))
   
   result = subprocess.run([
      f"borg info --json {borg_repo}" + (f"::{backup_name} " if backup_name != "" else " ") + "| " +
      "jq .cache.stats.unique_csize | " +
      "awk \'{ printf \"%d\", $1/1024/1024/1024; }\'"
   ], capture_output=True, text=True, shell=True)

   logger.debug(result)
   return int(result.stdout) if not result.returncode else 0

def backup_to_aws(borg_repo: str):
   """Syncs borg repo to AWS.  Returns 0 when successful"""
   s3_bucket = os.environ.get("BORG_S3_BACKUP_BUCKET")
   s3_profile = os.environ.get("BORG_S3_BACKUP_AWS_PROFILE")
   backup_threshold = int(os.environ.get("BACKUP_THRESHOLD", 0))

   if(backup_threshold > 0 ):
      backup_size = int(get_backup_size(borg_repo))
      if(backup_size > backup_threshold):
         msg = f"Backup size {backup_size} GB is larger than threshold {backup_threshold} GB"
         logger.error(msg)
         send_notification(title="Backup Threshold", message=msg)
         return 1;

   logger.info(f"Syncing to s3 bucket {s3_bucket}")
   try:
      result = subprocess.run([
         f"borg with-lock {borg_repo} " +
         f"aws s3 sync {borg_repo} s3://{s3_bucket} --profile={s3_profile} --delete"
      ], check=True, shell=True)
      logger.debug(result)
      return 0
   except subprocess.CalledProcessError as error:
      logger.error(error)
      send_notification(title="Error syncing with AWS", message=error)
      return 1;

def get_aws_bucket_size():
   """Syncs borg repo to AWS.  Returns 0 when successful"""
   s3_bucket = os.environ.get("BORG_S3_BACKUP_BUCKET")
   s3_profile = os.environ.get("BORG_S3_BACKUP_AWS_PROFILE")

   logger.info(f"Getting aws bucket size {s3_bucket}")
   try:
      result = subprocess.run([
         f"aws s3 ls --profile={s3_profile} --summarize --recursive s3://{s3_bucket} | " +
         "tail -1 | " +
         "awk '{ printf \"%.3f GB\", $3/1024/1024/1024; }'"
      ], capture_output=True, check=True, text=True, shell=True)
      logger.debug(result)
      return result.stdout if not result.returncode else ""
   except subprocess.CalledProcessError as error:
      logger.error(error)
      return "";

def cleanup():
   """Cleans up directory"""
   logger.info("Cleanup")

   logger.debug(subprocess.run(["rm -rf openwrt*"], shell=True))
   logger.debug(subprocess.run(["rm -rf pi-hole*"], shell=True))

if __name__ == "__main__":
   main()