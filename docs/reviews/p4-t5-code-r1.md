Reviewer: gpt-6.1-sol. Reviewed commit: 98da7e8. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/session/server.py:91` — Every connection creates an unrestricted host thread before authentication. The listen backlog does not limit active handlers, and the five-second timeout resets on each read. A sandbox process can exhaust wsd’s threads/fds with many connections or trickled bytes; thread-start failure can terminate the accept loop. Fix with bounded concurrent handlers, safe rejection/cleanup at capacity, and an absolute request deadline. Test saturation and slow clients.

2. [BLOCKING] `src/heterodyne/session/server.py:72` — `close()` closes only the listener. Accepted connections and handler threads remain active and can spool events or update turn state after shutdown returns. Fix by tracking accepted sockets and threads, stopping admission, shutting down clients, and joining handlers before returning. Test shutdown while a client holds a partial request, asserting no surviving handlers or subsequent state changes.

3. [BLOCKING] `src/heterodyne/session/server.py:113` and `:120` — Malformed input can escape without the required reply: deeply nested JSON under 64 KiB raises `RecursionError`, and an escaped lone surrogate in `token` raises `UnicodeEncodeError`. Both were reproduced without filesystem writes. Fix by handling excessive nesting and invalid token encoding explicitly, returning `MALFORMED` or `FORBIDDEN` as appropriate without logging token contents. Add socket-level regression tests.

The diff matches Task 5’s sample apart from the reported lint comment, but that sample contains these defects. Focused tests could not run because temporary-file creation was denied, including under `/tmp`.

REVISE