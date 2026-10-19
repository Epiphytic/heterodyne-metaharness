# Actual R5 findings and author dispositions

Actual invocation five; R3 aborted, so fourth substantive review. First two full HIGH retained. All eight new findings and all ten R4 findings remain OPEN UNWAIVED. Narrow readiness fix is offline PoC only. No native compatibility/deployment clearance.

## 1. OPEN UNWAIVED

> 1. `tests/test_admind_hook_high_fd.py:20`: The regression test skips whenever the soft RLIMIT_NOFILE is 1024 or less, which is the usual systemd/CI default. Both cases then skip and the fix is never exercised in standard CI.
> 
>    Failure scenario: CI runs with soft limit 1024 (hard limit much higher): both parametrized cases skip, the suite is green, and putting select.select back into backlog_waiting would not fail anything. The test could raise its own soft limit up to the hard limit and restore it afterwards (tests/test_tmux_launch_lock.py HOLDER already raises it to 2048), or run the check in a subprocess, instead of skipping.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 2. OPEN UNWAIVED

> 2. `src/heterodyne/harness/fetch.py:421`: FetchSession._stream still calls select.select([src], ...), the same FD_SETSIZE limit this commit fixes in the hook, and the ValueError it raises is not caught.
> 
>    Failure scenario: In a busy daemon or a long test process where the pipe fd is 1024 or higher, select raises ValueError('filedescriptor out of range in select()'). The fetch fails with an unexpected exception instead of a FetchRefused result. This is known OPEN, but it is the same root cause and the fix stopped at one call site.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 3. OPEN UNWAIVED

> 3. `src/heterodyne/admind/hook.py:543`: The fix covers the symptom (descriptor numbers at or above 1024). It does not explain why a test process reaches that many descriptors, and the commit message's 'cumulative test process' points to descriptors piling up across the suite.
> 
>    Failure scenario: If the descriptors are leaking across tests, switching to poll only lets the process climb further, until it hits EMFILE at the soft limit. Accept then fails, and backlog_waiting returns True every time, so dispatch is held. Nothing in this change bounds or checks how many descriptors are open, so the leak stays hidden (the descriptor cleanup is known OPEN).

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 4. OPEN UNWAIVED

> 4. `tests/tmux_guard.py:187`: Test helpers still use select.select (tmux_guard.py:187, test_escapes.py:93, test_sandbox_creates.py:108), which fails the same way once a long test process passes 1024 descriptors.
> 
>    Failure scenario: A full pytest run that leaks past fd 1024 (the situation this commit names) raises ValueError inside tmux_guard's read loop or test_escapes' bounded read. Unrelated tests then fail or hang depending on order. This is one plausible cause of the still-unexplained ~2-hour full-CI pytest stall.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 5. OPEN UNWAIVED

> 5. `src/heterodyne/admind/hook.py:547`: With poll, error states (POLLNVAL/POLLERR/POLLHUP) come back in the result instead of raising, so 'non-empty result' now silently stands for both 'connection queued' and 'socket broken'. The fail-closed comment only describes the exception path.
> 
>    Failure scenario: A later edit that tidies this to check `revents & POLLIN` would make an invalid or errored listener report 'no backlog', so dispatch goes ahead even though queued connections can no longer be seen. No test covers the POLLNVAL/POLLERR path; the uninspectable test only hits the ValueError raised for fd -1.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 6. OPEN UNWAIVED

> 6. `tests/test_admind_hook_high_fd.py:31`: The test injects a duplicated socket into the private `_listen` field and skips start(), so the docstring's 'real private hook listeners' claim is overstated. Listener setup, the loop add_reader registration, and close() at a high fd are not tested.
> 
>    Failure scenario: A failure on the real path (for example start() or the loop reader behaving differently at fd 1024 or higher, or close() removing the reader) would not be caught. The test proves only that the poll call itself handles a high fd.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 7. OPEN UNWAIVED

> 7. `src/heterodyne/admind/hook.py:545`: This repeats the existing 'make a poller, register POLLIN, poll(0)' idiom from sandbox/channel.py:312 alive() (and similar code in session/shim.py and codex_usage.py) instead of a shared helper.
> 
>    Failure scenario: A fourth copy of the same readiness check means the next FD_SETSIZE-style or error-flag (revents) fix has to find every copy again, as fetch.py's select already shows. A small shared helper like `readable_now(fd) -> bool` would cover all of them.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.

## 8. OPEN UNWAIVED

> 8. `src/heterodyne/admind/hook.py:545`: A new poll object is built and the socket registered on every backlog_waiting call, which runs on each dispatch attempt.
> 
>    Failure scenario: Small allocation and registration work on every flush of the dispatch loop. The poller could be created once in start() and dropped in close(). The cost is low and this is not correctness-relevant.

Retained as reported. No production, full-suite, lifecycle-cleanup or native acceptance claimed.
