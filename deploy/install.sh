#!/usr/bin/env bash
# The whole install, as root: the store helper's host setup (plan 4c Task 12) and the platform itself (design
# btq-v7sgt). The steps, in order, one summary line each: `store` (the store owner, the store and the helper
# copy), `units` (the helper's units), `release` (the commit's tree and its venv, built offline from
# uv.lock as the service user), `config` (the host config check, run from the release), `verity` (ext4
# verity under /var/lib, after every check, since it can't be undone) and `services` (the admind and wsd
# user units, rendered from the release, reconciled on the loaded unit and the running process, on the
# service user's own manager). deploy/store-helper/README.md says which form to run; on a checkout the
# service user can change, only the pinned form, which runs a root-only copy of this file whose sum the
# approval names:
#
#   sudo bash deploy/install.sh [--dry-run] [--no-verity] [--fetch] [--adopt] [--no-wsd]
#                               [--service-user USER] [--checkout DIR] [--commit ID --expect-sha256 SUM]
#
# --fetch lets uv use the network for the release (still locked and hash-checked); --adopt replaces a
# hand-made admind or wsd unit, keeping a backup; --no-wsd writes no wsd unit, and stops and disables a
# managed one. Lingering for the service user is a prerequisite, never changed here.
#
# This first stage runs every check before the first change, resolves the commit once, archives it as the
# service user into a root-only stage, checks the archive against --expect-sha256 before extracting it, and
# then runs the stage's copy of itself (`--staged`), which does the steps from the commit's own bytes.
# Re-running is safe: what is in place is reported `ok:`, each change is printed before it runs (`change:`),
# and `--dry-run` prints each as `would:` instead. Exit 0: everything is in place; 3: everything but a step
# skipped for a reason only the host or the operator can resolve (verity, which the host doesn't support, or
# admind, until `admind init` has run); 1: a refusal (`refused: <check>`) or a failure; 2: a usage error.
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
readonly STEPS=(store units release config verity services)
# `uv sync` for a release: from uv.lock exactly, without the dev group, on the system python3 (design
# btq-v7sgt D2). uv_sync adds --offline after --locked unless --fetch.
readonly UV_SYNC=(uv sync --locked --no-dev --python /usr/bin/python3)

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
    echo "usage: install.sh [--dry-run] [--no-verity] [--fetch] [--adopt] [--no-wsd] [--service-user USER]" \
        "[--checkout DIR] [--commit ID --expect-sha256 SUM]" >&2
    exit 2
}

