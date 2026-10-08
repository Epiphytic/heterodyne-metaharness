#!/bin/bash
# One-off (S8): accepts Codex's "Hooks need review" dialog by keys ("Trust all and continue") and saves
# the config.toml it writes, to learn the shape of hooks.state trusted_hash. Run under sandbox.sh.
# Not a setup procedure: setup must write the entries itself (see run-codex.sh HOOKTRUST=hash).
set -u
D=$(cd "$(dirname "$0")" && pwd); . "$D/lib.sh"; R=$S8_OUT
MODE=look "$D/run-codex.sh" >/dev/null 2>&1 &   # writes config, launches; we race it for the keys
sleep 8; T send-keys -t "=agent:" 2; sleep 0.5; T send-keys -t "=agent:" Enter; sleep 8
snap agent after-trust; cp "$R/codex/config.toml" "$R/config-after.toml"; wait
