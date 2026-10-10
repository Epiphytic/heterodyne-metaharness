Reviewer: gpt-6.1-sol. Reviewed commit: 09f176f. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/sandbox/spec.py:239–247`: Checking only the original source and its fully resolved path misses aliases into another account’s directory when a descendant symlink points outside it. For example, with `alias -> other-login` and `other-login/packages -> outside`, binding `alias/packages` passes despite D10 requiring rejection. I reproduced this read-only using `/bin/java`, whose parent resolves to `/usr/bin` but whose final target lies elsewhere. Fix by checking resolved path prefixes before following symlinks out of protected directories, while handling `..` correctly. Add regression tests combining a directory alias, an outward descendant symlink, and path normalization.

REVISE