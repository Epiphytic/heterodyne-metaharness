#!/usr/bin/env bash
# The platform's uninstall, as one fail-stop root script. Run it as deploy/store-helper/README.md says (on a
# checkout the service user can change, the pinned form):
#
#   sudo bash deploy/uninstall.sh [--service-user USER] [--restore-adopted]
#
# First the service user's platform units (design btq-v7sgt D6): both are classified before anything is
# acted on, by the install record's release, and any ambiguous state refuses with nothing changed. A unit
# the installer manages is stopped, disabled and removed, wsd first (with --restore-adopted, an adopted
# unit's backup is put back instead, and not started); one the installer didn't write is left in place. The
# releases are kept. Then the store helper's rollback (plan 4c Task 12): it removes nothing while a helper
# may run, and the store goes with `rmdir`, so a store with installs in it is kept, with its owner. The ext4
# verity feature stays set. The first failure stops it, and nothing after that runs. Exit 0: done; 1: a
# refusal (`refused: <check>`) or a failure; 2: a usage error. `--prefix DIR` (the tests only, with
# HZ_INSTALL_TEST=1) puts DIR in place of / for every host path, and keeps PATH, so that shims stand in for
# the host's commands.
set -euo pipefail
umask 077

readonly MANAGED=(heterodyne-wsd.service heterodyne-admind.service)     # the order they are removed in
readonly MARKER_PREFIX="# Rendered by heterodyne's deploy/install.sh for release "
# The install record's release, or nothing; exit 1 when the record can't be read as JSON.
readonly RECORD_RELEASE='
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    release = json.load(f).get("release")
print(release if isinstance(release, str) else "")
'

refuse() {
    echo "refused: $*" >&2
    exit 1
}

fail() {
    echo "failed: $*" >&2
    exit 1
}

usage() {
    echo "usage: uninstall.sh [--service-user USER] [--restore-adopted]" >&2
    exit 2
}