dry_run=0 verity=1 fetch=0 service_user=${SUDO_USER:-} checkout="" commit="" expect="" staged="" archive_sum=""
prefix="" probe="" adopt=0 no_wsd=0
while (($#)); do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --no-verity) verity=0; shift ;;
        --fetch) fetch=1; shift ;;
        --adopt) adopt=1; shift ;;
        --no-wsd) no_wsd=1; shift ;;
        --service-user) (($# >= 2)) || usage; service_user=$2; shift 2 ;;
        --checkout) (($# >= 2)) || usage; checkout=$2; shift 2 ;;
        --commit) (($# >= 2)) || usage; commit=$2; shift 2 ;;
        --expect-sha256) (($# >= 2)) || usage; expect=$2; shift 2 ;;
        --staged) (($# >= 2)) || usage; staged=$2; shift 2 ;;
        --archive-sha256) (($# >= 2)) || usage; archive_sum=$2; shift 2 ;;
        --prefix) (($# >= 2)) || usage; prefix=$2; shift 2 ;;
        --verity-probe) (($# >= 2)) || usage; probe=$2; shift 2 ;;
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

# The tools for the new user-level calls: the system's, except under --prefix, where the shims stand in.
user_tools=/usr/bin:/bin
if [[ -n $prefix ]]; then user_tools=$PATH; fi

# `systemctl --user` on the service user's own manager and bus. Never -M or machinectl, and never a call
# that changes the manager itself: no daemon-reexec, and no set-, unset- or import-environment (design
# btq-v7sgt D1).
user_systemctl() {
    as_service_user PATH="$user_tools" XDG_RUNTIME_DIR="/run/user/$service_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$service_uid/bus" systemctl --user "$@" </dev/null
}

# git as the service user on the checkout: the system's git (not one on their PATH), and no replacement
# refs, git config or hooks, so what it reports is the commit's own.
service_git() {
    as_service_user PATH=/usr/bin:/bin GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        git -C "$repo" -c core.hooksPath=/dev/null -c safe.directory="$repo" "$@" 2>/dev/null </dev/null
}

# --- the platform's user state (design btq-v7sgt D2 to D6) ---

releases=$(host "$service_home")/.local/share/heterodyne/releases
install_dir=$(host "$service_home")/.local/share/heterodyne/install
record=$install_dir/record.json
backups=$install_dir/backup
user_units=$(host "$service_home")/.config/systemd/user
# What the units get, and what every checked command runs with: host paths, never under the prefix.
config_dir=$service_home/.config/heterodyne
state_dir=$service_home/.local/state/heterodyne
readonly MANAGED=(heterodyne-admind.service heterodyne-wsd.service)
wanted=(heterodyne-admind.service)
((no_wsd)) || wanted+=(heterodyne-wsd.service)
plan_flags=()
((!adopt)) || plan_flags+=(--adopt)

# The commit's deploy/platform_state.py (PLATFORM_PY, set from the stage) as the service user: its bytes
# are the approved archive's, passed as `-c`, since the stage is root's alone.
platform() { as_service_user PATH="$user_tools" "$PYTHON" -I -c "$PLATFORM_PY" "$@"; }

# The release the install record names, or nothing.
recorded_release() {
    local line
    line=$(platform record-show --record "$record" </dev/null | sed -n 's/^release //p') \
        || refuse "the install record can't be read"
    [[ $line != - ]] && printf '%s' "$line"
    return 0
}

# A wsd that holds the units' state directory's instance lock while its unit is not active runs outside
# the unit, and the install would start a second one on the same journal (design btq-v7sgt D4). The
# lock is wsd's own, read by the approved archive's platform_state.py, so no release is needed: every
# branch looks, and evidence that can't be read refuses.
wsd_outside_check() {
    local state lock
    ((!no_wsd)) || return 0
    state=$(user_systemctl is-active heterodyne-wsd.service 2>/dev/null) || true
    [[ $state != active ]] || return 0
    lock=$(platform wsd-lock --lock "$(host "$state_dir")/wsd/wsd.lock" </dev/null 2>/dev/null) \
        || refuse "whether a wsd runs outside its unit can't be told"
    case $lock in
        held) refuse "a wsd is running outside its unit: stop it first" ;;
        free|absent) ;;
        *) refuse "whether a wsd runs outside its unit can't be told" ;;
    esac
}

# `platform dir` before any change: its refusal is the install's, as one line.
dir_check() {
    local what=$1 err
    shift
    if err=$(platform dir --what "$what" "$@" 2>&1 1>/dev/null </dev/null); then return 0; fi
    err=${err%%$'\n'*}
    [[ $err == "refused: "* ]] && refuse "${err#refused: }"
    refuse "the $what can't be checked"
}

# The levels `platform dir --make` made (the deepest is `$1`, the topmost `$2`), removed again, so the
# preflight leaves the service user's home as it found it. Nothing made: nothing to do.
unmake() {
    local d=$1 top=$2
    [[ -n $top ]] || return 0
    while true; do
        as_service_user PATH="$user_tools" rmdir -- "$d" </dev/null || return 1
        [[ $d != "$top" ]] || return 0
        d=${d%/*}
    done
}

# platform_state.py plan for each wanted unit as its files are now: a refusal is the install's. The render
# is empty, since it only decides between actions that go ahead.
plan_check() {
    local u msg
    dir_check "user unit directory" --dir "$user_units" --kind units
    dir_check "install directory" --dir "$install_dir" --kind private
    for u in "${wanted[@]}"; do
        if ! msg=$(platform plan --unit "$u" --record "$record" --file "$user_units/$u" --render /dev/null \
            --backup-dir "$backups" "${plan_flags[@]}" 2>&1 >/dev/null </dev/null); then
            msg=${msg%%$'\n'*}
            [[ $msg == "refused: "* ]] && refuse "${msg#refused: }"
            refuse "$u can't be planned"
        fi
    done
}

# The service user's login PATH, where `uv` is (its last line: a profile may print).
login_path() {
    local path
    path=$(runuser -l "$service_user" -c 'printf "%s\n" "$PATH"' 2>/dev/null </dev/null) \
        || refuse "the service user's login shell does not run"
    printf '%s' "${path##*$'\n'}"
}

# `uv sync` for the release tree `$1`, as the service user, offline unless --fetch, with the uv cache `$2`
# if given (the preflight's disposable copy). UV_LINK_MODE=copy: a release shares no files with the cache.
uv_sync() {
    local args=("${UV_SYNC[@]:0:3}")
    ((fetch)) || args+=(--offline)
    args+=("${UV_SYNC[@]:3}" --project "$1")
    as_service_user PATH="$service_path" UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
        ${2:+UV_CACHE_DIR="$2"} "${args[@]}" </dev/null
}

# The commit `$1`'s whole tree, archived by the hardened git and extracted as the service user into `$2`.
extract_tree() {
    service_git archive --format=tar "$1" | as_service_user PATH="$user_tools" tar -x -C "$2"
}

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

# What verity under /var/lib needs, read before any change: `on <device>`, `off <device>`, or
# `unsupported <check>` when the host can't have it. What it can't read is a refusal.
verity_probe() {
    local mnt fstype device text line major="" minor="" sb block features
    mnt=$(findmnt -no FSTYPE,SOURCE --target "$(host /var/lib)" 2>/dev/null) \
        || refuse "the store's filesystem can't be found"
    read -r fstype device <<<"$mnt"
    [[ $fstype == ext4 ]] || { echo "unsupported not ext4"; return; }
    [[ -e $(host "$VERITY_FEATURE") ]] || { echo "unsupported the kernel's ext4 has no verity"; return; }
    text=$(tune2fs 2>&1) || true    # no arguments: the usage and the version, and exit 1
    while IFS= read -r line; do
        if [[ $line =~ ^tune2fs\ ([0-9]+)\.([0-9]+) ]]; then
            major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]}
        fi
    done <<<"$text"
    [[ -n $major ]] || refuse "the e2fsprogs version can't be read"
    ((major > 1 || (major == 1 && minor >= 45))) || { echo "unsupported e2fsprogs older than 1.45"; return; }
    [[ $device == /dev/* ]] || refuse "the store's device can't be read"
    sb=$(tune2fs -l "$device" 2>/dev/null) || refuse "the superblock can't be read"
    block=$(superblock_field "$sb" "Block size")
    features=$(superblock_field "$sb" "Filesystem features")
    [[ -n $block && -n $features ]] || refuse "the superblock can't be read"
    [[ $block == "$(getconf PAGESIZE)" ]] || { echo "unsupported the block size is not the page size"; return; }
    if has_verity "$features"; then echo "on $device"; else echo "off $device"; fi
}

if [[ -z $staged ]]; then
    # === the first stage: every check, the commit, the stage ===

    for tool in git runuser systemctl loginctl findmnt cmp sha256sum tar; do
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

    # The service user's user manager, reached on its own bus (design btq-v7sgt D1). Lingering is the
    # operator's: the installer never changes it, and a failed read is not `yes` (loginctl fails for a user
    # with no session and no lingering).
    linger=$(loginctl show-user "$service_user" -p Linger --value 2>/dev/null </dev/null) || linger=""
    [[ $linger == yes ]] || refuse "lingering is off for $service_user (or could not be read): run" \
        "\`sudo loginctl enable-linger $service_user\`, then run the installer again"
    [[ $(stat -c '%F %u' -- "$(host "/run/user/$service_uid/bus")" 2>/dev/null) == "socket $service_uid" ]] \
        || refuse "the service user's systemd bus is not running"
    manager=$(user_systemctl is-system-running 2>/dev/null) || true
    [[ $manager == running || $manager == degraded ]] \
        || refuse "the service user's systemd manager is not running"

    service_path=$(login_path)
    as_service_user PATH="$service_path" sh -c 'command -v uv' >/dev/null 2>&1 </dev/null \
        || refuse "uv is not on the service user's login PATH"

    if ((verity)); then
        probe=$(verity_probe) || exit 1
    fi

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
    PLATFORM_PY=$(<"$stage/tree/deploy/platform_state.py") || refuse "the commit has no platform_state.py"

    # The offline build, proven before any change (design btq-v7sgt D2): the release's exact `uv sync`, on
    # the full tree, in a scratch directory, against a hardlink copy of the service user's uv cache, which
    # uv's rename-into-place writes can't reach through. Never the real cache: a copy that fails refuses.
    # Skipped for a built release, and under --fetch, where the network may supply what the cache lacks.
    if [[ ! -f $releases/$resolved/.complete ]] && ((!fetch)); then
        cache=$(as_service_user PATH="$service_path" uv cache dir 2>/dev/null </dev/null) && [[ -n $cache ]] \
            || refuse "the uv cache can't be found"
        scratch_parent=$(host "$service_home")/.cache/heterodyne
        dir_check "scratch directory" --dir "$scratch_parent" --kind scratch --home "$(host "$service_home")"
        made=$(platform dir --what "scratch directory" --dir "$scratch_parent" --kind scratch \
            --home "$(host "$service_home")" --make </dev/null 2>/dev/null) \
            || refuse "the offline build check has no scratch directory"
        # armed before the scratch is allocated, so a failed mktemp still removes the levels just made
        scratch=""
        trap '[[ -z $scratch ]] || as_service_user PATH="$user_tools" rm -rf -- "$scratch"
              unmake "$scratch_parent" "$made"; rm -rf -- "$stage"' EXIT
        scratch=$(as_service_user PATH="$user_tools" mktemp -d "$scratch_parent/preflight.XXXXXX" \
            </dev/null) || refuse "the offline build check has no scratch directory"
        as_service_user PATH="$user_tools" cp -al -- "$cache" "$scratch/cache" >/dev/null 2>&1 </dev/null \
            || refuse "the uv cache could not be copied for the offline build check"
        as_service_user PATH="$user_tools" mkdir -- "$scratch/tree" || refuse "the commit can't be archived"
        extract_tree "$resolved" "$scratch/tree" || refuse "the commit can't be archived"
        if ! out=$(uv_sync "$scratch/tree" "$scratch/cache" 2>&1); then
            printf '%s\n' "$out" | tail -n 20 | sed -E 's/[0-9a-f]{64}/<hex>/g'
            refuse "the uv cache lacks a package this release needs: run with --fetch"
        fi
        as_service_user PATH="$user_tools" rm -rf -- "$scratch" \
            || refuse "the scratch directory can't be removed"
        unmake "$scratch_parent" "$made" || refuse "the scratch directory can't be removed"
        trap 'rm -rf -- "$stage"' EXIT
    fi
    plan_check
    wsd_outside_check

    args=(--staged "$stage" --checkout "$checkout" --commit "$resolved" --archive-sha256 "$archive_sum"
          --service-user "$service_user")
    if ((dry_run)); then args+=(--dry-run); fi
    if ((fetch)); then args+=(--fetch); fi
    if ((adopt)); then args+=(--adopt); fi
    if ((no_wsd)); then args+=(--no-wsd); fi
    if ((verity)); then args+=(--verity-probe "$probe"); else args+=(--no-verity); fi
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
PLATFORM_PY=$(<"$tree/deploy/platform_state.py") || refuse "the commit has no platform_state.py"

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
    echo "release: $commit"
    echo "archive sha256: $archive_sum"
    if ((rc == 0)) && [[ ${result[verity]:-} == "skipped ("* && ${result[verity]} != "skipped (--no-verity)" ]]
    then
        rc=3
    fi
    if ((rc == 0)) && [[ ${result[services]:-} == "skipped (admind init has not run)" ]]; then rc=3; fi
    if [[ -n ${renders:-} ]]; then as_service_user PATH="$user_tools" rm -rf -- "$renders" || true; fi
    # a dry run's render parent levels, whether or not the render directory was allocated
    ((!dry_run)) || unmake "${scratch_parent:-}" "${render_made:-}" || true
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

# The filesystem as the preflight read it, read again before the first change.
if ((verity)); then
    [[ -n $probe ]] || usage
    now=$(verity_probe) || exit 1
    [[ $now == "$probe" ]] || refuse "the store's filesystem changed since the preflight"
fi

# The user state as the preflight read it, read again before the first change.
service_path=$(login_path)
plan_check
wsd_outside_check

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

# --- the release: the commit's whole tree and its venv, as the service user (design btq-v7sgt D2) ---

begin release
release=$releases/$commit
vbin=$release/.venv/bin
if [[ -f $release/.complete ]]; then
    echo "ok: the release is built"
    finish_step ok
elif ((dry_run)); then
    echo "would: build the release"
    finish_step "would change"
else
    # A directory without .complete is a failed run's: it goes, and the release is built again in place
    # (the venv's scripts and its editable .pth hold absolute paths, so it can't be built elsewhere).
    echo "change: build the release"
    as_service_user PATH="$user_tools" rm -rf -- "$release" || fail "a partial release can't be removed"
    as_service_user PATH="$user_tools" mkdir -p -m 0700 -- "$release" || fail "the release can't be made"
    extract_tree "$commit" "$release" || fail "the commit can't be archived"
    uv_sync "$release" || fail "uv sync failed"
    uv_version=$(as_service_user PATH="$service_path" uv --version </dev/null) || fail "uv can't be run"
    lock_sum=$(sha256sum <"$release/uv.lock") || fail "uv.lock can't be read"
    printf '%s\n%s\n%s\n' "$commit" "$uv_version" "${lock_sum%% *}" \
        | as_service_user PATH="$user_tools" tee -- "$release/.complete" >/dev/null \
        || fail ".complete can't be written"
    finish_step changed
fi

# --- step 6: the host config, as heterodyne reads it for the service user, before step 5's change, which
# can't be undone ---

begin config
location_env=(PATH="$vbin:$service_path" HETERODYNE_CONFIG_DIR="$config_dir" HETERODYNE_STATE_DIR="$state_dir")
why=""
if [[ ! -f $release/.complete ]]; then
    finish_step "advisory (the release is not built yet)"      # a dry run only: the release step built it
else
    if out=$(as_service_user "${location_env[@]}" "$vbin/heterodyne" config check 2>&1 </dev/null); then
        while IFS= read -r line; do
            if [[ $line == "harnesses problem: "* ]]; then why="config check reports a store problem"; fi
        done <<<"$out"
    else
        why="config check failed"
    fi
    printf '%s\n' "$out"
    if [[ -z $why ]]; then
        finish_step ok
    elif ((dry_run)); then
        finish_step "advisory ($why; the changes above may fix it)"    # a dry run made none of them
    else
        fail "$why"
    fi
fi

# --- step 5: verity on the store's filesystem, last: it can't be undone ---

# The host can't have verity: the install carries on, and its exit is 3.
unsupported() {
    echo "skipped: verity ($1)"
    finish_step "skipped ($1)"
}

verity_step() {
    local kind rest sb
    read -r kind rest <<<"$probe"
    case $kind in
        unsupported) unsupported "$rest"; return ;;
        on) echo "ok: verity is on"; finish_step ok; return ;;
    esac
    if ((!dry_run)); then
        echo "warning: the ext4 verity feature can't be cleared once it is set"
    fi
    change tune2fs -O verity "$rest" || fail "tune2fs -O verity failed"
    if ((!dry_run)); then
        sb=$(tune2fs -l "$rest" 2>/dev/null) || fail "the superblock can't be read"
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

# --- the services: admind, then wsd, reconciled from the user manager's live state (design btq-v7sgt D4) ---

ready_wsd=60 settle_admind=15
if [[ -n $prefix ]]; then ready_wsd=${HZ_INSTALL_READY_SECONDS:-0} settle_admind=${HZ_INSTALL_READY_SECONDS:-0}; fi
svc_changes=0 written=0 renders="" render_made=""
declare -A st=()

# A user-level change: printed, and run unless --dry-run. The description stands in for the command, which
# may hold all of platform_state.py.
user_change() {
    local what=$1
    shift
    svc_changes=1
    if ((dry_run)); then echo "would: $what"; return 0; fi
    echo "change: $what"
    "$@" </dev/null || fail "$what failed"
}

daemon_of() { local u=${1#heterodyne-}; printf '%s' "${u%.service}"; }    # admind | wsd

render_unit() {    # $1 unit, $2 output file
    as_service_user "${location_env[@]}" "$vbin/$(daemon_of "$1")" unit --python "$vbin/python3" \
        --config-dir "$config_dir" --state-dir "$state_dir" --release "$commit" </dev/null \
        | as_service_user PATH="$user_tools" tee -- "$2" >/dev/null
}

# One unit's file, in the D6 order: the backup, the record's `writing`, the write, the record's `written`.
write_unit() {    # $1 unit, $2 render
    local u=$1 r=$2 action name
    if ! action=$(platform plan --unit "$u" --record "$record" --file "$user_units/$u" --render "$r" \
        --backup-dir "$backups" "${plan_flags[@]}" 2>&1 </dev/null); then
        fail "${action#refused: }"
    fi
    case $action in
        ok) echo "ok: $u is the render"; return 0 ;;
        mark-written)
            user_change "record $u as written" platform record-set --unit "$u" --state written --record "$record" ;;
        create|adopt|rewrite|resume)
            if [[ $action == adopt ]]; then
                if ((dry_run)); then
                    echo "would: back up $u"
                    name=dry-run
                else
                    name=$(platform backup --unit "$u" --file "$user_units/$u" --backup-dir "$backups" \
                        </dev/null) || fail "$u can't be backed up"
                    echo "change: back up $u"
                fi
                user_change "record $u as being written" platform record-set --unit "$u" --state writing \
                    --record "$record" --origin adopted --backup "$name" --backup-dir "$backups"
            else
                user_change "record $u as being written" platform record-set --unit "$u" --state writing \
                    --record "$record" --origin created
            fi
            ((dry_run)) || platform dir --what "user unit directory" --dir "$user_units" --kind units --make \
                </dev/null >/dev/null || fail "the user unit directory can't be made"
            user_change "write $u" platform write --unit "$u" --file "$user_units/$u" --render "$r"
            user_change "record $u as written" platform record-set --unit "$u" --state written --record "$record"
            written=1 ;;
        *) fail "$u: unexpected plan" ;;
    esac
}

# ActiveState, SubState, MainPID and NRestarts of `$1` into `st`.
unit_state() {
    local out line
    out=$(user_systemctl show "$1" -p ActiveState -p SubState -p MainPID -p NRestarts) || return 1
    st=()
    while IFS= read -r line; do st[${line%%=*}]=${line#*=}; done <<<"$out"
    [[ -n ${st[ActiveState]:-} && ${st[MainPID]:-} =~ ^[0-9]+$ && ${st[NRestarts]:-} =~ ^[0-9]+$ ]]
}

# What differs between the process `$2` and the render of `$1`: keys, never values.
process_drift() {
    platform process-diff --render "$renders/$1" --proc "$(host /proc)" --pid "$2" </dev/null
}

# A unit's failure, with its journal.
svc_fail() {
    as_service_user PATH="$user_tools" XDG_RUNTIME_DIR="/run/user/$service_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$service_uid/bus" journalctl --user -u "$1" -n 20 \
        --no-pager </dev/null || true
    fail "$2"
}

ready() {
    local i=0
    if [[ $1 == heterodyne-admind.service ]]; then sleep "$settle_admind"; return 0; fi
    while true; do
        as_service_user "${location_env[@]}" "$vbin/wsctl" status >/dev/null 2>&1 </dev/null && return 0
        ((++i < ready_wsd)) || return 1
        sleep 1
    done
}

# D4 steps 3 to 6: enabled, then running the render (started, or restarted when the process differs), ready,
# and then still the same process.
reconcile_service() {
    local u=$1 enabled diff pid restarts
    enabled=$(user_systemctl is-enabled "$u" 2>/dev/null) || true
    [[ $enabled == enabled ]] || user_change "enable $u" user_systemctl enable "$u"
    unit_state "$u" || fail "systemd's unit state can't be read"
    case ${st[ActiveState]}/${st[SubState]} in
        active/running)
            diff=$(process_drift "$u" "${st[MainPID]}") || fail "$u's process can't be compared"
            if [[ -z $diff ]]; then
                echo "ok: $u runs the render"
                return 0
            fi
            echo "drift: $u runs other than the render: ${diff//$'\n'/, }"
            user_change "restart $u" user_systemctl restart "$u" ;;
        failed/*)
            user_change "reset $u" user_systemctl reset-failed "$u"
            user_change "start $u" user_systemctl start "$u" ;;
        *) user_change "start $u" user_systemctl start "$u" ;;
    esac
    ((!dry_run)) || return 0
    unit_state "$u" || svc_fail "$u" "systemd's unit state can't be read"
    [[ ${st[ActiveState]} == active && ${st[MainPID]} != 0 ]] || svc_fail "$u" "$u did not start"
    pid=${st[MainPID]} restarts=${st[NRestarts]}
    ready "$u" || svc_fail "$u" "$u is not ready"
    unit_state "$u" || svc_fail "$u" "systemd's unit state can't be read"
    [[ ${st[ActiveState]} == active && ${st[MainPID]} == "$pid" && ${st[NRestarts]} == "$restarts" ]] \
        || svc_fail "$u" "$u restarted while it was checked"
    diff=$(process_drift "$u" "$pid") || svc_fail "$u" "$u's process can't be compared"
    [[ -z $diff ]] || svc_fail "$u" "$u does not run the render"
    echo "ok: $u is up and runs the render"
}

begin services
if [[ ! -f $release/.complete ]]; then
    # a dry run only (the release step built it): the plan, on the files as they are
    for u in "${wanted[@]}"; do write_unit "$u" /dev/null; done
    echo "would: reload the user manager, then start what the release changes"
    finish_step "would change"
else
    init_rc=0
    as_service_user "${location_env[@]}" "$vbin/admind" initialised >/dev/null 2>&1 </dev/null || init_rc=$?
    case $init_rc in 0|1) ;; *) fail "admind initialised can't tell (exit $init_rc)" ;; esac
    scratch_parent=$(host "$service_home")/.cache/heterodyne
    render_made=$(platform dir --dir "$scratch_parent" --kind scratch --home "$(host "$service_home")" \
        --what "scratch directory" --make </dev/null) || fail "no render directory"
    renders=$(as_service_user PATH="$user_tools" mktemp -d "$scratch_parent/render.XXXXXX" </dev/null) \
        || fail "no render directory"
    for u in "${wanted[@]}"; do
        render_unit "$u" "$renders/$u" || fail "$u can't be rendered"
        write_unit "$u" "$renders/$u"
    done
    # --no-wsd: a wsd the install manages is stopped and disabled; its file stays, for uninstall
    if ((no_wsd)); then
        records=$(platform record-show --record "$record" </dev/null) || fail "the install record can't be read"
        if [[ $'\n'$records == *$'\n'"unit heterodyne-wsd.service "* ]]; then
            state=$(user_systemctl is-active heterodyne-wsd.service 2>/dev/null) || true
            [[ $state != active ]] || user_change "stop heterodyne-wsd.service" \
                user_systemctl stop heterodyne-wsd.service
            enabled=$(user_systemctl is-enabled heterodyne-wsd.service 2>/dev/null) || true
            [[ $enabled != enabled ]] || user_change "disable heterodyne-wsd.service" \
                user_systemctl disable heterodyne-wsd.service
        fi
    fi
    reload=$written
    for u in "${wanted[@]}"; do
        need=$(user_systemctl show -p NeedDaemonReload --value "$u") || fail "systemd's unit state can't be read"
        [[ $need == no ]] || reload=1
    done
    if ((reload)); then user_change "reload the user manager" user_systemctl daemon-reload; fi
    if ((!dry_run)); then
        for u in "${wanted[@]}"; do
            need=$(user_systemctl show -p NeedDaemonReload --value "$u") || fail "systemd's unit state can't be read"
            [[ $need == no ]] || fail "systemd still has an old copy of a platform unit after daemon-reload"
            shown=$(user_systemctl show "$u" -p FragmentPath -p DropInPaths -p ExecStart -p Environment \
                -p UnsetEnvironment) || fail "systemd's unit state can't be read"
            diff=$(platform loaded-diff --render "$renders/$u" --fragment "${user_units#"$prefix"}/$u" \
                <<<"$shown") || fail "$u's loaded unit can't be compared"
            [[ -z $diff ]] || fail "$u as systemd loads it differs from the render: ${diff//$'\n'/, }"
            dropins=$(sed -n 's/^DropInPaths=//p' <<<"$shown")
            if [[ -n $dropins ]]; then
                names=()
                for d in $dropins; do names+=("${d##*/}"); done
                echo "note: $u has drop-ins, which change nothing the installer compares: ${names[*]}"
            fi
        done
        recorded=$(recorded_release)
        if [[ $recorded != "$commit" ]]; then
            platform record-release --record "$record" --release "$commit" </dev/null \
                || fail "the install record can't be written"
        fi
    fi
    skipped=""
    for u in "${wanted[@]}"; do
        if [[ $u == heterodyne-admind.service ]] && ((init_rc == 1)); then
            echo "skipped: $u (admind init has not run: run \`admind init\`, then the installer again)"
            skipped="skipped (admind init has not run)"
            continue
        fi
        reconcile_service "$u"
    done
    as_service_user PATH="$user_tools" rm -rf -- "$renders" || fail "the render directory can't be removed"
    renders=""
    if ((dry_run)); then
        unmake "$scratch_parent" "$render_made" || fail "the render directory can't be removed"
        render_made=""
    fi
    if [[ -n $skipped ]]; then
        finish_step "$skipped"
    elif ((no_wsd)); then
        finish_step "skipped (--no-wsd)"
    elif ((svc_changes)); then
        finish_step "$changed"
    else
        finish_step ok
    fi
fi
