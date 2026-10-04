"""Detection results from the synthetic experiments.

Per-type design: for every violation type, the share of traces flagged (v < 1) by the full
policy pathway, without synchronization, with the published prototype's reduced semantics, and
by alignment only (f < 1); the mutation analysis against the independent oracle (an injection
is effective when the oracle finds the trace inadmissible, or, for timestamp shifts, when a
temporal guard is violated); and the predicates that fail.
Mixed design: precision and recall of policy-violation detection, of nonconformance detection
(PACC < 1 against alignment only), and false alarms on clean traces, per noise level.
Outputs eval/results/tables/detection_*.csv.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import RESULTS  # noqa: E402

OUT = RESULTS / "tables"


def prf(pred, truth):
    tp, fp, fn = int((pred & truth).sum()), int((pred & ~truth).sum()), int((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "tp": tp, "fp": fp, "fn": fn}


def per_type():
    d = pd.read_csv(RESULTS / "synthetic_types_traces.csv", keep_default_na=False)
    rows = []
    for vtype, g in d.groupby("noise"):
        shift = vtype == "timestamp_shift"
        effective = (g.v < 1) if shift else (g.admissible == 0)
        rows.append({
            "type": vtype, "traces": len(g), "effective": effective.mean(),
            "pacc": (g.v < 1).mean(), "no_sync": (g.v_nosync < 1).mean(),
            "prototype": (g.v_reduced < 1).mean(), "alignment": (g.f < 1).mean(),
            "recall_effective": ((g.v < 1) & effective).sum() / max(effective.sum(), 1),
            "missed_inadmissible": int(((g.v == 1) & (g.admissible == 0)).sum()),
            "flagged_admissible": 0 if shift else int(((g.v < 1) & (g.admissible == 1)).sum()),
            "fails_a": (g.failed_a > 0).mean(), "fails_r": (g.failed_r > 0).mean(), "fails_b": (g.failed_b > 0).mean(),
        })
    table = pd.DataFrame(rows).set_index("type")
    table.round(4).to_csv(OUT / "detection_per_type.csv")
    return table


def mixed():
    d = pd.read_csv(RESULTS / "synthetic_mixed_traces.csv", keep_default_na=False)
    d["pol"], d["flw"] = d.policy_type != "", d.flow_type != ""
    d["pacc"] = 0.7 * d.f + 0.3 * d.v
    nonconforming = d.pol | (d.f < 1)
    rows = []
    for noise, g in [("all", d)] + list(d.groupby("noise")):
        nc = g.pol | (g.f < 1)
        rows.append({"noise": noise, "traces": len(g), "policy_labelled": int(g.pol.sum()),
                     "flow_labelled": int(g.flw.sum()), "flow_effective": int((g.flw & (g.f < 1)).sum()),
                     **{f"policy_{k}": x for k, x in prf(g.v < 1, g.pol).items() if k in ("precision", "recall")},
                     **{f"nosync_{k}": x for k, x in prf(g.v_nosync < 1, g.pol).items() if k == "recall"},
                     **{f"prototype_{k}": x for k, x in prf(g.v_reduced < 1, g.pol).items() if k == "recall"},
                     **{f"align_on_policy_{k}": x for k, x in prf(g.f < 1, g.pol).items() if k == "recall"},
                     **{f"pacc_{k}": x for k, x in prf(g.pacc < 1, nc).items() if k in ("precision", "recall")},
                     **{f"align_{k}": x for k, x in prf(g.f < 1, nc).items() if k in ("precision", "recall")},
                     "clean_false_alarms": int(((~g.pol) & (g.v < 1)).sum() + ((~g.flw) & (g.f < 1)).sum()),
                     "mean_f": g.f.mean(), "mean_v": g.v.mean(), "mean_pacc": g.pacc.mean()})
    table = pd.DataFrame(rows).set_index("noise")
    table.round(4).to_csv(OUT / "detection_mixed.csv")
    effective = d.pol & ((d.admissible == 0) | (d.v < 1))
    summary = {"policy_labelled": int(d.pol.sum()), "effective": int(effective.sum()),
               "detected": int((d.pol & (d.v < 1)).sum()),
               "benign_swaps": int((d.flw & (d.f == 1)).sum()), "nonconforming": int(nonconforming.sum())}
    return table, summary


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    print(per_type().round(4).to_string())
    table, summary = mixed()
    print(table.round(4).T.to_string())
    print(summary)