service_user=${SUDO_USER:-} restore=0 prefix=""
while (($#)); do
    case "$1" in
        --service-user) (($# >= 2)) || usage; service_user=$2; shift 2 ;;
        --restore-adopted) restore=1; shift ;;
        --prefix) (($# >= 2)) || usage; prefix=$2; shift 2 ;;
        *) usage ;;
    esac
done
if [[ -n $prefix ]]; then
    [[ ${HZ_INSTALL_TEST:-} == 1 ]] || refuse "--prefix is for the tests only"
    [[ $prefix == /* && -d $prefix ]] || refuse "the prefix is not an absolute directory"
else
    PATH=/usr/sbin:/usr/bin:/sbin:/bin
fi

host() { printf '%s%s' "$prefix" "$1"; }

[[ $(id -u) == 0 ]] || refuse "the uninstall must run as root"

# --- the service user ---

[[ -n $service_user ]] || refuse "no service user: give --service-user or run it through sudo"
[[ $service_user =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || refuse "the service user is not a user name"
passwd=$(getent passwd "$service_user") || refuse "the service user does not exist"
IFS=: read -r _ _ service_uid _ _ service_home _ <<<"$passwd"
((service_uid != 0)) || refuse "the service user is root"

# `$@` (leading NAME=VALUE pairs first) as the service user, in an environment holding only their HOME and
# names.
as_service_user() {
    runuser -u "$service_user" -- env -i HOME="$service_home" USER="$service_user" LOGNAME="$service_user" "$@"
}

# The tools for `systemctl --user`: the system's, except under --prefix, where the shims stand in.
user_tools=/usr/bin:/bin
if [[ -n $prefix ]]; then user_tools=$PATH; fi

# `systemctl --user` on the service user's own manager and bus (design btq-v7sgt D1).
user_systemctl() {
    as_service_user PATH="$user_tools" XDG_RUNTIME_DIR="/run/user/$service_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$service_uid/bus" systemctl --user "$@" </dev/null
}

# --- the platform's user units, decided before anything is acted on (design btq-v7sgt D6) ---

share=$(host "$service_home")/.local/share/heterodyne
releases=$share/releases
install_dir=$share/install
record=$install_dir/record.json
user_units=$(host "$service_home")/.config/systemd/user

# Installed: a record, or a unit file with the installer's marker, read as the service user.
installed=0
if [[ -e $record || -L $record ]]; then installed=1; fi
for u in "${MANAGED[@]}"; do
    first=$(as_service_user PATH=/usr/bin:/bin head -n1 -- "$user_units/$u" 2>/dev/null </dev/null) || first=""
    if [[ $first == "$MARKER_PREFIX"* ]]; then installed=1; fi
done

if ((!installed)); then
    echo "platform: nothing installed"
    for u in "${MANAGED[@]}"; do
        if [[ -e $user_units/$u || -L $user_units/$u ]]; then
            echo "$u: not managed by the installer; left in place"
        fi
    done
else
    shown=$(user_systemctl show "${MANAGED[@]}" -p Id -p LoadState -p ActiveState -p FragmentPath) \
        || refuse "the service user's systemd state can't be read; nothing was changed"
    rel=$(as_service_user PATH=/usr/bin:/bin /usr/bin/python3 -I -c "$RECORD_RELEASE" "$record" \
        2>/dev/null </dev/null) || rel=""
    [[ $rel =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ && -f $releases/$rel/.complete ]] \
        || refuse "the install record names no completed release; nothing was changed"
    # The recorded release's own deploy/platform_state.py, on its own interpreter, as the service user.
    py=("$releases/$rel/.venv/bin/python3" -I "$releases/$rel/deploy/platform_state.py")
    platform() { as_service_user PATH=/usr/bin:/bin "${py[@]}" "$@"; }
    places=(--record "$record" --unit-dir "$user_units" --backup-dir "$install_dir/backup")
    classify=(classify "${places[@]}" --loaded-unit-dir "${user_units#"$prefix"}")
    if ((restore)); then classify+=(--restore-adopted); fi
    if ! rows=$(printf '%s\n' "$shown" | platform "${classify[@]}"); then
        while read -r u kind why; do
            if [[ $kind == ambiguous ]]; then echo "$u: ambiguous: $why" >&2; fi
        done <<<"$rows"
        refuse "a platform unit's ownership is ambiguous; nothing was changed"
    fi
    units=$(platform record-show --record "$record" </dev/null) || fail "the install record can't be read"

    changed=0
    while read -r u kind _; do
        case $kind in
            managed)
                echo "change: stop, disable and remove $u"
                user_systemctl stop "$u" || fail "$u can't be stopped"
                user_systemctl disable "$u" || fail "$u can't be disabled"
                if ((restore)) && command grep -qxF "unit $u written adopted" <<<"$units"; then
                    platform restore --unit "$u" "${places[@]}" </dev/null || fail "$u can't be restored"
                    echo "$u: put back from its backup, not started"
                else
                    as_service_user PATH=/usr/bin:/bin rm -- "$user_units/$u" </dev/null \
                        || fail "$u can't be removed"
                fi
                platform record-drop --unit "$u" --record "$record" </dev/null \
                    || fail "$u can't be dropped from the install record"
                changed=1 ;;
            not-managed) echo "$u: not managed by the installer; left in place" ;;
            absent) ;;
            *) fail "classify gave an unknown kind" ;;
        esac
    done <<<"$rows"
    if ((changed)); then user_systemctl daemon-reload || fail "systemctl --user daemon-reload failed"; fi
    for r in "$releases"/*/; do
        if [[ -d $r ]]; then r=${r%/}; echo "release: ${r##*/} (kept)"; fi
    done
fi

# --- the store helper's rollback (plan 4c Task 12) ---

systemctl disable --now heterodyne-store-helper.socket
systemctl stop 'heterodyne-store-helper@*.service'
left=$(systemctl list-units --plain --no-legend --state=active,activating,deactivating 'heterodyne-store-helper@*')
[[ -z $left ]] || { echo "refused: a helper instance is still running" >&2; exit 1; }
if pgrep -u heterodyne-store >/dev/null; then
    echo "refused: a process still runs as heterodyne-store" >&2
    exit 1
fi
rm "$(host /etc/systemd/system/heterodyne-store-helper.socket)" \
    "$(host /etc/systemd/system/heterodyne-store-helper@.service)"
rm "$(host /etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf)"
rmdir "$(host /etc/systemd/system/heterodyne-store-helper.socket.d)"
systemctl daemon-reload
rm "$(host /usr/local/libexec/heterodyne/store_core.py)" "$(host /etc/heterodyne/store-helper.json)"
rmdir "$(host /usr/local/libexec/heterodyne)" "$(host /etc/heterodyne)"
rmdir "$(host /var/lib/heterodyne/harnesses)" "$(host /var/lib/heterodyne)"
userdel heterodyne-store
