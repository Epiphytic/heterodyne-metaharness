# btq-98q verification

Tests use an isolated pytest environment with coincurve and pytest-subtests:
`PYTHONPATH=. /tmp/btq-ejp-test-venv/bin/python -m pytest -q`.
Focused regression output and the full suite log are retained alongside this file.
No live agents, Marmot sends or service restarts are part of these tests.

Ripwire quality-delta reports dynamically dispatched fixture/test methods as dead
code, ambient churn, and Store class length increasing by 25 lines. The bounded
registry repair belongs in Store alongside identity persistence; this class-size
finding is retained for review without suppression. This is not a clean quality
checker result. Git diff whitespace validation passes.
