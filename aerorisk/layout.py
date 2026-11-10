"""Cabin configuration loading and seat generation.

A configuration is a compact, hand-authored JSON spec (see configs/).
`build_cabin` expands it into concrete seats with geometry and verifiable
layout facts. It deliberately produces NO survival or risk scores.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


class ConfigError(ValueError):
    pass


@dataclass
class Exit:
    id: str
    kind: str              # "door" | "overwing"
    pos: float             # position in row-ordinal space (0 = first row)
    y_in: float            # approx longitudinal position, inches from first row
    sides: list[str]
    at_row: int | None = None


@dataclass
class Seat:
    seat: str
    row: int
    letter: str
    row_index: int
    column_index: int
    position: str                      # window | middle | aisle
    side: str                          # L | R
    seats_to_aisle: int
    pitch_in: float | None
    pitch_source: str                  # row_specific | cabin_default | range | unknown
    y_in: float
    in_exit_row: bool
    nearest_exits: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    section: str = "middle"            # front | middle | rear (cabin thirds by row)
    near_exit: bool = False            # an exit within 5 rows (the Galea / Greenwich finding)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Cabin:
    config: dict[str, Any]
    rows: list[int]
    seats: list[Seat]
    exits: list[Exit]

    @property
    def id(self) -> str:
        return self.config["id"]

    def seat(self, label: str) -> Seat:
        label = label.strip().upper()
        for s in self.seats:
            if s.seat == label:
                return s
        raise KeyError(f"Seat {label} does not exist in {self.id}")


def load_config(config_id_or_path: str | Path) -> dict[str, Any]:
    p = Path(config_id_or_path)
    if not p.suffix:
        p = CONFIG_DIR / f"{config_id_or_path}.json"
    if not p.exists():
        raise ConfigError(f"No configuration file at {p}")
    cfg = json.loads(p.read_text())
    validate_config(cfg)
    return cfg


def list_configs(config_dir: Path = CONFIG_DIR) -> list[dict[str, Any]]:
    out = []
    for p in sorted(config_dir.glob("*.json")):
        cfg = json.loads(p.read_text())
        validate_config(cfg)
        out.append(cfg)
    return out


def validate_config(cfg: dict[str, Any]) -> None:
    required = ["id", "operator", "aircraft", "total_seats", "letters",
                "aisles_after", "rows", "exits", "provenance"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ConfigError(f"{cfg.get('id', '?')}: missing keys {missing}")
    for letter in cfg["aisles_after"]:
        if letter not in cfg["letters"]:
            raise ConfigError(f"{cfg['id']}: aisle after unknown letter {letter}")
    if not cfg["provenance"].get("sources"):
        raise ConfigError(f"{cfg['id']}: provenance.sources must not be empty")


def _row_numbers(cfg: dict[str, Any]) -> list[int]:
    r = cfg["rows"]
    skip = set(r.get("skip", []))
    return [n for n in range(r["first"], r["last"] + 1) if n not in skip]


def _pitch(cfg: dict[str, Any], row: int) -> tuple[float | None, str]:
    p = cfg.get("pitch_in", {})
    if row in p.get("unknown_rows", []):
        return None, "unknown"
    if str(row) in p.get("rows", {}):
        return float(p["rows"][str(row)]), "row_specific"
    if "default" in p:
        return float(p["default"]), "cabin_default"
    if "default_range" in p:
        return None, "range"
    return None, "unknown"


def _column_info(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Classify each letter as window/middle/aisle and count seats to the aisle."""
    letters = cfg["letters"]
    aisle_after = set(cfg["aisles_after"])
    # split into blocks separated by aisles
    blocks: list[list[str]] = [[]]
    for letter in letters:
        blocks[-1].append(letter)
        if letter in aisle_after:
            blocks.append([])
    blocks = [b for b in blocks if b]
    info: dict[str, dict[str, Any]] = {}
    n_blocks = len(blocks)
    for bi, block in enumerate(blocks):
        for li, letter in enumerate(block):
            outer_block = bi in (0, n_blocks - 1)
            # distance to an aisle within this block
            dist_left = li if bi > 0 else math.inf
            dist_right = (len(block) - 1 - li) if bi < n_blocks - 1 else math.inf
            to_aisle = int(min(dist_left, dist_right))
            if to_aisle == 0:
                pos = "aisle"
            elif outer_block and (li == 0 and bi == 0 or li == len(block) - 1 and bi == n_blocks - 1):
                pos = "window"
            else:
                pos = "middle"
            side = "L" if letters.index(letter) < len(letters) / 2 else "R"
            info[letter] = {"position": pos, "seats_to_aisle": to_aisle, "side": side,
                            "column_index": letters.index(letter)}
    return info


SECTIONS = ("front", "middle", "rear")
NEAR_EXIT_ROWS = 5


def section_bounds(cfg: dict[str, Any], rows: list[int]) -> dict[str, tuple[int, int]]:
    """Cabin thirds by row (the split used by the TIME 2015 review). A config may override with "sections"."""
    if cfg.get("sections"):
        return {k: tuple(v) for k, v in cfg["sections"].items()}
    n = len(rows)
    a, b = round(n / 3), round(2 * n / 3)
    return {"front": (rows[0], rows[a - 1]), "middle": (rows[a], rows[b - 1]), "rear": (rows[b], rows[-1])}


def section_of(cfg: dict[str, Any], rows: list[int], row: int) -> str:
    for name, (lo, hi) in section_bounds(cfg, rows).items():
        if lo <= row <= hi:
            return name
    return "middle"


