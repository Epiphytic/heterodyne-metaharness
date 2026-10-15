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

# With no install record, each managed unit as the files and systemd show it, without a release: wsd
# first, one "U KIND [WHY]" line each, and exit 1 when one is ambiguous (design btq-v7sgt D6). Its
# arguments are the marker, the unit directory, and --no-manager when no user manager runs; otherwise
# systemd's `show` text is on stdin. stdlib only, and in this file, so the pinned uninstall hash covers
# it. (No apostrophes in it: it is a single-quoted bash string.)
readonly NO_RECORD='
import os, stat, sys
marker, unit_dir = sys.argv[1].encode(), sys.argv[2]
no_manager = sys.argv[3:] == ["--no-manager"]
shown = {}
if not no_manager:
    for block in sys.stdin.read().strip().split("\n\n"):
        props = dict(line.partition("=")[::2] for line in block.splitlines())
        if "Id" in props:
            shown[props["Id"]] = props
bad = False
for unit in ("heterodyne-wsd.service", "heterodyne-admind.service"):
    path = os.path.join(unit_dir, unit)
    try:
        st = os.lstat(path)
        kind = "file" if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() else "odd"
    except FileNotFoundError:
        kind = "absent"
    except OSError:
        kind = "unreadable"
    if kind == "file":
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
                kind = "marker" if f.readline().startswith(marker) else "file"
        except OSError:
            kind = "unreadable"
    props = {"LoadState": "not-found", "ActiveState": "inactive"} if no_manager else shown.get(unit, {})
    load, active = props.get("LoadState"), props.get("ActiveState")
    if load is None or active is None:
        why = "the systemd state can not be read"
    elif kind == "odd":
        why = "the unit file is a symlink, not a regular file, or another user owns it"
    elif kind == "unreadable":
        why = "the unit file can not be read"
    elif kind == "marker":
        why = "the installer marker with no install record"
    elif kind == "absent" and (load != "not-found" or active != "inactive"):
        why = "loaded or running with no file"
    else:
        why = ""
    bad = bad or bool(why)
    print(unit, *(("ambiguous", why) if why else ("not-managed" if kind == "file" else "absent",)))
sys.exit(1 if bad else 0)
'
# Whether a path is there, for root, without following it: "absent" is ENOENT alone, "present" is a
# successful lstat, and "error" is any other failure, which callers treat as "can't tell".
readonly LSTAT='
import os, sys
try:
    os.lstat(sys.argv[1])
except FileNotFoundError:
    print("absent")
except OSError:
    print("error")
else:
    print("present")
'

# The directories the uninstall writes into or runs from, as `WHAT KIND DIR` triples after the home, walked
# from the home with lstat as platform_state.py `dir` does, since the recorded release may predate it: each
# level a real directory of the user, never a link, and no level above a directory writable by others. A
# unit directory must not be writable by others either. Absent passes. Prints `CODE WHAT` and exits 1 on
# the first failure. (No apostrophes in it: it is a single-quoted bash string.)
readonly WALK='
import os, stat, sys
def no(code, what):
    print(code, what)
    sys.exit(1)
home = sys.argv[1]
for what, kind, d in zip(*[iter(sys.argv[2:])] * 3):
    try:
        st = os.stat(home)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            no("not-theirs", what)
        rel = os.path.relpath(d, home)
        if rel == "." or rel == ".." or rel.startswith("../"):
            no("outside", what)
        p = home
        for part in rel.split("/"):
            if stat.S_IMODE(st.st_mode) & 0o022:
                no("above", what)
            p = os.path.join(p, part)
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                break
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
                no("not-theirs", what)
        else:
            if kind == "units" and stat.S_IMODE(st.st_mode) & 0o022:
                no("writable", what)
    except OSError:
        no("unreadable", what)
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

# How systemd sees the managed units. None runs when the service user's runtime directory is absent:
# logind makes it before user@ starts and removes it after the manager stops, so nothing is loaded.
# Any other failure to read it refuses, before the root steps.
runtime=$(host "/run/user/$service_uid")
lstat_of() {
    python3 -I -c "$LSTAT" "$1" </dev/null 2>/dev/null || echo error
}
show_units() {
    user_systemctl show "${MANAGED[@]}" -p Id -p LoadState -p ActiveState "$@" \
        || refuse "the service user's systemd state can't be read; nothing was changed"
}
walk() {
    local out code what
    out=$(as_service_user PATH="$user_tools" python3 -I -c "$WALK" "$(host "$service_home")" "$@" \
        2>/dev/null </dev/null) && return 0
    read -r code what <<<"$out"
    case $code in
        not-theirs) refuse "the $what is not the service user's directory; nothing was changed" ;;
        outside) refuse "the $what is not under the service user's home; nothing was changed" ;;
        above) refuse "the $what is under a directory others can write; nothing was changed" ;;
        writable) refuse "the $what can be written by others; nothing was changed" ;;
        *) refuse "the service user's directories can't be checked; nothing was changed" ;;
    esac
}
no_record() {
    as_service_user PATH="$user_tools" python3 -I -c "$NO_RECORD" "$MARKER_PREFIX" "$user_units" "$@"
}

has_record=$(lstat_of "$record")
case $has_record in
    absent|present) ;;
    *) refuse "the install record can't be read; nothing was changed" ;;
esac
if [[ $has_record == absent ]]; then
    # No install record: no release to ask, so the files and systemd are classified here.
    rc=0
    has_runtime=$(lstat_of "$runtime")
    case $has_runtime in
        absent) rows=$(no_record --no-manager </dev/null) || rc=$? ;;
        present)
            shown=$(show_units) || exit 1     # show_units has printed the refusal
            rows=$(printf '%s\n' "$shown" | no_record) || rc=$? ;;
        *) refuse "the service user's systemd state can't be read; nothing was changed" ;;
    esac
    if ((rc)); then
        while read -r u kind why; do
            if [[ $kind == ambiguous ]]; then echo "$u: ambiguous: $why" >&2; fi
        done <<<"$rows"
        refuse "a platform unit's ownership is ambiguous; nothing was changed"
    fi
    echo "platform: nothing installed"
    while read -r u kind _; do
        if [[ $kind == not-managed ]]; then echo "$u: not managed by the installer; left in place"; fi
    done <<<"$rows"
else
    # what the uninstall writes into, and the release it runs, before anything is read through them
    walk "user unit directory" units "$user_units" "install directory" private "$install_dir" \
        "backup directory" private "$install_dir/backup" "releases directory" private "$releases"
    shown=$(show_units -p FragmentPath) || exit 1

    rel=$(as_service_user PATH="$user_tools" python3 -I -c "$RECORD_RELEASE" "$record" \
        2>/dev/null </dev/null) || rel=""
    [[ $rel =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ && -f $releases/$rel/.complete ]] \
        || refuse "the install record names no completed release; nothing was changed"
    walk "release directory" private "$releases/$rel"
    # The recorded release's own deploy/platform_state.py, on its own interpreter, as the service user.
    py=("$releases/$rel/.venv/bin/python3" -I "$releases/$rel/deploy/platform_state.py")
    platform() { as_service_user PATH="$user_tools" "${py[@]}" "$@"; }
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
                    as_service_user PATH="$user_tools" rm -- "$user_units/$u" </dev/null \
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
