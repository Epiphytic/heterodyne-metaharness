"""Pure loaded-kernel identity; no file, environment or ambient login-directory reads.

This is a deployment compatibility check, not source authentication or a sandbox. The
source blobs and admitting product revision are separately pinned in the input manifest.
Code identity is admitted on Python 3.12; another interpreter must be admitted explicitly.
"""

# This adapter deliberately fingerprints the exact shared internal kernel.
# pyright: reportPrivateUsage=false

import hashlib
import json
from types import CodeType, FunctionType

from heterodyne.admind import redact as kernel


def _code(code: CodeType) -> object:
    return [
        code.co_code.hex(),
        code.co_names,
        code.co_varnames,
        [_code(value) if isinstance(value, CodeType) else value for value in code.co_consts],
        code.co_argcount,
        code.co_kwonlyargcount,
        code.co_flags,
    ]


def fingerprint() -> str:
    functions = (kernel._compile_dirs, kernel._pass_with_dirs, kernel._redact_loop, kernel.redact_with_dirs)
    if any(not isinstance(value, FunctionType) for value in functions):
        return ""
    patterns = (kernel._PEM, kernel._NPUB, kernel._HEX_RUN, kernel._CONTROLS)
    body = [
        [_code(value.__code__) for value in functions],
        [(value.pattern, value.flags) for value in patterns],
        [(kind, value.pattern, value.flags) for kind, value in kernel._SECRETS],
        kernel._AFTER,
        kernel.MAX_PASSES,
        kernel.LOGIN_DIR,
        kernel.UNSTABLE,
    ]
    return hashlib.sha256(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


# Exact loaded Python 3.12 kernel from product c3e827949b35601dc3f39936b9f5821358dcb6c9.
EXPECTED = "83afc23702d60c698c818cc06fa1cf1978a797a9929d5086e0924a06b5ec9967"


def matches() -> bool:
    try:
        return fingerprint() == EXPECTED
    except (TypeError, ValueError, AttributeError, UnicodeError):
        return False
