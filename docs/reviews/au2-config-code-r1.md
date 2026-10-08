Reviewer: gpt-6.1-sol. Reviewed range: 49ba1e4..f648a0a. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/config/accounts.py:44` — `identity.encode()` crashes with `UnicodeEncodeError` when a canonical path contains a surrogate-escaped POSIX filename byte. Reproduced with `HOME='/tmp/au2-review-home-\udcff'` and defaults only. Existing configs therefore fail to load, and `config check` emits an uncaught traceback. Fix: use UTF-8 encoding with `surrogateescape`, preserving ordinary-path keys, and add a regression test for this case.

REVISE
