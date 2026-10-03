"""LLM faithfulness (RQ4): errors, omissions, stability, and cost per prompt variant.

An error is a claim the checked log does not support: a trace placed in the wrong section, a
non-existent or clean trace, an activity named in a control-flow claim that is not involved in
the deviation, a policy claim on a (trace, activity) without failed evaluation or with the
wrong dimension, or an entity or number absent from the inputs. An omission is a problematic
trace, a deviation, or a failed (trace, activity) the explanation does not mention.
Stability: mean pairwise Jaccard similarity of the traces claimed per section across repetitions.
Outputs eval/results/tables/llm_*.csv.
"""
import ast
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import RESULTS  # noqa: E402

PRECISION = ["flow_precision", "policy_precision", "silent_precision", "flow_activity_precision",
             "pair_precision", "dimension_accuracy"]
RECALL = ["flow_recall", "policy_recall", "silent_recall", "flow_coverage", "pair_recall"]
COUNTS = ["hallucinated_traces", "entities_unsupported", "numbers_unsupported"]


def jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 1.0


def main():
    d = pd.read_csv(RESULTS / "llm_faithfulness.csv")
    d["error"] = (d[PRECISION] < 1).any(axis=1) | (d[COUNTS] > 0).any(axis=1)
    d["omission"] = (d[RECALL] < 1).any(axis=1)
    stability = []
    for (variant, scenario), g in d.groupby(["variant", "scenario"]):
        sets = [{k: set(ast.literal_eval(r[k])) for k in ("claimed_flow", "claimed_policy", "claimed_silent")}
                for _, r in g.iterrows()]
        pairs = list(combinations(sets, 2))
        stability.append({"variant": variant, "scenario": scenario,
                          "jaccard": sum(jaccard(x[k], y[k]) for x, y in pairs for k in x) / (3 * len(pairs))})
    stability = pd.DataFrame(stability)
    summary = d.groupby("variant").agg(
        explanations=("scenario", "size"), with_error=("error", "mean"), with_omission=("omission", "mean"),
        **{c: (c, "mean") for c in PRECISION + RECALL + COUNTS},
        latency_s=("latency_s", "mean"), latency_sd=("latency_s", "std"),
        prompt_tokens=("prompt_tokens", "mean"), completion_tokens=("completion_tokens", "mean"))
    summary["stability_jaccard"] = stability.groupby("variant").jaccard.mean()
    out = RESULTS / "tables"
    out.mkdir(parents=True, exist_ok=True)
    summary.T.round(4).to_csv(out / "llm_summary.csv")
    errors = d[d.error].groupby(["variant", "scenario"]).size().rename("explanations_with_error")
    errors.to_csv(out / "llm_errors_by_scenario.csv")
    pd.set_option("display.width", 200)
    print(summary.T.round(3).to_string())
    print(errors.to_string())


if __name__ == "__main__":
    main()
