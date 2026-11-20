"""Progress reports for long-running sync steps (matches, profiles)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Progress:
    done: int
    total: int
    elapsed_s: float
    unit: str = "matches"

    @property
    def remaining_s(self) -> float | None:
        if self.done == 0:
            return None
        return self.elapsed_s / self.done * (self.total - self.done)
