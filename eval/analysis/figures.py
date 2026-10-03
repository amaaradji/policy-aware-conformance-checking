"""Figures of the revised manuscript (vector PDF, grayscale, sized for the two-column layout).

fig_detection.pdf  share of traces flagged per injected violation type (RQ1)
fig_weights.pdf    Kendall tau versus alpha, and the distribution of crossing weights (RQ2)
fig_runtime.pdf    execution time versus model size, versus number of traces, and speedup (RQ3)
Output folder: eval/figures (copied to PolicyLog/Figure for the manuscript).
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import FIGURES, RESULTS  # noqa: E402

plt.rcParams.update({"font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "legend.fontsize": 7,
                     "xtick.labelsize": 7, "ytick.labelsize": 7, "font.family": "serif",
                     "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
COLUMN, PAGE = 3.35, 7.0
TYPE_NAMES = {"activity_state_violation": "Activity state", "resource_state_violation": "Resource state",
              "wrong_resource": "Wrong resource", "timestamp_shift": "Timestamp shift",
              "missing_policy": "Missing policy", "state_sequence_skip": "State-sequence skip",
              "premature_completion": "Premature completion"}
CATEGORIES = ["Seq", "XOR", "AND", "Comb"]
MARKERS = dict(zip(CATEGORIES, ["o", "s", "^", "D"]))


def detection():
    table = pd.read_csv(RESULTS / "tables" / "detection_per_type.csv", index_col="type")
    table = table.loc[list(TYPE_NAMES)]
    series = [("pacc", "PACC", "0.15", ""), ("no_sync", "No synchronization", "0.45", "///"),
              ("prototype", "Prototype logic", "0.75", "..."), ("alignment", "Alignment only (0%)", "white", "")]
    fig, ax = plt.subplots(figsize=(COLUMN, 3.3))
    y = np.arange(len(table))
    height = 0.2
    for i, (col, label, color, hatch) in enumerate(series):
        ax.barh(y + (i - 1.5) * height, table[col] * 100, height, color=color, hatch=hatch,
                edgecolor="black", linewidth=0.4, label=label)
    ax.scatter(table["effective"] * 100, y - 1.5 * height, marker="|", s=60, color="black", zorder=3,
               label="Effective (oracle)")
    ax.set_yticks(y, [TYPE_NAMES[t] for t in table.index])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Traces flagged (%)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.35, -0.16), ncol=3, frameon=False, handlelength=1.2,
              columnspacing=0.8)
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_detection.pdf", bbox_inches="tight")


def weights():
    taus = pd.read_csv(RESULTS / "tables" / "weights_kendall.csv")
    crossings = pd.read_csv(RESULTS / "tables" / "weights_crossings.csv")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(PAGE, 2.2))
    ax1.plot(taus["alpha"], taus["mean"], color="black", marker="o", markersize=3, label="mean")
    ax1.fill_between(taus["alpha"], taus["min"], taus["mean"], color="0.8", label="minimum to mean")
    ax1.axvline(0.7, color="0.4", linestyle=":", linewidth=0.8)
    ax1.text(0.71, 0.55, r"default $\alpha=0.7$", fontsize=7, color="0.3")
    ax1.set_xlabel(r"$\alpha$")
    ax1.set_ylabel(r"Kendall $\tau_b$ vs. ranking at $\alpha=0.7$")
    ax1.set_ylim(0.25, 1.02)
    ax1.legend(frameon=False, loc="lower center")
    ax1.set_title("(a) Agreement of trace rankings")
    values = np.sort(crossings["alpha_star"].to_numpy())
    ax2.plot(values, np.arange(1, len(values) + 1) / len(values), color="black")
    for q, style in ((0.5, "--"), (0.95, ":")):
        x = np.quantile(values, q)
        ax2.axvline(x, color="0.4", linestyle=style, linewidth=0.8)
        ax2.text(x + 0.01, 0.1 if q == 0.5 else 0.3, f"{int(q * 100)}th pct: {x:.2f}", fontsize=7, color="0.3")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel(r"Crossing weight $\alpha^{*}$ of discordant pairs")
    ax2.set_ylabel("Cumulative share of pairs")
    ax2.set_title(r"(b) Weights at which pairs of traces swap")
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_weights.pdf", bbox_inches="tight")


def runtime():
    models = pd.read_csv(RESULTS / "runtime_models.csv")
    med = models.groupby(["category", "activities", "pathway"]).seconds.median().unstack("pathway").reset_index()
    traces = pd.read_csv(RESULTS / "runtime_traces.csv")
    tmed = traces.groupby(["model", "traces", "pathway"]).seconds.median().unstack("pathway").reset_index()
    parallel = pd.read_csv(RESULTS / "runtime_parallel.csv")
    pmed = parallel.groupby(["model", "pathway", "workers"]).seconds.median().unstack("workers")
    speedup = pmed.rdiv(pmed[1], axis=0)
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(PAGE, 2.4))
    for category in CATEGORIES:
        part = med[med.category == category].sort_values("activities")
        # medians over the three replicas of each size
        part = part.groupby("activities")[["policy", "alignment"]].median().reset_index()
        ax1.plot(part.activities, part.alignment, color="black", linestyle="--", marker=MARKERS[category],
                 markersize=3, markerfacecolor="white", linewidth=0.8)
        ax1.plot(part.activities, part.policy, color="black", linestyle="-", marker=MARKERS[category],
                 markersize=3, linewidth=0.8, label=category)
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("Activities in the model (200 traces)")
    ax1.set_ylabel("Median time (s)")
    ax1.set_title("(a) Model size: policy (solid), alignment (dashed)")
    ax1.legend(frameon=False, loc="upper left")
    for name, part in tmed.groupby("model"):
        marker = "v" if name == "Comb_m5" else MARKERS[name.split("_")[0]]  # the original 6-activity model
        ax2.plot(part.traces, part.policy, color="black", linestyle="-", marker=marker, markersize=3,
                 linewidth=0.8, label=name.replace("_m5", " (6 act.)").replace("_10_0", " (10 act.)"))
        ax2.plot(part.traces, part.alignment, color="black", linestyle="--", marker=marker,
                 markersize=3, markerfacecolor="white", linewidth=0.8)
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("Traces in the log")
    ax2.set_title("(b) Log size")
    ax2.legend(frameon=False, loc="upper left", fontsize=6)
    workers = speedup.columns.to_numpy()
    for (name, pathway), row in speedup.iterrows():
        marker = "v" if name == "Comb_m5" else MARKERS[name.split("_")[0]]
        ax3.plot(workers, row.to_numpy(), color="black", linestyle="-" if pathway == "policy" else "--",
                 marker=marker, markersize=3, linewidth=0.8,
                 markerfacecolor="black" if pathway == "policy" else "white")
    ax3.plot(workers, workers, color="0.6", linestyle=":", linewidth=0.8)
    ax3.set_xlabel("Worker processes (20,000 traces)")
    ax3.set_ylabel("Speedup")
    ax3.set_title("(c) Parallel speedup")
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_runtime.pdf", bbox_inches="tight")


if __name__ == "__main__":
    FIGURES.mkdir(parents=True, exist_ok=True)
    targets = sys.argv[1:] or ["detection", "weights", "runtime"]
    for name in targets:
        globals()[name]()
        print("written", name)
