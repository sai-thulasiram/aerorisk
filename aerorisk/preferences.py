"""Preference rankers: order seats by personal preference.

Rankers are deliberately separate from the evidence layer. A ranker may
reorder seats a user is choosing between; it must never write to seat
facts, risk fields or data-quality fields, and its output is labeled with
its `kind`. An entertainment ranker (e.g. an astrology/"lucky seat" mode)
would be registered with kind="entertainment" and must not use the words
"safe" or "risk" in its explanation — enforced by `rank()` below.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

from .layout import Cabin, Seat

BANNED_WORDS = ("safe", "safer", "safest", "risk", "survival", "survive")


class Ranker(Protocol):
    name: str
    kind: str          # "comfort" | "entertainment"

    def score(self, seat: Seat, profile: dict[str, Any]) -> tuple[float, str]: ...


class ComfortRanker:
    name, kind = "comfort", "comfort"

    def score(self, seat: Seat, profile: dict[str, Any]) -> tuple[float, str]:
        want = profile.get("position")              # "window" | "aisle" | None
        s, why = 0.0, []
        if want and seat.position == want:
            s += 2; why.append(f"{want} seat")
        if seat.pitch_in:
            s += (seat.pitch_in - 28) / 4; why.append(f"{seat.pitch_in:g}in pitch")
        for bad in ("no_window", "last_row"):
            if bad in seat.flags:
                s -= 1.5; why.append(bad.replace("_", " "))
        return s, ", ".join(why)


REGISTRY: dict[str, Ranker] = {"comfort": ComfortRanker()}


def register(ranker: Ranker) -> None:
    REGISTRY[ranker.name] = ranker


def rank(cabin: Cabin, ranker_name: str, profile: dict[str, Any], *,
         candidates: list[str] | None = None, top: int = 5) -> list[dict[str, Any]]:
    r = REGISTRY[ranker_name]
    pool = [cabin.seat(c) for c in candidates] if candidates else cabin.seats
    scored = []
    for seat in pool:
        score, why = r.score(seat, profile)
        lowered = why.lower()
        if any(w in lowered for w in BANNED_WORDS):
            raise ValueError(f"Ranker {r.name!r} produced a safety claim: {why!r}")
        scored.append({"seat": seat.seat, "score": round(score, 3), "why": why, "ranker_kind": r.kind})
    scored.sort(key=lambda d: -d["score"])
    return scored[:top]
