#!/usr/bin/env python3
import os
import subprocess
import logging
from datetime import datetime
from dotenv import load_dotenv

DOCKER_BACKUP_PREFIX = "docker-backup"
ROUTER_BACKUP_PREFIX = "router-backup"
PIHOLE_BACKUP_PREFIX = "pihole-backup"
ETC_BACKUP_PREFIX = "etc-backup"
ALL_PREFIXES = (DOCKER_BACKUP_PREFIX, 
                ROUTER_BACKUP_PREFIX, 
                PIHOLE_BACKUP_PREFIX, 
                ETC_BACKUP_PREFIX)
CURRENT_TIME = datetime.now().strftime("%Y-%m-%dT%H.%M")
PIHOLE_BACKUP_DIR="pi-hole-backup"
ROUTER_BACKUP_DIR="openwrt-backup"
ROUTER_TAR_NAME="openwrt.tar.gz"
DEBUG=False

ENV_VARS = (
   "DOCKER_DIR",
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

def borg_create(borg_repo: str, 
           backup_name: str, 
           backup_dir: str, 
           excludes_file: str, 
           dry_run = False):
   """Creates a borg archive"""
   print(f"Backing up {backup_dir} with borg to {borg_repo}::{backup_name}")
   cmd = [
      "borg create " +
      ("--dry-run " if dry_run else "") +
      f"{borg_repo}::{backup_name} " +
      f"{backup_dir} " +
      ("--stats " if not dry_run else "-v ") +
      f"--exclude-from {excludes_file} " +
      "--compression zlib,6"
   ]
   result = subprocess.run(cmd, check=not dry_run, shell=True)
   print(result)
   return result

def ssh(host: str, command: str):
   """Runs a ssh command"""
   print(f"Initiating ssh command: {host} {command}")
   private_key_path = os.environ.get("SSH_PRIVATE_KEY_PATH")

   return subprocess.run([f"ssh -i {private_key_path} {host} {command}"], check=not DEBUG, shell=True)

def scp(host: str, remote_path: str, local_path: str):
   """Runs a scp command"""
   private_key_path = os.environ.get("SSH_PRIVATE_KEY_PATH")
   print(f"Initiating scp command: {host}:{remote_path} {local_path}")

   return subprocess.run([f"scp -i {private_key_path} {host}:{remote_path} {local_path}"], check=not DEBUG, shell=True)

def get_router_backup():
   """Retrieves /etc config files from router.  Returns 0 when successful"""
   print("Retrieving Openwrt.lan backup")

   router_host = os.environ.get("ROUTER_HOST")
   user_and_host = f"root@{router_host}"

   try:
      result = ssh(user_and_host, f"tar -cvzf {ROUTER_TAR_NAME} /etc")
      result = scp(user_and_host, ROUTER_TAR_NAME, ".")
      result = ssh(user_and_host, f"rm -rf {ROUTER_TAR_NAME}")
   except subprocess.CalledProcessError:
      print(result)
      send_notification(title="Error retrieving Openwrt.lan backup", message=result)
      return 1;

   os.mkdir(ROUTER_BACKUP_DIR)

   result = subprocess.run(["tar", "xzvf", ROUTER_TAR_NAME, "-C", ROUTER_BACKUP_DIR])
   print(result)
   return result.returncode
   


def get_pihole_backup():
   """Retrieves /etc config files from pihole.  Returns 0 when successful"""
   print("Retrieving Pi-Hole backup")
   
   pihole_host = os.environ.get("PIHOLE_HOST")
   user_and_host = f"pi@{pihole_host}"

   try:
      result = ssh(user_and_host, "pihole -a -t")
      result = scp(user_and_host, "pi-hole*", ".")
      result = ssh(user_and_host, f"rm -rf pi-hole*")
   except subprocess.CalledProcessError:
      print(result)
      send_notification(title="Error retrieving Pi-Hole backup", message=result)
      return 1

   os.mkdir(PIHOLE_BACKUP_DIR)

   result = subprocess.run([f"tar xzvf pi-hole-raspberrypi-teleporter* -C {PIHOLE_BACKUP_DIR}"], shell=True)
   print(result)
   return result.returncode


def backup_to_repo(borg_repo: str, create_router_archive: bool, create_pihole_archive: bool):
   """Performs the backups to repo"""
   print(f"Backing up to repo {borg_repo}")
   excludes = "excludes.txt"

   # Docker
   borg_create(
      borg_repo = borg_repo,
      backup_name = f"{DOCKER_BACKUP_PREFIX}-{CURRENT_TIME}",
      backup_dir = os.environ.get("DOCKER_DIR"),
      excludes_file = excludes,
      dry_run = DEBUG)

   # Router
   if create_router_archive:
      borg_create(
         borg_repo = borg_repo,
         backup_name = f"{ROUTER_BACKUP_PREFIX}-{CURRENT_TIME}",
         backup_dir = ROUTER_BACKUP_DIR,
         excludes_file = excludes,
         dry_run = DEBUG)

   # Pihole
   if create_pihole_archive:
      borg_create(
         borg_repo = borg_repo,
         backup_name = f"{PIHOLE_BACKUP_PREFIX}-{CURRENT_TIME}",
         backup_dir = PIHOLE_BACKUP_DIR,
         excludes_file = excludes,
         dry_run = DEBUG)

   # /etc
   borg_create(
      borg_repo = borg_repo,
      backup_name = f"{ETC_BACKUP_PREFIX}-{CURRENT_TIME}",
      backup_dir = "/etc",
      excludes_file = excludes,
      dry_run = DEBUG)

def stop_docker():
   """Stops all running docker containers"""
   print("Stopping docker containers")
   docker_ps_process = subprocess.run(["docker","ps","-a","-q"], capture_output=True, text=True)
   docker_ps_split = filter(lambda container: container != "", docker_ps_process.stdout.split("\n"))

   args=[
      "docker",
      "stop",
      *docker_ps_split,
   ]
   print(args)
   if not DEBUG:
      result = subprocess.run(args)
      print(result)

def start_docker():
   """Starts all docker containers"""
   print("Starting docker containers")
   docker_ps_process = subprocess.run(["docker","ps","-a","-q"], capture_output=True, text=True)
   docker_ps_split = filter(lambda container: container != "", docker_ps_process.stdout.split("\n"))

   args=[
      "docker",
      "start",
      *docker_ps_split,
   ]
   print(args)
   if not DEBUG:
      result = subprocess.run(args)
      print(result)

def send_notification(title: str, message: str, priority = 0):
   """Sends notification to pushover"""
   print("Sending notification to pushover")
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
   print(result)

def prune_repo(borg_repo: str):
   """Prune old archives from borg repo"""
   print(f"Pruning old backups from repo {borg_repo}")
   for prefix in ALL_PREFIXES:
      subprocess.run([
         f"borg prune -v -P {prefix} --list --keep-daily=1 --keep-weekly=1 --keep-monthly=1 {borg_repo}"
      ], shell=True)

def backup_to_aws(borg_repo: str):
   """Syncs borg repo to AWS"""
   s3_bucket = os.environ.get("BORG_S3_BACKUP_BUCKET")
   s3_profile = os.environ.get("BORG_S3_BACKUP_AWS_PROFILE")

   print(f"Syncing to s3 bucket {s3_bucket}")
   try:
      result = subprocess.run([
         f"borg with-lock {borg_repo} " +
         f"aws s3 sync {borg_repo} s3://{s3_bucket} --profile={s3_profile} --delete"
      ], shell=True)
      print(result)
   except subprocess.CalledProcessError:
      send_notification(title="Error syncing with AWS", message=result)

def main():
   """Backup all the goodies"""
   print(f"Starting backup {CURRENT_TIME}")
   DEBUG=True

   # Get environment variables from .env 
   load_dotenv()

   # Verify all required variables are set
   for env_var in ENV_VARS:
      if not os.environ.get(env_var):
         print(f"Please provide {env_var} in .env file")
         exit(1)

   # Prepare backup directories
   create_router_archive = not get_router_backup()
   create_pihole_archive = not get_pihole_backup()

   stop_docker()
   
   borg_repo = os.environ.get("BORG_REPO")
   backup_to_repo(borg_repo = borg_repo,
                  create_router_archive = create_router_archive,
                  create_pihole_archive = create_pihole_archive)
   
   prune_repo(borg_repo = borg_repo)
   # backup_to_aws(borg_repo)
   
   # Change passphrase for next repo
   os.environ['BORG_PASSPHRASE'] = os.environ.get("BORG_EXTDRIVE_PASSPHRASE")

   borg_ext_repo = os.environ.get("BORG_EXTDRIVE_REPO")
   # backup_to_repo(borg_repo = borg_ext_repo,
   #                create_router_archive = create_router_archive,
   #                create_pihole_archive = create_pihole_archive)
   prune_repo(borg_repo = borg_ext_repo)

   start_docker()

   # send_notification()


if __name__ == "__main__":
   main()