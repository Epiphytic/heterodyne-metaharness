"""What a successful `new-session` prints for fake `subprocess.run`s: the marker `Tmux.new_session` asks for
with `-P -F`, filled in from the call's own argv (its format and `-s` name), as tmux 3.4 does."""


def started(argv: list[str], sid: str = "$0", name: str | None = None) -> bytes:
    """The marker line for `argv`, or b"" if it is not a start. `name` overrides what tmux reports."""
    if "new-session" not in argv:
        return b""
    fmt = argv[argv.index("-F") + 1]
    reported = argv[argv.index("-s") + 1] if name is None else name
    return (fmt.replace("#{session_id}", sid).replace("#{session_name}", reported) + "\n").encode()
