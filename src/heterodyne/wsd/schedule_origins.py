"""Bounded memory-only origin association. Access is serialized by the owning Lane.lock."""

from dataclasses import dataclass, field

UINT64 = (1 << 64) - 1


@dataclass
class Origins:
    count: int = 0
    first: str | None = None
    last: str | None = None
    ids: set[str] = field(default_factory=set[str])
    truncated: bool = False
    completed_at: float | None = None

    def add(self, request_id: str) -> None:
        self.count = min(UINT64, self.count + 1)
        if self.first is None:
            self.first = request_id
        self.last = request_id
        if len(self.ids) < 64:
            self.ids.add(request_id)
        elif request_id not in self.ids:
            self.truncated = True

    def summary(self) -> dict[str, object]:
        return {
            "count": self.count,
            "first": self.first,
            "last": self.last,
            "truncated": self.truncated,
            "completed_at": self.completed_at,
        }
