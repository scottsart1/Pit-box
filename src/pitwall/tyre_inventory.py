"""Finite set allocation, separate from compound choice and tyre physics.

A compound is not a consumable: an indexed physical set is. Used spares may
be fitted with their reported wear, but this planner does not yet model taking
a set off and refitting it later in the same projected race.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any


class TyreInventory:
    def __init__(self, state: dict[str, Any], fallback: dict[str, dict[str, Any]]):
        records = state.get("tyre_sets") or []
        self.known = bool(records)
        self.groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.fitted: dict[str, Any] | None = None
        fitted_index = state.get("fitted_tyre_set_idx", -1)
        seen: set[int] = set()
        for ordinal, raw in enumerate(records):
            item = dict(raw)
            compound = str(item.get("compound", "UNKNOWN")).upper()
            if compound not in {"SOFT", "MEDIUM", "HARD", "INTER", "WET"}:
                continue
            index = int(item.get("index", ordinal))
            if index in seen:
                continue
            seen.add(index)
            item.update(index=index, compound=compound)
            if item.get("fitted") or index == fitted_index:
                item["fitted"] = True
                self.fitted = item
            elif item.get("available"):
                self.groups[compound].append(item)
        if not self.known:
            for compound, raw in fallback.items():
                self.groups[compound] = [dict(raw, index=None)]
        for items in self.groups.values():
            items.sort(key=lambda item: item.get("index") or 0)

    @property
    def status(self) -> str:
        return "known" if self.known else "unknown"

    def supports(self, compounds: list[str]) -> bool:
        return all(
            name in self.groups and (not self.known or len(self.groups[name]) >= count)
            for name, count in Counter(compounds).items()
        )

    def allocate(
        self,
        stints: list[dict[str, Any]],
        compounds: list[str],
        simulate: Callable[[str, int, int, dict[str, Any]], dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Minimum-cost unique-set matching for at most three future stints.

        Dynamic programming over a tiny stint bitmask considers every spare,
        including a quicker but slightly worn set. It is O(sets * 2^stops),
        not the Cartesian product of sets for every candidate schedule.
        Feasible assignments always outrank assignments exceeding tyre life.
        """
        assigned = list(stints)
        requests: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        offset = int(stints[0]["laps"])
        for position, (compound, stint) in enumerate(zip(compounds[1:], stints[1:]), 1):
            requests[compound].append((position, int(stint["laps"]), offset))
            offset += int(stint["laps"])
        for compound, jobs in requests.items():
            sets = self.groups.get(compound, [])
            if not sets or (self.known and len(sets) < len(jobs)):
                return None
            if not self.known:
                for position, laps, start in jobs:
                    assigned[position] = simulate(compound, laps, start, sets[0])
                continue
            # mask -> ((infeasible stints, conservative seconds), assignments)
            dp: dict[int, tuple[tuple[int, float], dict[int, dict[str, Any]]]] = {
                0: ((0, 0.0), {})
            }
            for item in sets:
                updated = dict(dp)
                models = [simulate(compound, laps, start, item) for _, laps, start in jobs]
                for mask, (score, matching) in dp.items():
                    for bit, ((position, _, _), model) in enumerate(zip(jobs, models)):
                        if mask & (1 << bit):
                            continue
                        key = mask | (1 << bit)
                        candidate = (
                            score[0] + int(not model["feasible"]),
                            score[1] + float(model["conservative_time_s"]),
                        )
                        if key not in updated or candidate < updated[key][0]:
                            updated[key] = (candidate, {**matching, position: model})
                dp = updated
            complete = dp.get((1 << len(jobs)) - 1)
            if complete is None:
                return None
            for position, model in complete[1].items():
                assigned[position] = model
        return assigned
