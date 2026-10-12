Reviewer: gpt-6.1-sol. Reviewed commit: 843521d. Verdict: REVISE.

The 1 MiB tail cap that 843521d introduced came from the controller's fix direction. Fixed in a73f609.

1. [BLOCKING] [src/heterodyne/sandbox/resources/probes.py:140](~/repos/heterodyne-metaharness-v2-p4t9c/src/heterodyne/sandbox/resources/probes.py:140), issuer parsing at line 166 — **Tail truncation can replace certificate evidence with a server-controlled HTTP header.** Once verbose output exceeds 1 MiB, the real TLS issuer can disappear. The remaining `< issuer: CN=OpenShell Sandbox CA` header then satisfies the unrestricted `issuer:` search. With exit 0 and curl’s genuine terminal `http=200`, sections 6 and 6b accept a connection whose actual issuer was unrelated.

   I reproduced the false verdict using the actual parser and a synthesized 1,296,167-byte transcript. Large post-handshake traces are possible: [curl’s OpenSSL callback](https://raw.githubusercontent.com/curl/curl/curl-8_5_0/lib/vtls/openssl.c) logs session-ticket and key-update messages. A live reproduction and pytest rerun were blocked by this environment’s socket and filesystem restrictions.

   **Fix:** fail the capture whenever bytes are discarded, while continuing to drain and reap the child. Also restrict issuer extraction to curl’s certificate diagnostic lines rather than matching HTTP headers. Add a negative control containing an unrelated real issuer, output exceeding the cap, a forged issuer header, and a valid terminal status.

REVISE