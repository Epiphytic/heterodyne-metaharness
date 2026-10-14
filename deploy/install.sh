#!/usr/bin/env bash
# The store helper's whole host setup (plan 4c Task 12): the store owner, the store and the helper copy
# (step 3), the helper's units (step 4), ext4 verity under /var/lib (step 5) and the host config check
# (step 6). deploy/store-helper/README.md says which form to run; on a checkout the service user can change,
# only the pinned form, which runs a root-only copy of this file whose sum the approval names:
#
#   sudo bash deploy/install.sh [--dry-run] [--no-verity] [--service-user USER] [--checkout DIR]
#                               [--commit ID --expect-sha256 SUM]
#
# This first stage runs every check before the first change, resolves the commit once, archives it as the
# service user into a root-only stage, checks the archive against --expect-sha256 before extracting it, and
# then runs the stage's copy of itself (`--staged`), which does steps 3 to 6 from the commit's own bytes.
# Re-running is safe: what is in place is reported `ok:`, each change is printed before it runs (`change:`),
# and `--dry-run` prints each as `would:` instead. Exit 0: everything is in place; 3: everything but
# verity, which the host doesn't support; 1: a refusal (`refused: <check>`) or a failure; 2: a usage error.
# `--prefix DIR` (the tests only, with HZ_INSTALL_TEST=1) puts DIR in place of / for every host path, and
# keeps PATH, so that shims stand in for the host's commands.
set -euo pipefail
umask 077

readonly PATHS=(deploy src/heterodyne/harness/resources/store_core.py)
readonly UNITS=(heterodyne-store-helper.socket heterodyne-store-helper@.service)
readonly SOCKET=heterodyne-store-helper.socket
# An instance of the helper template, for `systemctl show`, which takes no template name.
readonly HELPER_INSTANCE=${UNITS[1]/@./@install-check.}
readonly UNIT_DIR=/etc/systemd/system
readonly STAGE_PARENT=/run
readonly HOST_CONFIG=.config/heterodyne/config.toml
readonly VERITY_FEATURE=/sys/fs/ext4/features/verity
readonly PYTHON=/usr/bin/python3
readonly STEPS=(store units verity config)

# Exit 1 with the message when the service user's config sets `[harnesses] fallback`, or can't be read as
# TOML. A missing file is empty, as heterodyne reads it.
readonly FALLBACK_CHECK='
import os, sys, tomllib
path = sys.argv[1]
if not os.path.lexists(path):
    sys.exit(0)
try:
    with open(path, "rb") as f:
        harnesses = tomllib.load(f).get("harnesses", {})
except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
    sys.exit("the host config is not valid TOML")
