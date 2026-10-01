from heterodyne.admind.commands import Command, CommandRunner
from heterodyne.services import UnitStatus


class Agent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def new(self) -> str:
        self.calls.append("new")
        return "launched"

    def interrupt(self) -> None:
        self.calls.append("interrupt")

    def tail(self, lines: int) -> str:
        return f"{lines} lines"

    def alive(self) -> bool:
        return True


class Services:
    def __init__(self) -> None:
        self.restarted: list[str] = []

    def restart(self, unit: str) -> tuple[bool, str]:
        self.restarted.append(unit)
        return (unit != "broken.service", "exit 1")

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "active", "running", "")


def runner() -> tuple[CommandRunner, Agent, Services]:
    agent, services = Agent(), Services()
    units = ("wsd.service", "broken.service")
    return CommandRunner(agent, services, units, lambda: False), agent, services  # type: ignore[arg-type]


def test_restart_only_allowlisted_units() -> None:
    r, _, services = runner()
    assert r.run(Command("restart", arg="wsd.service")) == "Restarted wsd.service."
    assert "failed" in r.run(Command("restart", arg="broken.service"))
    refused = r.run(Command("restart", arg="sshd.service"))
    assert refused.startswith("Refused") and services.restarted == ["wsd.service", "broken.service"]


def test_other_commands() -> None:
    r, agent, _ = runner()
    assert "fresh" in r.run(Command("new"))
    assert r.run(Command("interrupt")) == "Sent Esc to the admin agent."
    fence = "`" * 3
    assert r.run(Command("tail", lines=12)) == f"{fence}\n12 lines\n{fence}"
    assert agent.calls == ["new", "interrupt"]
    ps = r.run(Command("ps"))
    assert "wsd.service: active (running)" in ps
    assert "wn-agent (admind): down" in ps and "admin agent: running" in ps
