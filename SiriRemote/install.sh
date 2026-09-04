#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mac=${1:-70:48:0F:F2:65:99}

case "$mac" in
    ??\:??\:??\:??\:??\:??) ;;
    *) echo "Usage: sudo ./install.sh [AA:BB:CC:DD:EE:FF]" >&2; exit 2 ;;
esac

install -o root -g root -m 0755 "$script_dir/siri_remote_moode.py" /usr/local/sbin/siri_remote_moode.py
install -o root -g root -m 0644 "$script_dir/siri-remote-moode.service" /etc/systemd/system/siri-remote-moode.service

if [ ! -e /etc/default/siri-remote-moode ]; then
    install -o root -g root -m 0644 "$script_dir/siri-remote-moode.env" /etc/default/siri-remote-moode
fi
sed -i "s/^SIRI_REMOTE_MAC=.*/SIRI_REMOTE_MAC=$mac/" /etc/default/siri-remote-moode
# Migrate the old package defaults without overwriting custom commands.
sed -i 's/^MOODE_VOLUME_UP_CMD=set_volume -up 2$/MOODE_VOLUME_UP_CMD=set_volume -up 5/' /etc/default/siri-remote-moode
sed -i 's/^MOODE_VOLUME_DOWN_CMD=set_volume -dn 2$/MOODE_VOLUME_DOWN_CMD=set_volume -dn 5/' /etc/default/siri-remote-moode
grep -q '^MOODE_PREVIOUS_CMD=' /etc/default/siri-remote-moode || printf '%s\n' 'MOODE_PREVIOUS_CMD=previous' >> /etc/default/siri-remote-moode
grep -q '^MOODE_NEXT_CMD=' /etc/default/siri-remote-moode || printf '%s\n' 'MOODE_NEXT_CMD=next' >> /etc/default/siri-remote-moode
grep -q '^SIRI_IGNORE_DURING_RENDERER=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_IGNORE_DURING_RENDERER=yes' >> /etc/default/siri-remote-moode
grep -q '^SIRI_RENDERER_CACHE_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_RENDERER_CACHE_SECONDS=0.25' >> /etc/default/siri-remote-moode
grep -q '^SIRI_RENDERER_DIRECT_DB=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_RENDERER_DIRECT_DB=yes' >> /etc/default/siri-remote-moode
grep -q '^MOODE_DB_PATH=' /etc/default/siri-remote-moode || printf '%s\n' 'MOODE_DB_PATH=/var/local/www/db/moode-sqlite3.db' >> /etc/default/siri-remote-moode
grep -q '^SIRI_TOUCH_X_SPLIT=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_TOUCH_X_SPLIT=3096' >> /etc/default/siri-remote-moode
grep -q '^SIRI_TOUCH_DEAD_ZONE=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_TOUCH_DEAD_ZONE=60' >> /etc/default/siri-remote-moode
grep -q '^SIRI_TOUCH_MAX_AGE_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_TOUCH_MAX_AGE_SECONDS=1.5' >> /etc/default/siri-remote-moode
grep -q '^SIRI_HOME_BUTTON_MASK=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_HOME_BUTTON_MASK=0x01' >> /etc/default/siri-remote-moode
grep -q '^SIRI_HOME_HOLD_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_HOME_HOLD_SECONDS=3' >> /etc/default/siri-remote-moode
grep -q '^SIRI_HOME_COMMAND=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_HOME_COMMAND=/usr/bin/systemctl poweroff' >> /etc/default/siri-remote-moode
# Manual battery display and the Microphone/Siri button are intentionally unused.
# Remove obsolete settings from installations that previously mapped either
# Microphone/Siri or a long Menu press to battery display.
sed -i '/^SIRI_MENU_HOLD_SECONDS=/d; /^SIRI_MIC_BUTTON_MASK=/d; /^SIRI_MIC_HOLD_SECONDS=/d; /^MOODE_SIRI_CMD=/d' /etc/default/siri-remote-moode
# Local display click for Menu/Back. Determine the uid 1000 home directory so
# the package does not assume a specific moOde account name.
display_home=$(getent passwd 1000 | cut -d: -f6)
[ -n "$display_home" ] || display_home=/home/pi
grep -q '^SIRI_MENU_SCREEN_CLICK=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_MENU_SCREEN_CLICK=yes' >> /etc/default/siri-remote-moode
grep -q '^SIRI_X_DISPLAY=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_X_DISPLAY=:0' >> /etc/default/siri-remote-moode
grep -q '^SIRI_XAUTHORITY=' /etc/default/siri-remote-moode || printf 'SIRI_XAUTHORITY=%s/.Xauthority\n' "$display_home" >> /etc/default/siri-remote-moode
grep -q '^SIRI_OVERLAY=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_OVERLAY=yes' >> /etc/default/siri-remote-moode
grep -q '^SIRI_OVERLAY_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_OVERLAY_SECONDS=1' >> /etc/default/siri-remote-moode
sed -i '/^SIRI_OVERLAY_TRACK_POLL_SECONDS=/d' /etc/default/siri-remote-moode
sed -i "s|^SIRI_XAUTHORITY=/home/[^/]*/.Xauthority$|SIRI_XAUTHORITY=$display_home/.Xauthority|" /etc/default/siri-remote-moode
# Remove obsolete fixed metadata coordinates from older package versions. The
# Python daemon now calculates a safe point inside the cover-art link.
sed -i '/^SIRI_MENU_CLICK_X=/d; /^SIRI_MENU_CLICK_Y=/d' /etc/default/siri-remote-moode
sed -i 's/^SIRI_ATT_MTU=104$/SIRI_ATT_MTU=23/' /etc/default/siri-remote-moode
grep -q '^SIRI_ATT_MTU=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_ATT_MTU=23' >> /etc/default/siri-remote-moode
sed -i 's/^SIRI_CONNECT_TIMEOUT_SECONDS=2$/SIRI_CONNECT_TIMEOUT_SECONDS=4/' /etc/default/siri-remote-moode
grep -q '^SIRI_CONNECT_TIMEOUT_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_CONNECT_TIMEOUT_SECONDS=4' >> /etc/default/siri-remote-moode
sed -i 's/^SIRI_KEEPALIVE_SECONDS=.*/SIRI_KEEPALIVE_SECONDS=0/' /etc/default/siri-remote-moode
grep -q '^SIRI_KEEPALIVE_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_KEEPALIVE_SECONDS=0' >> /etc/default/siri-remote-moode
sed -i 's/^SIRI_BATTERY_CHECK_SECONDS=300$/SIRI_BATTERY_CHECK_SECONDS=900/' /etc/default/siri-remote-moode
grep -q '^SIRI_BATTERY_CHECK_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_BATTERY_CHECK_SECONDS=900' >> /etc/default/siri-remote-moode
sed -i 's/^SIRI_BATTERY_LOW_CHECK_SECONDS=.*/SIRI_BATTERY_LOW_CHECK_SECONDS=300/' /etc/default/siri-remote-moode
grep -q '^SIRI_BATTERY_LOW_CHECK_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_BATTERY_LOW_CHECK_SECONDS=300' >> /etc/default/siri-remote-moode
sed -i 's/^SIRI_BATTERY_CRITICAL_CHECK_SECONDS=.*/SIRI_BATTERY_CRITICAL_CHECK_SECONDS=60/' /etc/default/siri-remote-moode
grep -q '^SIRI_BATTERY_CRITICAL_CHECK_SECONDS=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_BATTERY_CRITICAL_CHECK_SECONDS=60' >> /etc/default/siri-remote-moode
grep -q '^SIRI_BATTERY_LOW_PERCENT=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_BATTERY_LOW_PERCENT=10' >> /etc/default/siri-remote-moode
grep -q '^SIRI_BATTERY_CRITICAL_PERCENT=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_BATTERY_CRITICAL_PERCENT=5' >> /etc/default/siri-remote-moode
sed -i 's/^SIRI_SECURITY=.*/SIRI_SECURITY=medium/' /etc/default/siri-remote-moode
sed -i 's/^RECONNECT_MIN=.*/RECONNECT_MIN=0.2/' /etc/default/siri-remote-moode
sed -i 's/^RECONNECT_MAX=.*/RECONNECT_MAX=1/' /etc/default/siri-remote-moode
grep -q '^SIRI_RECLAIM_BUSY=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_RECLAIM_BUSY=yes' >> /etc/default/siri-remote-moode
grep -q '^SIRI_DEBUG=' /etc/default/siri-remote-moode || printf '%s\n' 'SIRI_DEBUG=no' >> /etc/default/siri-remote-moode

systemctl daemon-reload
systemctl enable siri-remote-moode.service
# Explicit restart is required for upgrades: "enable --now" starts a stopped
# service but does not replace an already running daemon process.
systemctl restart siri-remote-moode.service

echo "Installed. Follow logs with: journalctl -u siri-remote-moode -f"
