"""Execute the shipped strategy rendering against isolated Node DOM fixtures."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Strategy DOM behavior requires Node")


def rendered_sequence(changes, strategy_extra=None):
    harness = r"""
import fs from 'node:fs';
const source = fs.readFileSync(process.argv[1], 'utf8');
const module = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.disabled = false; this._text = ''; this.hidden = false;
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const nodes = {};
globalThis.document = {
  getElementById(id) { return nodes[id] ||= new Element(); },
  createElement(tag) { return new Element(tag); },
};
let requests = 0;
globalThis.fetch = async () => {
  requests += 1;
  return {ok: true, json: async () => ({spoken: 'Plan locked.'})};
};
const base = {
  feasible: true, legal: true, finish_projection_valid: true,
  inventory_status: 'known', inventory_feasible: true,
  stops_remaining: 1, compounds: ['MEDIUM', 'HARD'], box_laps: [12],
  projected_finish_position: 1, projected_points: 25, projected_rejoin_position: 5,
  projected_max_wear_pct: 60, instruction: 'Box lap 12 for HARD.',
  monte_carlo: {p75_s: 900, uncertainty_s: 2, calibrated: false},
};
const snapshots = [];
for (const change of JSON.parse(process.argv[2])) {
  const plan = {...base, ...change};
  const state = {connected: true, strategy: {available: true, confidence: 'low',
    recommended: plan, plans: [plan], model_summary: {}, ...JSON.parse(process.argv[3])}};
  module.renderCall(state);
  module.renderPlans(state);
  const row = nodes.stratPlanRows.children[0];
  const action = row.children[8].children[0];
  const requestsBefore = requests;
  // A direct listener call tests the guard independently of native disabled behavior.
  action.listeners.click();
  await new Promise(resolve => setTimeout(resolve, 0));
  snapshots.push({meta: nodes.stratMeta.textContent,
    rule: nodes.stratRule.textContent,
    position: row.children[3].textContent, points: row.children[4].textContent,
    verdict: row.children[7].textContent, disabled: action.disabled,
    requests: requests - requestsBefore, status: nodes.stratPlanStatus.textContent});
}
process.stdout.write(JSON.stringify(snapshots));
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", harness,
         str(ROOT / "static/js/strategy.js"), json.dumps(changes), json.dumps(strategy_extra or {})],
        # Node writes UTF-8. Without naming it, text mode decodes with the
        # locale encoding, which is cp1252 on a Windows runner: the em dash
        # this contract asserts on comes back mangled and the test fails on
        # Windows while passing everywhere else.
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=10,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("delta", [
    {"feasible": False},
    {"finish_projection_valid": False},
    {"legal": None},
])
def test_unsupported_projection_cannot_show_a_p1_finish_or_25_points(delta):
    result = rendered_sequence([delta])[0]
    assert "P1" not in result["meta"]
    assert "25 pts" not in result["meta"]
    assert result["position"] in {"Unsupported", "Unconfirmed"}
    assert result["points"] == "—"
    assert "unavailable" in result["meta"].lower()


def test_illegal_finish_has_zero_eligible_points_and_cannot_be_adopted():
    result = rendered_sequence([{"legal": False}])[0]
    assert "P1" not in result["meta"]
    assert "0 eligible points" in result["meta"]
    assert result["points"] == "0"
    assert result["verdict"] == "illegal"
    assert result["disabled"] is True
    assert result["requests"] == 0


def test_infeasible_plan_cannot_be_adopted_even_by_a_direct_listener_call():
    result = rendered_sequence([{"feasible": False}])[0]
    assert result["disabled"] is True
    assert result["requests"] == 0


def test_unknown_spare_inventory_qualifies_position_points_and_verdict():
    result = rendered_sequence([{"inventory_status": "unknown", "inventory_feasible": None}])[0]
    assert "conditional" in result["position"].lower()
    assert "conditional" in result["points"].lower()
    assert "spare tyres" in result["verdict"].lower()
    assert "spare tyres" in result["meta"].lower()
    assert result["disabled"] is False  # Executable/legal plan, stock needs confirmation.


def test_no_stop_does_not_depend_on_unknown_spares_and_shows_calibration_limit():
    result = rendered_sequence([{"inventory_status": "unknown", "inventory_feasible": None,
                                 "stops_remaining": 0, "box_laps": [], "compounds": ["MEDIUM"]}])[0]
    assert result["position"] == "P1"
    assert result["points"] == "25"
    assert "spare tyres" not in result["meta"].lower()
    assert "not yet calibrated" in result["meta"]
    assert result["disabled"] is False


def test_legality_inventory_and_forecast_only_changes_invalidate_board_cache():
    results = rendered_sequence([{}, {"legal": False},
                                 {"inventory_status": "unknown", "inventory_feasible": None},
                                 {"finish_projection_valid": False}])
    assert results[0]["position"] == "P1"
    assert results[1]["disabled"] is True
    assert results[1]["points"] == "0"
    assert "conditional" in results[2]["position"]
    assert results[3]["position"] == "Unsupported"


def test_future_wet_plan_cannot_claim_a_compound_waiver_already_earned():
    result = rendered_sequence([{}], strategy_extra={
        "compound_rule": {"applies": False, "wet_waiver": True},
        "observed_compound_rule": {"applies": True, "wet_waiver": False,
                                   "dry_count": 1, "change_outstanding": True},
    })[0]
    assert "1/2 dry compounds used" in result["rule"]
    assert "change is still required" in result["rule"]
    assert "waived" not in result["rule"]


def test_existing_snapshots_without_observed_rule_keep_the_compound_display():
    result = rendered_sequence([{}], strategy_extra={
        "compound_rule": {"applies": False, "wet_waiver": True},
    })[0]
    assert "waived by wet/inter running" in result["rule"]
