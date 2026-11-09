"""Tail-number (registration) knowledge.

Two sources, in priority order:
1. data/tail_overrides.csv — hand-verified registration -> configuration id.
   This is the only thing that can make a match "exact".
2. OpenSky Network aircraft database (aircraftDatabase.csv) — registration ->
   ICAO type code + operator. Free for research/non-commercial use; check
   https://opensky-network.org/ terms before commercial use. Download it
   yourself (the file is large) and pass its path.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def norm_reg(reg: str | None) -> str | None:
    if not reg:
        return None
    return reg.strip().upper().replace(" ", "")


@dataclass(frozen=True)
class TailOverride:
    registration: str
    config_id: str
    source: str
    verified_at: str


@dataclass(frozen=True)
class AircraftRecord:
    registration: str
    icao24: str | None
    typecode: str | None
    model: str | None
    operator_icao: str | None
    operator_name: str | None


def load_tail_overrides(path: Path = DATA_DIR / "tail_overrides.csv") -> dict[str, TailOverride]:
    if not path.exists():
        return {}
    out: dict[str, TailOverride] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("registration") or row["registration"].startswith("#"):
                continue
            reg = norm_reg(row["registration"])
            out[reg] = TailOverride(reg, row["config_id"].strip(),
                                    row.get("source", "").strip(), row.get("verified_at", "").strip())
    return out


class OpenSkyFleet:
    """Streaming loader for OpenSky aircraftDatabase.csv, filtered to operators of interest."""

    # Column names vary slightly across OpenSky dump versions; try aliases.
    ALIASES = {
        "registration": ["registration"],
        "icao24": ["icao24"],
        "typecode": ["typecode", "icaoaircrafttype_typecode", "typecode "],
        "model": ["model"],
        "operator_icao": ["operatoricao", "operator_icao"],
        "operator_name": ["operator", "owner"],
    }

    def __init__(self, records: dict[str, AircraftRecord]):
        self.records = records

    @classmethod
    def from_csv(cls, path: str | Path, operators_icao: set[str] | None = None) -> "OpenSkyFleet":
        records: dict[str, AircraftRecord] = {}
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh, quotechar="'" if _sniff_single_quote(path) else '"')
            header = {h.strip().strip("'\"").lower(): h for h in (reader.fieldnames or [])}

            def col(row, key):
                for alias in cls.ALIASES[key]:
                    h = header.get(alias)
                    if h is not None:
                        v = (row.get(h) or "").strip().strip("'\"")
                        return v or None
                return None

            for row in reader:
                op = (col(row, "operator_icao") or "").upper() or None
                if operators_icao and op not in operators_icao:
                    continue
                reg = norm_reg(col(row, "registration"))
                if not reg:
                    continue
                records[reg] = AircraftRecord(
                    registration=reg, icao24=col(row, "icao24"),
                    typecode=(col(row, "typecode") or "").upper() or None,
                    model=col(row, "model"), operator_icao=op,
                    operator_name=col(row, "operator_name"))
        return cls(records)

    def lookup(self, registration: str | None) -> AircraftRecord | None:
        reg = norm_reg(registration)
        return self.records.get(reg) if reg else None

    def fleet_summary(self) -> dict[tuple[str | None, str | None], int]:
        out: dict[tuple[str | None, str | None], int] = {}
        for r in self.records.values():
            k = (r.operator_icao, r.typecode)
            out[k] = out.get(k, 0) + 1
        return out


def _sniff_single_quote(path: str | Path) -> bool:
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
    return first.startswith("'")
