#!/usr/bin/env bash
# The store helper's rollback (plan 4c Task 12): the README's rollback, as one fail-stop root script. Run it as
# deploy/store-helper/README.md says (on a checkout the service user can change, the pinned form). The first
# failure stops it, and nothing after that runs: it removes nothing while a helper may run, and the store goes
# with `rmdir`, so a store with installs in it is kept, with its owner. The ext4 verity feature stays set.
set -euo pipefail
systemctl disable --now heterodyne-store-helper.socket
systemctl stop 'heterodyne-store-helper@*.service'
left=$(systemctl list-units --plain --no-legend --state=active,activating,deactivating 'heterodyne-store-helper@*')
[[ -z $left ]] || { echo "refused: a helper instance is still running" >&2; exit 1; }
if pgrep -u heterodyne-store >/dev/null; then
    echo "refused: a process still runs as heterodyne-store" >&2
    exit 1
fi
rm /etc/systemd/system/heterodyne-store-helper.socket /etc/systemd/system/heterodyne-store-helper@.service
rm /etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf
rmdir /etc/systemd/system/heterodyne-store-helper.socket.d
systemctl daemon-reload
rm /usr/local/libexec/heterodyne/store_core.py /etc/heterodyne/store-helper.json
rmdir /usr/local/libexec/heterodyne /etc/heterodyne
rmdir /var/lib/heterodyne/harnesses /var/lib/heterodyne
userdel heterodyne-store
