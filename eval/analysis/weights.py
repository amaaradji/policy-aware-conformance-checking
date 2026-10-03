"""Weight sensitivity (RQ2) on the mixed-design synthetic logs.

Per log (model, seed, noise > 0), with PACC_alpha = alpha f + (1 - alpha) v:
- Kendall tau-b between the trace rankings at alpha and at the default 0.7;
- for every discordant pair of traces ((f_i - f_j)(v_i - v_j) < 0) the crossing weight
  alpha* = (v_j - v_i) / ((f_i - f_j) + (v_j - v_i)) at which their order flips (Property P4);
- at audit thresholds theta, the share of policy-only traces (f = 1, v < 1) and of flow-only
  traces (f < 1, v = 1) whose PACC falls below theta.
Outputs eval/results/tables/weights_*.csv.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kendalltau

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import RESULTS  # noqa: E402

ALPHAS = np.round(np.arange(0.0, 1.01, 0.1), 2)
THETAS = (0.90, 0.95, 0.99)


def crossing_weights(f, v):
    df = f[:, None] - f[None, :]
    dv = v[:, None] - v[None, :]
    mask = np.triu((df * dv) < 0, k=1)
    return (-dv[mask]) / (df[mask] - dv[mask])


def main():
    d = pd.read_csv(RESULTS / "synthetic_mixed_traces.csv", keep_default_na=False)
    d = d[d.noise > 0]
    taus, crossings = [], []
    for (m, seed, noise), g in d.groupby(["model", "seed", "noise"]):
        f, v = g.f.to_numpy(), g.v.to_numpy()
        base = 0.7 * f + 0.3 * v
        for a in ALPHAS:
            tau = kendalltau(a * f + (1 - a) * v, base).statistic
            taus.append({"model": m, "seed": seed, "noise": noise, "alpha": a, "tau": tau})
        crossings += [{"noise": noise, "alpha_star": x} for x in crossing_weights(f, v)]
    taus, crossings = pd.DataFrame(taus), pd.DataFrame(crossings)
    out = RESULTS / "tables"
    out.mkdir(parents=True, exist_ok=True)
    tau_table = taus.groupby("alpha").tau.agg(["mean", "std", "min"]).round(4)
    tau_table.to_csv(out / "weights_kendall.csv")
    q = crossings.alpha_star.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(4)
    shares = pd.DataFrame({"alpha": ALPHAS, "pairs_flipped_vs_0.7": [
        ((crossings.alpha_star > min(a, 0.7)) & (crossings.alpha_star < max(a, 0.7))).mean() for a in ALPHAS]})
    shares.round(4).to_csv(out / "weights_flipped_pairs.csv", index=False)
    crossings.to_csv(out / "weights_crossings.csv", index=False)
    policy_only = d[(d.f == 1) & (d.v < 1)]
    flow_only = d[(d.f < 1) & (d.v == 1)]
    rows = []
    for a in ALPHAS:
        for theta in THETAS:
            rows.append({"alpha": a, "theta": theta,
                         "policy_only_flagged": ((a * policy_only.f + (1 - a) * policy_only.v) < theta).mean(),
                         "flow_only_flagged": ((a * flow_only.f + (1 - a) * flow_only.v) < theta).mean()})
    flagged = pd.DataFrame(rows).round(4)
    flagged.to_csv(out / "weights_thresholds.csv", index=False)
    print("Kendall tau-b vs alpha = 0.7 (logs with noise > 0):\n", tau_table.to_string())
    print(f"\ndiscordant pairs: {len(crossings)}; alpha* quantiles:\n", q.to_string())
    print("\nshare of discordant pairs whose order differs from alpha = 0.7:\n", shares.round(4).to_string(index=False))
    print(f"\npolicy-only traces: {len(policy_only)} (v: mean {policy_only.v.mean():.4f}, min {policy_only.v.min():.4f});"
          f" flow-only traces: {len(flow_only)} (f: mean {flow_only.f.mean():.4f})")
    print(flagged.pivot(index="alpha", columns="theta", values=["policy_only_flagged", "flow_only_flagged"]).to_string())


if __name__ == "__main__":
    main()
