#!/usr/bin/env bash
# The store owner, the store and the helper copy (plan 4c Task 12, step 3). Root runs this only as step 3
# runs it (README, "The root commands"): the copy in a root-only stage, extracted from an archive of the
# pinned commit whose sums the execution approval names, never the copy in a checkout:
#
#   bash <stage>/tree/deploy/store-helper/setup-store.sh --staged <stage>/tree --service-user <service user>
#
# Every check runs before the first change, and a failed one changes nothing: `refused: <check>` on stderr
# and exit 1. Idempotent: what is already as it should be is reported `ok:`, and each change is printed
# before it runs (`change:`). `--dry-run` runs every check, as the service user, and prints each change as
# `would:` without making it. `--prefix DIR` (dry run only, for the offline test) puts DIR in place of `/`
# for every path the script checks or would write; users and groups are always the host's.
set -euo pipefail
umask 022
if ((EUID == 0)); then
    PATH=/usr/sbin:/usr/bin:/sbin:/bin
fi

readonly STORE_USER=heterodyne-store
readonly STATE=/var/lib/heterodyne
readonly STORE=/var/lib/heterodyne/harnesses
readonly LIBEXEC=/usr/local/libexec/heterodyne
readonly HELPER_COPY=/usr/local/libexec/heterodyne/store_core.py
readonly CONFIG_DIR=/etc/heterodyne
readonly CONFIG=/etc/heterodyne/store-helper.json
readonly DROPIN_DIR=/etc/systemd/system/heterodyne-store-helper.socket.d
readonly DROPIN=/etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf
readonly PACKAGED=src/heterodyne/harness/resources/store_core.py
readonly PYTHON=/usr/bin/python3

refuse() {
    echo "refused: $*" >&2
    exit 1
}

usage() {
    echo "usage: setup-store.sh --staged DIR --service-user <user> [--dry-run] [--prefix DIR]" >&2
    exit 2
}

