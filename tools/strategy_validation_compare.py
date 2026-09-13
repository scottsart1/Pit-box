"""Create compact paired evidence, refusing mismatched benchmark inputs.

    python -m tools.strategy_validation_compare baseline.json candidate.json result.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.strategy_validation import percentile


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compare(baseline, candidate):
    for key in ("manifest_sha256", "evaluator_sha256", "adapter_contract"):
        if baseline[key] != candidate[key]:
            raise ValueError(f"Cannot compare different {key}")
    old_rows = {row["name"]: row for row in baseline["worlds"]}
    new_rows = {row["name"]: row for row in candidate["worlds"]}
    if old_rows.keys() != new_rows.keys():
        raise ValueError("Cannot compare different world sets")
    rows = []
    for name, old in old_rows.items():
        new = new_rows[name]
        for key in ("world", "state", "history", "oracle"):
            if old[key] != new[key]:
                raise ValueError(f"Cannot compare changed {key} for {name}")
        delta = new["regret_s"] - old["regret_s"] if old["regret_s"] is not None and new["regret_s"] is not None else None
        rows.append({
            "name": name, "family": old["family"], "split": old["split"],
            "input_sha256": fingerprint({key: old[key] for key in ("world", "state", "history")}),
            "oracle": old["oracle"],
            "baseline": {key: old[key] for key in ("recommended", "score", "regret_s", "contracts", "runtime_ms")},
            "candidate": {key: new[key] for key in ("recommended", "score", "regret_s", "contracts", "runtime_ms")},
            "paired_regret_change_s": round(delta, 6) if delta is not None else None,
        })
    paired = {}
    for label in ("all", "training", "heldout"):
        group = [row for row in rows if label == "all" or row["split"] == label]
        common = [row for row in group if row["paired_regret_change_s"] is not None]
        before = [row["baseline"]["regret_s"] for row in common]
        after = [row["candidate"]["regret_s"] for row in common]
        paired[label] = {
            "worlds": len(group), "common_executable_worlds": len(common),
            "improved": sum(row["paired_regret_change_s"] < -1e-6 for row in common),
            "unchanged": sum(abs(row["paired_regret_change_s"]) <= 1e-6 for row in common),
            "worse": sum(row["paired_regret_change_s"] > 1e-6 for row in common),
            "newly_executable": sum(not row["baseline"]["score"]["physical_feasible"] and row["candidate"]["score"]["physical_feasible"] for row in group),
            "newly_impossible": sum(row["baseline"]["score"]["physical_feasible"] and not row["candidate"]["score"]["physical_feasible"] for row in group),
            "baseline_common_median_regret_s": percentile(before, 0.5),
            "candidate_common_median_regret_s": percentile(after, 0.5),
            "baseline_common_p90_regret_s": percentile(before, 0.9),
            "candidate_common_p90_regret_s": percentile(after, 0.9),
        }
    return {
        "schema_version": 1,
        "baseline_commit": baseline["evaluated_commit"], "candidate_commit": candidate["evaluated_commit"],
        "evaluator_sha256": baseline["evaluator_sha256"], "manifest_sha256": baseline["manifest_sha256"],
        "inputs_and_oracles_identical": True,
        "baseline_source_dirty": baseline["source_has_uncommitted_changes"],
        "candidate_source_dirty": candidate["source_has_uncommitted_changes"],
        "adapter_contract": baseline["adapter_contract"],
        "paired": paired,
        "baseline_summary": baseline["summary"], "candidate_summary": candidate["summary"],
        "worlds": rows,
        "closed_loop": {"baseline": baseline["closed_loop"], "candidate": candidate["closed_loop"]},
        "limitations": baseline["manifest"]["limitations"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    baseline_bytes, candidate_bytes = args.baseline.read_bytes(), args.candidate.read_bytes()
    evidence = compare(json.loads(baseline_bytes), json.loads(candidate_bytes))
    evidence["baseline_artifact_sha256"] = hashlib.sha256(baseline_bytes).hexdigest()
    evidence["candidate_artifact_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps(evidence["paired"], indent=2))


if __name__ == "__main__":
    main()
