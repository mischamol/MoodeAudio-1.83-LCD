#!/usr/bin/python3
#copy to /var/www/util/aplmeta.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2014 The moOde audio player project / Tim Curtis
#

#
# Caller: After starting shairport-sync
# cat /tmp/shairport-sync-metadata | shairport-sync-metadata-reader | /var/www/daemon/aplmeta.py
#
# DEBUG
# /var/www/daemon/aplmeta.py 1 (2 for more detail)
#
# Shairport-sync.conf
# metadata = {
# enabled = "yes";
# include_cover_art = "yes";
# cover_art_cache_directory = "/var/local/www/imagesw/airplay-covers";
# pipe_name = "/tmp/shairport-sync-metadata";
# diagnostics = {
# retain_cover_art = "no"; // artwork is deleted when its corresponding track has been played. Set this to "yes" to retain all artwork permanently. Warning -- your directory might fill up.
#

import sys
import subprocess
import re
import os
import glob
import time
from datetime import datetime

#
# Globals
#

PGM_VERSION = '1.0.1'
DEBUG = 0
COVERS_LOCAL_ROOT = '/var/local/www/imagesw/airplay-covers/'
COVERS_WEB_ROOT = 'imagesw/airplay-covers/'
APLMETA_FILE = '/var/local/www/aplmeta.txt'

artist = None
title = None
album = None
duration = '0'

# --- track current Persistent ID ---
current_pid = None
# -----------------------------------------------------

#
# Functions
#

# Debug logger
def debug_msg(msg, line_ending = '\n'):
	global DEBUG
	if DEBUG > 0:
		time_stamp = datetime.now().strftime("%H:%M:%S")
		print(time_stamp + ' DEBUG: ' + msg, end = line_ending);

# Get specified metadata key,value pairs
def get_metadata(line):
	match = re.match('^(Title|Artist|Album Name): \"(.*?)\"\.$', line)
	if match:
		return match.group(1), match.group(2)
	else:
		match = re.match('^(Track length): (.*?)\.$', line)
		if match:
			return match.group(1), match.group(2).split(' ')[0]
		else:
			return None, None

# Update global vars
def update_globals(key, val):
	global artist, album, title, duration
	if key == 'Title':
		title = val
	elif key == 'Artist':
		artist = val
	elif key == 'Album Name':
		album = val
	elif key == 'Track length':
		duration = val

# --- Persistent ID handling ---

# Parse "Persistent ID: 0x...."
def get_persistent_id(line):
	match = re.match(r'^Persistent ID:\s*(0x[0-9a-fA-F]+)\.\s*$', line, flags=re.IGNORECASE)
	return match.group(1) if match else None

# Read PID from aplmeta.txt if present (expects a line "PID=0x...")
def read_pid_from_aplmeta():
	try:
		if not os.path.exists(APLMETA_FILE):
			return None
		with open(APLMETA_FILE, 'r', encoding='utf-8') as f:
			for ln in f:
				if ln.startswith('PID='):
					return ln.split('=', 1)[1].strip()
	except Exception as e:
		debug_msg(f"Could not read PID from {APLMETA_FILE}: {e}")
	return None

# Replace metadata line with a default entry + write PID line (keeps format stable)
def write_default_metadata_with_pid(new_pid):
	try:
		default_cover_url = COVERS_WEB_ROOT + 'notfound.jpg'
		default_metadata = '' + '~~~' + '' + 'airplay source' + '' + '~~~' + '0' + '~~~' + default_cover_url + '~~~' + 'ALAC/AAC'
		with open(APLMETA_FILE, 'w', encoding='utf-8') as f:
			f.write(default_metadata + "\n")
			f.write(f"PID={new_pid}\n")
	except Exception as e:
		debug_msg(f"Could not write default metadata to {APLMETA_FILE}: {e}")

# ----------------------------------------------------

#
# Main
#

# Get debug level
if len(sys.argv) > 1:
	if sys.argv[1] == '--version':
		print('aplmeta.py version ' + PGM_VERSION)
		exit()
	else:
		DEBUG = int(sys.argv[1])

# --- initialize current_pid from file if available ---
current_pid = read_pid_from_aplmeta()
if current_pid:
	debug_msg('--> PID loaded from aplmeta.txt: ' + current_pid)
# ----------------------------------------------------------------------

# Forever loop
try:
	while True:
		line = sys.stdin.readline()
		if DEBUG > 1:
			debug_msg(line, '')

		# --- handle Persistent ID detection and change ---
		pid = get_persistent_id(line)
		if pid:
			debug_msg('--> persistent_id detected: ' + pid)
			if current_pid and pid != current_pid:
				# PID changed: replace metadata line with a default entry, update PID line
				debug_msg('--> persistent_id changed: ' + current_pid + ' -> ' + pid)
				write_default_metadata_with_pid(pid)
				# Reset globals so new track metadata will be rebuilt
				artist = None
				title = None
				album = None
				duration = '0'
			current_pid = pid
		# ---------------------------------------------------------------

		# Update specified globals
		key, val = get_metadata(line)
		if key and val:
			debug_msg('--> key|val=' + key + '|' + val)
			update_globals(key, val)

		# When all globals are set, send metadata to front-end for display
		if artist and title and album:
			# Get cover file:
			# - Only one file will exist because retain_cover_art = "no"
			# - We need a delay to allow shairport-sync time to write the file
			debug_msg('--> Get cover file...')
			time.sleep(1)
			cover_path = glob.glob(COVERS_LOCAL_ROOT + '*')

			if not cover_path:
				cover_file = 'notfound.jpg'
				debug_msg('--> Cover file not found')
			else:
				# Construct cover URL
				cover_file = os.path.basename(cover_path[0])
				cover_url = COVERS_WEB_ROOT + cover_file
				debug_msg('--> Cover ' + cover_file)

				# Write metadata file
				debug_msg('--> Write metadata file')
				format = 'ALAC/AAC'
				metadata = title + '~~~' + artist + '~~~' + album + '~~~' + duration + '~~~' + cover_url + '~~~' + format
				file = open(APLMETA_FILE, 'w')
				file.write(metadata + "\n")
				# also write PID (if known) as second line
				if current_pid:
					file.write(f"PID={current_pid}\n")
				file.close()

				# Send FE command
				debug_msg('--> Send FE command')
				subprocess.call(['/var/www/util/send-fecmd.php','update_aplmeta,' + metadata])

				# Reset globals
				debug_msg('--> Reset globals')
				artist = None
				title = None
				album = None
				duration = '0'

except KeyboardInterrupt:
	sys.stdout.flush()
	print("\n")