staged="" service_user="" dry_run=0 prefix=""
while (($#)); do
    case "$1" in
        --staged) (($# >= 2)) || usage; staged=$2; shift 2 ;;
        --service-user) (($# >= 2)) || usage; service_user=$2; shift 2 ;;
        --prefix) (($# >= 2)) || usage; prefix=$2; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        *) usage ;;
    esac
done
[[ -n $staged && -n $service_user ]] || usage
[[ $service_user =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || refuse "the service user is not a user name"
if [[ -n $prefix ]]; then
    ((dry_run)) || refuse "--prefix is for a dry run only"
    [[ $prefix == /* && -d $prefix ]] || refuse "the prefix is not an absolute directory"
fi
if ((!dry_run)) && ((EUID != 0)); then
    refuse "a run that changes the host must be root (or use --dry-run)"
fi

host() { printf '%s%s' "$prefix" "$1"; }

# `$@` as the service user: through runuser as root, directly in a dry run by that user.
as_service_user() {
    if ((EUID == 0)); then
        runuser -u "$service_user" -- "$@"
    else
        "$@"
    fi
}

# "<uid> <octal mode>" of a path, the one way this script reads ownership.
owner_mode() { stat -c '%u %a' -- "$1"; }

change() {
    if ((dry_run)); then
        echo "would: $*"
    else
        echo "change: $*"
        "$@"
    fi
}

# --- the users ---

passwd=$(getent passwd "$service_user") || refuse "the service user does not exist"
IFS=: read -r _ _ service_uid service_gid _ <<<"$passwd"
((service_uid != 0)) || refuse "the service user is root"
[[ $service_user != "$STORE_USER" ]] || refuse "the service user is the store owner"
service_group=$(getent group "$service_gid" | cut -d: -f1) || refuse "the service user's group does not exist"
[[ -n $service_group ]] || refuse "the service user's group does not exist"
store_user_exists=0 store_uid=""
if store_passwd=$(getent passwd "$STORE_USER"); then
    IFS=: read -r _ _ store_uid _ _ _ store_shell <<<"$store_passwd"
    ((store_uid != 0 && store_uid != service_uid)) || refuse "the store owner's uid is root's or the service user's"
    [[ $store_shell == /usr/sbin/nologin ]] || refuse "the store owner has a login shell"
    store_user_exists=1
fi
[[ -x $PYTHON ]] || refuse "the helper's python is missing"
"$PYTHON" -I -c 'import sys; sys.exit(sys.version_info < (3, 12))' || refuse "the helper's python is older than 3.12"

# A dry run by another user can't speak for the service user (what it may write), so it refuses rather than
# guess.
if ((EUID != 0)) && [[ $(id -un) != "$service_user" ]]; then
    refuse "a dry run must be run by the service user"
fi

# --- the paths: none the service user owns or may write, from / down ---

checked=" "
check_components() {
    local path=$1 sofar="" part at uid
    local -a parts
    IFS=/ read -r -a parts <<<"${path#/}"
    for part in "" "${parts[@]}"; do
        [[ -n $part ]] && sofar="$sofar/$part"
        at=$(host "${sofar:-/}")
        [[ $checked == *" $at "* ]] && continue
        checked="$checked$at "
        [[ -e $at || -L $at ]] || continue
        [[ ! -L $at ]] || refuse "a path component is a symlink: ${sofar:-/}"
        [[ -d $at ]] || refuse "a path component is not a directory: ${sofar:-/}"
        read -r uid _ < <(owner_mode "$at") || refuse "a path component can't be read: ${sofar:-/}"
        # Its owner may chmod it and swap what is under it, writable now or not.
        ((uid != service_uid)) || refuse "a path component is owned by the service user: ${sofar:-/}"
        if as_service_user test -w "$at"; then
            refuse "a path component is writable by the service user: ${sofar:-/}"
        fi
    done
}

# --- the staged tree and the helper copy ---

[[ $staged == /* && -d $staged && ! -L $staged ]] || refuse "the staged tree is not an absolute directory"
if ((EUID == 0)); then
    # Step 3's stage: root's own, 0700, under no path the service user controls.
    read -r uid mode < <(owner_mode "$staged") || refuse "the staged tree can't be read"
    [[ $uid == 0 && $mode == 700 ]] || refuse "the staged tree is not root's alone"
    check_components "$staged"
fi
helper=$staged/$PACKAGED
[[ -f $helper && ! -L $helper ]] || refuse "the staged tree has no helper"
"$PYTHON" -I -S -c 'import ast, sys; ast.parse(open(sys.argv[1], "rb").read())' "$helper" 2>/dev/null \
    || refuse "the staged helper does not parse"
echo "ok: the staged helper is read"

for dir in "$STORE" "$LIBEXEC" "$CONFIG_DIR" "$DROPIN_DIR"; do
    check_components "$dir"
done
echo "ok: no path component is the service user's or writable by them"

# A directory that must be `owner`'s: missing is created, another owner refuses, another mode is fixed.
dir_plan=()
plan_dir() {
    local path=$1 owner=$2 owner_uid=$3 at uid mode
    at=$(host "$path")
    if [[ ! -e $at ]]; then
        dir_plan+=("$path $owner create")
        return
    fi
    read -r uid mode < <(owner_mode "$at") || refuse "$path can't be read"
    if [[ -z $owner_uid || $uid != "$owner_uid" ]]; then
        refuse "$path exists with another owner"
    fi
    if [[ $mode != 755 ]]; then
        dir_plan+=("$path $owner chmod")
    else
        echo "ok: $path"
    fi
}
plan_dir "$STATE" root 0
plan_dir "$STORE" "$STORE_USER" "$store_uid"
plan_dir "$LIBEXEC" root 0
plan_dir "$CONFIG_DIR" root 0
plan_dir "$DROPIN_DIR" root 0

# A file that must be root's, 0644, with these bytes: anything else is replaced. One that isn't a regular
# file (a link, a directory) refuses.
file_plan=()
plan_file() {
    local path=$1 source=$2 at uid mode
    at=$(host "$path")
    if [[ -e $at || -L $at ]]; then
        [[ -f $at && ! -L $at ]] || refuse "$path is not a regular file"
        read -r uid mode < <(owner_mode "$at") || refuse "$path can't be read"
        if cmp -s "$at" "$source" && [[ $uid == 0 && $mode == 644 ]]; then
            echo "ok: $path"
            return
        fi
    fi
    file_plan+=("$path $source")
}
config=$(mktemp)
dropin=$(mktemp)
trap 'rm -f "$config" "$dropin"' EXIT
printf '{"service_uid": %d, "store": "%s"}\n' "$service_uid" "$STORE" >"$config"
printf '[Socket]\nSocketGroup=%s\n' "$service_group" >"$dropin"
chmod 0644 "$config" "$dropin"
plan_file "$HELPER_COPY" "$helper"
plan_file "$CONFIG" "$config"
plan_file "$DROPIN" "$dropin"

# --- the changes, every check having passed ---

if ((store_user_exists)); then
    echo "ok: the user $STORE_USER"
else
    change useradd --system --no-create-home --shell /usr/sbin/nologin --user-group "$STORE_USER"
fi
for entry in "${dir_plan[@]}"; do
    read -r path owner what <<<"$entry"
    if [[ $what == create ]]; then
        change install -d -o "$owner" -g "$owner" -m 0755 "$(host "$path")"
    else
        change chmod 0755 "$(host "$path")"
    fi
done
for entry in "${file_plan[@]}"; do
    read -r path source <<<"$entry"
    case "$path" in
        "$HELPER_COPY") what="the staged helper" ;;
        "$CONFIG") what="{\"service_uid\": $service_uid, \"store\": \"$STORE\"}" ;;
        *) what="[Socket] SocketGroup=$service_group" ;;
    esac
    echo "  ($path: $what)"
    change install -o root -g root -m 0644 "$source" "$(host "$path")"
done
echo "done"
