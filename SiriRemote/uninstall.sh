#!/bin/sh
set -eu

usage() {
    echo "Usage: sudo ./uninstall.sh [--keep-config]" >&2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root: sudo ./uninstall.sh" >&2
    exit 1
fi

keep_config=no
case "${1:-}" in
    "") ;;
    --keep-config) keep_config=yes ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
esac

# All removal targets are fixed paths created by install.sh. Pairing data and
# any separate BlueZ configuration are deliberately left untouched.
systemctl disable --now siri-remote-moode.service 2>/dev/null || true
rm -f -- /etc/systemd/system/siri-remote-moode.service
rm -f -- /usr/local/sbin/siri_remote_moode.py

if [ "$keep_config" = no ]; then
    rm -f -- /etc/default/siri-remote-moode
fi

systemctl daemon-reload
systemctl reset-failed siri-remote-moode.service 2>/dev/null || true

if [ "$keep_config" = yes ]; then
    echo "Uninstalled. Configuration retained at /etc/default/siri-remote-moode"
else
    echo "Uninstalled. Bluetooth pairing and BlueZ configuration were not changed."
fi
