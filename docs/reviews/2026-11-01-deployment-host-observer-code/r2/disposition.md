# Actual R2 disposition

The full actual HIGH review returned changes requested, with findings1/2 blocking R2. The source repair refuses a stale private endpoint before executing systemctl: a UID-authenticated session bus alone is not promoted to a trusted systemd observation. A stronger authenticated bus-owner path remains absent. Historical real-host diagnostic measurements are retained, but trusted observations on this host now HOLD even after its separately authorized venv-lock mode repair. This is an explicit automated-effect limitation; it does not block expressly authorized manual installation.

Unit readback now requires empty EnvironmentFiles, PassEnvironment and UnsetEnvironment. The explicit unit Environment release is required; inherited manager values cannot substitute for it. Active reads additionally bind actual original process environment/UID. PID values are bounded to signed pid_t before opening, and profile target IDs are validated before manager/tree/DB work. A real WAL fixture leaves its writer open, proves a nonempty WAL exists, copies/replays it read-only and observes quarantine/digest changes after committed WAL writes. The stale-private test proves refusal precedes any executable opening.

Findings5–9 remain explicit finite PoC limitations: repeated tree reads and ancestor opens, command path execution rather than fexecve, duplicated cessation manager logic, and redundant diagnostic endpoint checks. No effects, online/stopped/held guard, or global authority constructor is added. No expected state is recorded as a measured PASS.

Final repair checks:81 focused tests pass in1.56s; Ruff and Pyright pass. R3 uses the operator's exact medium PoC question and preserves both actual HIGH rounds.