if not isinstance(harnesses, dict):
    sys.exit("the host config'"'"'s harnesses is not a table")
sys.exit("the host config sets harnesses.fallback" if "fallback" in harnesses else 0)
'

refuse() {
    echo "refused: $*" >&2
    exit 1
}

usage() {
    echo "usage: install.sh [--dry-run] [--no-verity] [--service-user USER] [--checkout DIR]" \
        "[--commit ID --expect-sha256 SUM]" >&2
    exit 2
}

dry_run=0 verity=1 service_user=${SUDO_USER:-} checkout="" commit="" expect="" staged="" archive_sum="" prefix=""
while (($#)); do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --no-verity) verity=0; shift ;;
        --service-user) (($# >= 2)) || usage; service_user=$2; shift 2 ;;
        --checkout) (($# >= 2)) || usage; checkout=$2; shift 2 ;;
        --commit) (($# >= 2)) || usage; commit=$2; shift 2 ;;
        --expect-sha256) (($# >= 2)) || usage; expect=$2; shift 2 ;;
        --staged) (($# >= 2)) || usage; staged=$2; shift 2 ;;
        --archive-sha256) (($# >= 2)) || usage; archive_sum=$2; shift 2 ;;
        --prefix) (($# >= 2)) || usage; prefix=$2; shift 2 ;;
        *) usage ;;
    esac
done
if [[ -n $prefix ]]; then
    [[ ${HZ_INSTALL_TEST:-} == 1 ]] || refuse "--prefix is for the tests only"
    [[ $prefix == /* && -d $prefix ]] || refuse "the prefix is not an absolute directory"
    [[ -n $checkout ]] || usage
else
    PATH=/usr/sbin:/usr/bin:/sbin:/bin
fi
[[ -z $commit || $commit =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || refuse "the commit is not a full commit ID"
[[ -z $expect || $expect =~ ^[0-9a-f]{64}$ ]] || refuse "--expect-sha256 is not a sha256"

host() { printf '%s%s' "$prefix" "$1"; }

# "<uid> <octal mode>" of a path, the one way this script reads ownership.
owner_mode() { stat -c '%u %a' -- "$1"; }

[[ $(id -u) == 0 ]] || refuse "the installer must run as root"

# --- the service user ---

[[ -n $service_user ]] || refuse "no service user: give --service-user or run it through sudo"
[[ $service_user =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || refuse "the service user is not a user name"
passwd=$(getent passwd "$service_user") || refuse "the service user does not exist"
IFS=: read -r _ _ service_uid service_gid _ service_home _ <<<"$passwd"
((service_uid != 0)) || refuse "the service user is root"
group=$(getent group "$service_gid") || refuse "the service user's group does not exist"
service_group=${group%%:*}

# `$@` (leading NAME=VALUE pairs first) as the service user, in an environment holding only their HOME and
# names, so that HETERODYNE_CONFIG_DIR and XDG_CONFIG_HOME are unset.
as_service_user() {
    runuser -u "$service_user" -- env -i HOME="$service_home" USER="$service_user" LOGNAME="$service_user" "$@"
}

# git as the service user on the checkout: the system's git (not one on their PATH), and no replacement
# refs, git config or hooks, so what it reports is the commit's own.
service_git() {
    as_service_user PATH=/usr/bin:/bin GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        git -C "$repo" -c core.hooksPath=/dev/null -c safe.directory="$repo" "$@" 2>/dev/null </dev/null
}

# The service user's login PATH, where `uv` is (its last line: a profile may print).
login_path() {
    local path
    path=$(runuser -l "$service_user" -c 'printf "%s\n" "$PATH"' 2>/dev/null </dev/null) \
        || refuse "the service user's login shell does not run"
    printf '%s' "${path##*$'\n'}"
}

if [[ -z $staged ]]; then
    # === the first stage: every check, the commit, the stage ===

    for tool in git runuser systemctl findmnt cmp sha256sum tar; do
        command -v "$tool" >/dev/null || refuse "a tool is missing: $tool"
    done
    if ((verity)); then
        command -v tune2fs >/dev/null || refuse "a tool is missing: tune2fs"
    fi
    [[ -x $PYTHON ]] && "$PYTHON" -I -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
        || refuse "python3 3.12 or later is missing"

    if [[ -z $checkout ]]; then
        checkout=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    fi
    [[ $checkout == /* ]] || refuse "the checkout is not an absolute path"
    repo=$(host "$checkout")
    [[ -e $repo/.git && -f $repo/deploy/install.sh ]] || refuse "the checkout is not a heterodyne checkout (give --checkout)"

    # A checkout the service user can change (owns or may write any component of the installer's path or
    # of .git) runs only in the pinned form. A modified first stage could skip this, so it is not a trust
    # boundary: it catches the honest mistake of running the unpinned form there.
    changeable=0
    for path in "$checkout/deploy/install.sh" "$checkout/.git"; do
        sofar=""
        IFS=/ read -r -a parts <<<"${path#/}"
        for part in "" "${parts[@]}"; do
            [[ -n $part ]] && sofar="$sofar/$part"
            at=$(host "${sofar:-/}")
            if ! read -r uid _ < <(owner_mode "$at") || ((uid == service_uid)); then
                changeable=1
            elif as_service_user PATH=/usr/bin:/bin test -w "$at"; then
                changeable=1
            fi
        done
    done
    if ((changeable)) && [[ -z $commit || -z $expect ]]; then
        refuse "the service user can change the checkout: run the pinned form (--commit and --expect-sha256)"
    fi

    cfg=$(host "$service_home")/$HOST_CONFIG
    if ! msg=$(as_service_user PATH=/usr/bin:/bin "$PYTHON" -I -c "$FALLBACK_CHECK" "$cfg" 2>&1 </dev/null); then
        [[ $msg == "the host config"* ]] || msg="the host config can't be checked"
        refuse "$msg"
    fi

    service_path=$(login_path)
    as_service_user PATH="$service_path" sh -c 'command -v uv' >/dev/null 2>&1 </dev/null \
        || refuse "uv is not on the service user's login PATH"
    as_service_user PATH="$service_path" uv run --offline --project "$repo" heterodyne --help >/dev/null 2>&1 \
        </dev/null || refuse "the run checkout does not run heterodyne (run uv sync there first)"

    # The commit, resolved once: every later git call names it, never HEAD.
    resolved=$(service_git rev-parse --verify --quiet "${commit:-HEAD}^{commit}") \
        || refuse "the commit can't be resolved"
    [[ $resolved =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || refuse "the commit can't be resolved"
    [[ -z $commit || $resolved == "$commit" ]] || refuse "the commit resolves to another commit"
    echo "commit: $resolved"
    if ! status=$(service_git status --porcelain -- "${PATHS[@]}"); then
        echo "note: the work tree's state can't be read (advice only: the commit's bytes are installed)"
    elif [[ -n $status ]]; then
        echo "note: the work tree differs from the commit (advice only: the commit's bytes are installed)"
    fi

    stage=$(mktemp -d "$(host "$STAGE_PARENT")/heterodyne-install.XXXXXX")
    trap 'rm -rf -- "$stage"' EXIT
    service_git archive --format=tar "$resolved" -- "${PATHS[@]}" >"$stage/archive.tar" \
        || refuse "the commit can't be archived"
    archive_sum=$(sha256sum <"$stage/archive.tar")
    archive_sum=${archive_sum%% *}
    echo "archive sha256: $archive_sum"
    [[ -z $expect || $archive_sum == "$expect" ]] || refuse "the archive does not match --expect-sha256"
    mkdir "$stage/tree"
    tar -x --no-same-owner --no-same-permissions -C "$stage/tree" -f "$stage/archive.tar" \
        || refuse "the archive can't be extracted"
    installer=$stage/tree/deploy/install.sh
    [[ -f $installer && ! -L $installer ]] || refuse "the commit has no installer"

    args=(--staged "$stage" --checkout "$checkout" --commit "$resolved" --archive-sha256 "$archive_sum"
          --service-user "$service_user")
    if ((dry_run)); then args+=(--dry-run); fi
    if ((!verity)); then args+=(--no-verity); fi
    if [[ -n $prefix ]]; then args+=(--prefix "$prefix"); fi
    rc=0
    bash "$installer" "${args[@]}" || rc=$?
    exit "$rc"
fi

# === the staged run: steps 3 to 6, from the stage's bytes only ===

[[ $staged == /* && -d $staged && ! -L $staged ]] || refuse "the stage is not an absolute directory"
read -r uid mode < <(owner_mode "$staged") || refuse "the stage can't be read"
[[ $uid == 0 && $mode == 700 ]] || refuse "the stage is not root's alone"
[[ $(realpath -- "${BASH_SOURCE[0]}") == "$(realpath -- "$staged")/tree/deploy/install.sh" ]] \
    || refuse "the staged installer is not the stage's"
[[ -n $checkout && -n $commit && $archive_sum =~ ^[0-9a-f]{64}$ ]] || usage
repo=$(host "$checkout")
tree=$staged/tree
units=$tree/deploy/store-helper

declare -A result=()
step=""
changed=changed
if ((dry_run)); then changed="would change"; fi

begin() {
    step=$1
    echo "== $1"
}

finish_step() {
    result[$step]=$1
    step=""
}

fail() {
    result[$step]="failed ($1)"
    echo "failed: $step ($1)" >&2
    exit 1
}

summary() {
    local rc=$? s
    if [[ -n $step && -z ${result[$step]:-} ]]; then
        result[$step]="failed (stopped)"
    fi
    echo "summary:"
    for s in "${STEPS[@]}"; do
        echo "  $s: ${result[$s]:-not run}"
    done
    echo "commit: $commit"
    echo "archive sha256: $archive_sum"
    if ((rc == 0)) && [[ ${result[verity]:-} == "skipped ("* && ${result[verity]} != "skipped (--no-verity)" ]]
    then
        rc=3
    fi
    exit "$rc"
}
trap summary EXIT

change() {
    if ((dry_run)); then
        echo "would: $*"
    else
        echo "change: $*"
        "$@"
    fi
}

# Whether `$1` (a script's output) lists a change.
lists_change() {
    local line
    while IFS= read -r line; do
        [[ $line == change:* || $line == would:* ]] && return 0
    done <<<"$1"
    return 1
}

# --- step 3: the store owner, the store and the helper copy ---

begin store
setup=(bash "$units/setup-store.sh" --staged "$tree" --service-user "$service_user")
if [[ -n $prefix ]]; then
    setup+=(--dry-run --prefix "$prefix")    # setup-store.sh's own tests cover its changes
elif ((dry_run)); then
    setup+=(--dry-run)
fi
out=$("${setup[@]}") || { printf '%s\n' "$out"; fail "setup-store.sh refused or failed"; }
printf '%s\n' "$out"
if lists_change "$out"; then finish_step "$changed"; else finish_step ok; fi

# --- step 4: the units, reconciled from systemd's live state, not from what this run changed ---

begin units
unit_changes=0
for u in "${UNITS[@]}"; do
    dest=$(host "$UNIT_DIR/$u")
    if [[ -f $dest && ! -L $dest ]] && cmp -s -- "$units/$u" "$dest" && [[ $(owner_mode "$dest") == "0 644" ]]
    then
        echo "ok: $UNIT_DIR/$u"
    else
        unit_changes=1
        change install -o root -g root -m 0644 "$units/$u" "$dest" || fail "a unit can't be installed"
    fi
done
change systemctl daemon-reload || fail "daemon-reload failed"
if ((!dry_run)); then
    for u in "$SOCKET" "$HELPER_INSTANCE"; do
        need=$(systemctl show -p NeedDaemonReload --value "$u") || fail "systemd's unit state can't be read"
        [[ $need == no ]] || fail "systemd still has an old copy of a unit after daemon-reload"
    done
fi
change systemctl enable "$SOCKET" || fail "the socket can't be enabled"

unit_key() { sed -n "s/^$1=//p" "$units/$SOCKET"; }
listen=$(unit_key ListenStream)
socket_mode=$(unit_key SocketMode)
socket_user=$(unit_key SocketUser)
[[ -n $listen && -n $socket_mode && -n $socket_user ]] || fail "the socket unit has no listen path, mode or user"

# What differs between the socket as it runs and its units (the group is the drop-in's: the service user's
# group), one line each; nothing when it is in place.
live_drift() {
    local state file props
    state=$(systemctl is-active "$SOCKET" 2>/dev/null) || true
    if [[ $state != active ]]; then
        echo "the socket is not active"
        return
    fi
    file=$(stat -c '%F %U %G %a' -- "$(host "$listen")" 2>/dev/null) || file="missing"
    [[ $file == "socket $socket_user $service_group ${socket_mode#0}" ]] \
        || echo "the socket file's type, owner, group or mode"
    props=$(systemctl show -p SocketGroup,SocketMode,Listen "$SOCKET" 2>/dev/null | LC_ALL=C sort) \
        || props="unreadable"
    [[ $props == "Listen=$listen (Stream)"$'\n'"SocketGroup=$service_group"$'\n'"SocketMode=$socket_mode" ]] \
        || echo "the socket's settings in systemd"
}

drift=$(live_drift)
if [[ -z $drift ]]; then
    echo "ok: the socket is listening as its units say"
else
    printf 'drift: %s\n' "$drift"
    unit_changes=1
    if [[ $drift == "the socket is not active" ]]; then
        change systemctl start "$SOCKET" || fail "the socket can't be started"
    else
        change systemctl restart "$SOCKET" || fail "the socket can't be restarted"
    fi
    if ((!dry_run)); then
        drift=$(live_drift)
        [[ -z $drift ]] || fail "the socket does not match its units: ${drift%%$'\n'*}"
    fi
fi
if ((unit_changes)); then finish_step "$changed"; else finish_step ok; fi

# --- step 5: verity on the store's filesystem ---

# The value of the superblock field `$2` in `tune2fs -l`'s output `$1`.
superblock_field() {
    local line
    while IFS= read -r line; do
        if [[ $line == "$2:"* ]]; then
            line=${line#"$2:"}
            echo "${line#"${line%%[![:space:]]*}"}"
            return
        fi
    done <<<"$1"
}

has_verity() { [[ " $1 " == *" verity "* ]]; }

# The host can't have verity: the install carries on, and its exit is 3.
unsupported() {
    echo "skipped: verity ($1)"
    finish_step "skipped ($1)"
}

verity_step() {
    local mnt fstype device text line major="" minor="" sb block features
    mnt=$(findmnt -no FSTYPE,SOURCE --target "$(host /var/lib)") || fail "the store's filesystem can't be found"
    read -r fstype device <<<"$mnt"
    [[ $fstype == ext4 ]] || { unsupported "not ext4"; return; }
    [[ -e $(host "$VERITY_FEATURE") ]] || { unsupported "the kernel's ext4 has no verity"; return; }
    text=$(tune2fs 2>&1) || true    # no arguments: the usage and the version, and exit 1
    while IFS= read -r line; do
        if [[ $line =~ ^tune2fs\ ([0-9]+)\.([0-9]+) ]]; then
            major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]}
        fi
    done <<<"$text"
    [[ -n $major ]] || fail "the e2fsprogs version can't be read"
    ((major > 1 || (major == 1 && minor >= 45))) || { unsupported "e2fsprogs older than 1.45"; return; }
    [[ $device == /dev/* ]] || fail "the store's device can't be read"
    sb=$(tune2fs -l "$device" 2>/dev/null) || fail "the superblock can't be read"
    block=$(superblock_field "$sb" "Block size")
    features=$(superblock_field "$sb" "Filesystem features")
    [[ -n $block && -n $features ]] || fail "the superblock can't be read"
    [[ $block == "$(getconf PAGESIZE)" ]] || { unsupported "the block size is not the page size"; return; }
    if has_verity "$features"; then
        echo "ok: verity is on"
        finish_step ok
        return
    fi
    if ((!dry_run)); then
        echo "warning: the ext4 verity feature can't be cleared once it is set"
    fi
    change tune2fs -O verity "$device" || fail "tune2fs -O verity failed"
    if ((!dry_run)); then
        sb=$(tune2fs -l "$device" 2>/dev/null) || fail "the superblock can't be read"
        has_verity "$(superblock_field "$sb" "Filesystem features")" || fail "verity is not on after tune2fs"
    fi
    finish_step "$changed"
}

begin verity
if ((verity)); then
    verity_step
else
    finish_step "skipped (--no-verity)"
fi

# --- step 6: the host config, as heterodyne reads it for the service user ---

begin config
service_path=$(login_path)
out=$(as_service_user PATH="$service_path" uv run --offline --project "$repo" heterodyne config check 2>&1 \
    </dev/null) || { printf '%s\n' "$out"; fail "config check failed"; }
printf '%s\n' "$out"
problems=0
while IFS= read -r line; do
    if [[ $line == "harnesses problem: "* ]]; then problems=1; fi
done <<<"$out"
if ((problems)) && ((dry_run)); then
    finish_step "problems (the changes above may fix them)"
elif ((problems)); then
    fail "config check reports a store problem"
else
    finish_step ok
fi