def build_cabin(cfg: dict[str, Any]) -> Cabin:
    rows = _row_numbers(cfg)
    idx = {r: i for i, r in enumerate(rows)}

    # Longitudinal position: cumulative pitch. Unknown pitch -> cabin default for drawing only.
    pcfg = cfg.get("pitch_in", {})
    default = float(pcfg.get("default") or (sum(pcfg["default_range"]) / 2 if "default_range" in pcfg else 30))
    y_of: dict[int, float] = {}
    y = 0.0
    for r in rows:
        y_of[r] = y
        p, _ = _pitch(cfg, r)
        y += p if p is not None else default

    exits: list[Exit] = []
    for e in cfg["exits"]:
        if e.get("at_row") is not None:
            r = e["at_row"]
            if r not in idx:
                raise ConfigError(f"{cfg['id']}: exit {e['id']} at non-existent row {r}")
            exits.append(Exit(e["id"], e["kind"], float(idx[r]), y_of[r], e["sides"], at_row=r))
        elif e.get("before_row") is not None:
            r = e["before_row"]
            exits.append(Exit(e["id"], e["kind"], idx[r] - 0.5, y_of[r] - default / 2, e["sides"]))
        elif e.get("after_row") is not None:
            r = e["after_row"]
            p, _ = _pitch(cfg, r)
            exits.append(Exit(e["id"], e["kind"], idx[r] + 0.5,
                              y_of[r] + (p or default) + default / 2, e["sides"]))
        else:
            raise ConfigError(f"{cfg['id']}: exit {e['id']} needs at_row/before_row/after_row")

    exit_rows = {e.at_row for e in exits if e.at_row is not None}
    missing = {s.upper() for s in cfg.get("missing_seats", [])}
    flags_cfg = cfg.get("seat_flags", {})
    cols = _column_info(cfg)

    seats: list[Seat] = []
    for r in rows:
        pitch, psrc = _pitch(cfg, r)
        for letter in cfg["letters"]:
            label = f"{r}{letter}"
            if label in missing:
                continue
            c = cols[letter]
            ri = idx[r]
            near = []
            for e in exits:
                if e.at_row is not None:
                    between = max(0, abs(ri - int(e.pos)) - 1) if ri != int(e.pos) else 0
                else:
                    between = int(abs(ri - e.pos) - 0.5)
                near.append({
                    "exit": e.id, "kind": e.kind,
                    "direction": "forward" if e.pos < ri else ("aft" if e.pos > ri else "same_row"),
                    "rows_between": between,
                    "approx_distance_in": round(abs(y_of[r] - e.y_in)),
                    "same_side_available": c["side"] in e.sides,
                })
            near.sort(key=lambda d: (d["rows_between"], d["approx_distance_in"]))
            flags = []
            for flag_name, val in flags_cfg.items():
                if val == "all" or label in {v.upper() for v in val}:
                    flags.append(flag_name)
            if c["position"] != "window":
                # window seat on this side is missing in this row (e.g. 28A beside an exit door)
                row_side = [l for l in cfg["letters"] if cols[l]["side"] == c["side"]]
                outer = row_side[0] if c["side"] == "L" else row_side[-1]
                between = row_side[:row_side.index(letter)] if c["side"] == "L" else row_side[row_side.index(letter) + 1:]
                if outer != letter and all(f"{r}{l}" in missing for l in between):
                    flags.append("outermost_seat_window_seat_absent")
            if ri == 0:
                flags.append("first_row")
            if ri == len(rows) - 1:
                flags.append("last_row")
            if r not in exit_rows and ri + 1 < len(rows) and rows[ri + 1] in exit_rows:
                flags.append("row_ahead_of_exit_row")
            seats.append(Seat(
                seat=label, row=r, letter=letter, row_index=ri,
                column_index=c["column_index"], position=c["position"], side=c["side"],
                seats_to_aisle=c["seats_to_aisle"], pitch_in=pitch, pitch_source=psrc,
                y_in=round(y_of[r], 1), in_exit_row=r in exit_rows,
                nearest_exits=near[:2], flags=sorted(set(flags)),
                section=section_of(cfg, rows, r),
                near_exit=any(abs(ri - e.pos) <= NEAR_EXIT_ROWS for e in exits),
            ))

    if len(seats) != cfg["total_seats"]:
        raise ConfigError(
            f"{cfg['id']}: generated {len(seats)} seats but config declares {cfg['total_seats']}")
    return Cabin(cfg, rows, seats, exits)


def seat_facts(cabin: Cabin, label: str) -> dict[str, Any]:
    """User-facing facts for one seat. Facts only; risk is explicitly not estimated."""
    s = cabin.seat(label)
    prov = cabin.config["provenance"]
    nearest = s.nearest_exits[0]
    if nearest["direction"] == "same_row":
        exit_sentence = f"{s.seat} is in an exit row ({nearest['exit']})."
    else:
        exit_sentence = (f"Nearest exit {nearest['exit']} ({nearest['kind']}) is {nearest['direction']}, "
                         f"{nearest['rows_between']} seat row(s) between.")
    return {
        "configuration": cabin.id,
        "seat": s.seat,
        "summary": f"{s.seat} is {'an' if s.position == 'aisle' else 'a'} {s.position} seat on the {'left' if s.side == 'L' else 'right'} side. {exit_sentence}",
        "layout": s.to_dict(),
        "historical_seat_risk": {
            "estimable": False,
            "reason": "No validated occupant-level dataset supports a per-seat survival probability. "
                      "Seat-specific evidence is shown only from individual investigation records.",
        },
        "data_quality": {
            "status": prov["status"],
            "method": prov["method"],
            "sources": prov["sources"],
            "conflicts": prov.get("conflicts", []),
            "corroboration": prov.get("corroboration", {}),
            "primary_source_check": prov.get("primary_source_check", "not done"),
        },
    }
