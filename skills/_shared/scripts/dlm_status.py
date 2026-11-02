#!/usr/bin/env python3
"""Derived statuses. Stored fields never claim verification; it is computed."""

from __future__ import annotations

from collections import Counter
from typing import Any

from dlm_model import ARTIFACT_STATES, Ledger

TERMINAL_ITEM = {"pass", "na"}
TERMINAL_OBLIGATION = {"verified", "waived", "superseded"}


def population_counts(population: dict[str, Any]) -> Counter:
    counts: Counter = Counter(item.get("status", "pending") for item in population.get("items", []))
    counts["total"] = len(population.get("items", []))
    return counts


def population_closed(population: dict[str, Any]) -> bool:
    counts = population_counts(population)
    if counts["total"] == 0:
        return False
    if population.get("sample"):
        return counts["fail"] == 0 and counts["gap"] == 0 and counts["pass"] > 0
    return all(counts[s] == 0 for s in ("pending", "fail", "gap", "blocked"))


def valid_evidence(ledger: Ledger, obligation_id: str) -> list[dict[str, Any]]:
    return [v for v in ledger.collection("V")
            if obligation_id in v.get("obligations", []) and v.get("status", "valid") == "valid"]


def obligation_status(ledger: Ledger, obligation: dict[str, Any]) -> str:
    stored = obligation.get("status", "open")
    if stored in {"transferred", "waived", "superseded"}:
        return stored
    coverage = obligation.get("coverage", "single")
    evidence = valid_evidence(ledger, obligation["id"])
    if coverage == "full":
        population = ledger.find(obligation.get("population") or "", "N")
        if population is None:
            return "unknown"
        if population_closed(population) and not population.get("sample"):
            return "verified"
        counts = population_counts(population)
        return "implemented" if counts["pass"] else "open"
    if coverage == "sample":
        if evidence and obligation.get("sample_decision"):
            return "verified"
        return "implemented" if evidence else "open"
    if any(v.get("scope") == "full" for v in evidence):
        return "verified"
    if evidence:
        return "implemented"
    stale = [v for v in ledger.collection("V")
             if obligation["id"] in v.get("obligations", []) and v.get("status") == "unverifiable"]
    return "stale" if stale else "open"


def owned(ledger: Ledger, task_id: str) -> list[dict[str, Any]]:
    return [o for o in ledger.collection("O") if o.get("owner") == task_id]


def preserved(ledger: Ledger, task_id: str) -> list[dict[str, Any]]:
    return [o for o in ledger.collection("O") if task_id in (o.get("preservers") or [])]


def artifact_ready(state: str, required: str) -> bool:
    try:
        return ARTIFACT_STATES.index(state) >= ARTIFACT_STATES.index(required)
    except ValueError:
        return False
