"""Detection on synthetic logs (RQ1) and scores for the weight analysis (RQ2).

Design "mixed": for every model, seed, and noise level, one observed log in which a fraction
`noise` of the traces receives one policy violation (the six types and weights of
src/inject_deviations.py) and, independently, a fraction `noise` receives one control-flow
deviation (swap, skip, insert).
Design "types": for every model, seed, and violation type (the six plus premature_completion),
one log in which every trace receives one violation of that type and no control-flow deviation.

Each log is checked by the control-flow pathway (alignment-only baseline, f), the full policy
pathway (v), the policy pathway without activity/resource synchronization (v_nosync: activity,
resource, and binding checked separately), and the policy pathway reduced to what the
published prototype evaluated (v_reduced). One row per trace in eval/results/<out>_traces.csv.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import checker, configs, core, oracle  # noqa: E402
from pacc_eval.paths import MODELS, RESULTS  # noqa: E402
from pacc_eval.profile import model_profile  # noqa: E402

TYPES = core.VIOLATION_TYPES + ["premature_completion"]
FIELDS = ["design", "model", "category", "n_activities", "seed", "noise", "trace", "events",
          "f", "v", "v_nosync", "v_reduced", "policy_type", "flow_type", "failed_a", "failed_r", "failed_b", "admissible"]


def evaluate(writer, design, m, category, n_act, seed, noise, log, config, model, pol, flow):
    f = core.control_flow_pathway(log, config, model)
    v, reasons = core.policy_pathway(log, config)
    v_nosync, _ = core.policy_pathway(log, config, checker.Semantics(sync=False))
    v_reduced, _ = core.policy_pathway(log, config, checker.PUBLISHED)
    types = core.load_types()
    pol_by_trace = {x["trace"]: x["type"] for x in pol}
    flow_by_trace = {x["trace"]: x["type"] for x in flow}
    failed = {}
    for r in reasons:
        counts = failed.setdefault(r["trace"], [0, 0, 0])
        for i, key in enumerate("arb"):
            counts[i] += bool(r[key])
    for t, events in enumerate(log):
        a, r, b = failed.get(t, (0, 0, 0))
        writer.writerow({"design": design, "model": m, "category": category, "n_activities": n_act,
                         "seed": seed, "noise": noise, "trace": t, "events": len(events),
                         "f": round(f[t], 6), "v": round(v[t], 6), "v_nosync": round(v_nosync[t], 6),
                         "v_reduced": round(v_reduced[t], 6), "policy_type": pol_by_trace.get(t, ""),
                         "flow_type": flow_by_trace.get(t, ""), "failed_a": a, "failed_r": r, "failed_b": b,
                         "admissible": int(oracle.admissible(events, config, *types))})


def run(design, model_ids, seeds, noise_levels, n_traces, out_name):
    folder = MODELS / "original"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{out_name}_traces.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for m in model_ids:
            bpmn = folder / f"processModel_{m}.bpmn"
            category, n_act = model_profile(bpmn)
            model = core.load_model(bpmn)
            config = configs.corrected_activity_info(core.load_activity_info(folder / f"activity_info_{m}.json"))
            for seed in seeds:
                if design == "mixed":
                    for noise in noise_levels:
                        log, pol, flow = core.generate(model, config, n_traces, noise, noise,
                                                       seed=seed * 1000 + int(noise * 100))
                        evaluate(writer, design, m, category, n_act, seed, noise, log, config, model, pol, flow)
                else:
                    for k, vtype in enumerate(TYPES):
                        log, pol, flow = core.generate(model, config, n_traces, 1.0, 0.0,
                                                       seed=seed * 1000 + 500 + k,
                                                       policy_types=[vtype], policy_weights=[1])
                        evaluate(writer, design, m, category, n_act, seed, vtype, log, config, model, pol, flow)
                fh.flush()
            print(f"model {m} ({category}, {n_act} activities) done", flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", choices=["mixed", "types"], default="mixed")
    ap.add_argument("--models", type=int, nargs="+", default=list(range(1, 21)))
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--noise", type=float, nargs="+", default=[0.0, 0.05, 0.1, 0.2, 0.3])
    ap.add_argument("--traces", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    print(run(args.design, args.models, args.seeds, args.noise, args.traces, args.out or f"synthetic_{args.design}"))
