#!/usr/bin/env bash
# The store owner, the store and the helper copy (plan 4c Task 12, step 3). Run as root from a checkout of
# the pinned commit:
#
#   sudo bash deploy/store-helper/setup-store.sh --commit <pinned commit> --service-user <service user>
#
# Every check runs before the first change, and a failed one changes nothing: `refused: <check>` on stderr
# and exit 1. Idempotent: what is already as it should be is reported `ok:`, and each change is printed
# before it runs (`change:`). `--dry-run` runs every check, as the invoking user, and prints each change
# as `would:` without making it. `--prefix DIR` (dry run only, for the offline test) puts DIR in place of
# `/` for every path the script checks or would write; users and groups are always the host's.
#
# Root never runs git or anything else from the checkout: the helper copy is read with
# `git cat-file` as the service user, the checkout's owner.
set -euo pipefail
umask 022

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
readonly UNITS=deploy/store-helper
readonly PYTHON=/usr/bin/python3

refuse() {
    echo "refused: $*" >&2
    exit 1
}

usage() {
    echo "usage: setup-store.sh --commit <full commit ID> --service-user <user> [--repo DIR] [--dry-run]" \
         "[--prefix DIR]" >&2
    exit 2
}

commit="" service_user="" repo="" dry_run=0 prefix=""
while (($#)); do
    case "$1" in
        --commit) (($# >= 2)) || usage; commit=$2; shift 2 ;;
        --service-user) (($# >= 2)) || usage; service_user=$2; shift 2 ;;
        --repo) (($# >= 2)) || usage; repo=$2; shift 2 ;;
        --prefix) (($# >= 2)) || usage; prefix=$2; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        *) usage ;;
    esac
done
[[ -n $commit && -n $service_user ]] || usage
[[ $commit =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || refuse "the commit is not a full commit ID"
[[ $service_user =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || refuse "the service user is not a user name"
if [[ -n $prefix ]]; then
    ((dry_run)) || refuse "--prefix is for a dry run only"
    [[ $prefix == /* && -d $prefix ]] || refuse "the prefix is not an absolute directory"
fi
if ((!dry_run)) && ((EUID != 0)); then
    refuse "a run that changes the host must be root (or use --dry-run)"
fi
if [[ -z $repo ]]; then
    repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
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

# git in the checkout as the service user, with no global or system config (root's HOME is not theirs).
# Its own messages name paths and objects; the refusal that follows names the check.
git_at() {
    as_service_user env -i PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        git -C "$repo" "$@" 2>/dev/null
}

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
store_user_exists=0
if store_passwd=$(getent passwd "$STORE_USER"); then
    IFS=: read -r _ _ store_uid _ _ _ store_shell <<<"$store_passwd"
    ((store_uid != 0 && store_uid != service_uid)) || refuse "the store owner's uid is root's or the service user's"
    [[ $store_shell == /usr/sbin/nologin ]] || refuse "the store owner has a login shell"
    store_user_exists=1
fi
[[ -x $PYTHON ]] || refuse "the helper's python is missing"
"$PYTHON" -I -c 'import sys; sys.exit(sys.version_info < (3, 12))' || refuse "the helper's python is older than 3.12"

# --- the checkout and the helper copy ---

# A dry run by another user can't speak for the service user (what it may write, what it can read), so it
# refuses rather than guess.
if ((EUID != 0)) && [[ $(id -un) != "$service_user" ]]; then
    refuse "a dry run must be run by the service user"
fi

[[ $(git_at rev-parse --verify --quiet "$commit^{commit}") == "$commit" ]] \
    || refuse "the commit is not in the checkout"
[[ $(git_at rev-parse --verify --quiet HEAD) == "$commit" ]] \
    || refuse "the checkout is not at the commit (the units are installed from it)"
[[ -z $(git_at status --porcelain --untracked-files=all -- "$UNITS") ]] \
    || refuse "the checkout's $UNITS differs from the commit"
staged=$(mktemp)
trap 'rm -f "$staged"' EXIT
git_at cat-file blob "$commit:$PACKAGED" >"$staged" \
    || refuse "the commit has no helper"
chmod 0644 "$staged"
"$PYTHON" -I -S -c 'import ast, sys; ast.parse(open(sys.argv[1], "rb").read())' "$staged" \
    || refuse "the commit's helper does not parse"
echo "ok: the helper at the commit is read"

# --- the paths: none writable by the service user, from / down ---

writable_by_service_user() { as_service_user test -w "$1"; }

checked=" "
check_components() {
    local path=$1 sofar="" part
    local -a parts
    IFS=/ read -r -a parts <<<"${path#/}"
    for part in "" "${parts[@]}"; do
        [[ -n $part ]] && sofar="$sofar/$part"
        local at
        at=$(host "${sofar:-/}")
        [[ $checked == *" $at "* ]] && continue
        checked="$checked$at "
        [[ -e $at || -L $at ]] || continue
        [[ ! -L $at ]] || refuse "a path component is a symlink: ${sofar:-/}"
        [[ -d $at ]] || refuse "a path component is not a directory: ${sofar:-/}"
        if writable_by_service_user "$at"; then
            refuse "a path component is writable by the service user: ${sofar:-/}"
        fi
    done
}
for dir in "$STORE" "$LIBEXEC" "$CONFIG_DIR" "$DROPIN_DIR"; do
    check_components "$dir"
done
echo "ok: no path component is writable by the service user"

owner_of() { stat -c '%U' "$1"; }
mode_of() { stat -c '%a' "$1"; }

# A directory that must be `owner`'s: missing is created, another owner refuses, another mode is fixed.
dir_plan=()
plan_dir() {
    local path=$1 owner=$2 at
    at=$(host "$path")
    if [[ ! -e $at ]]; then
        dir_plan+=("$path $owner create")
        return
    fi
    if [[ $owner == "$STORE_USER" && $store_user_exists == 0 ]] || [[ $(owner_of "$at") != "$owner" ]]; then
        refuse "$path exists with another owner"
    fi
    if [[ $(mode_of "$at") != 755 ]]; then
        dir_plan+=("$path $owner chmod")
    else
        echo "ok: $path"
    fi
}
plan_dir "$STATE" root
plan_dir "$STORE" "$STORE_USER"
plan_dir "$LIBEXEC" root
plan_dir "$CONFIG_DIR" root
plan_dir "$DROPIN_DIR" root

# A file that must be root's, 0644, with these bytes: anything else is replaced. One that isn't a regular
# file (a link, a directory) refuses.
file_plan=()
plan_file() {
    local path=$1 source=$2 at
    at=$(host "$path")
    if [[ -e $at || -L $at ]]; then
        [[ -f $at && ! -L $at ]] || refuse "$path is not a regular file"
        if cmp -s "$at" "$source" && [[ $(owner_of "$at") == root && $(mode_of "$at") == 644 ]]; then
            echo "ok: $path"
            return
        fi
    fi
    file_plan+=("$path $source")
}
config=$(mktemp)
dropin=$(mktemp)
trap 'rm -f "$staged" "$config" "$dropin"' EXIT
printf '{"service_uid": %d, "store": "%s"}\n' "$service_uid" "$STORE" >"$config"
printf '[Socket]\nSocketGroup=%s\n' "$service_group" >"$dropin"
chmod 0644 "$config" "$dropin"
plan_file "$HELPER_COPY" "$staged"
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
        "$HELPER_COPY") what="the helper at the commit" ;;
        "$CONFIG") what="{\"service_uid\": $service_uid, \"store\": \"$STORE\"}" ;;
        *) what="[Socket] SocketGroup=$service_group" ;;
    esac
    echo "  ($path: $what)"
    change install -o root -g root -m 0644 "$source" "$(host "$path")"
done
echo "done"
