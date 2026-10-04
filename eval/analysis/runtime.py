"""Execution time: medians and spread of the policy and control-flow pathways.

Reads eval/results/runtime_<part>.csv (whichever exist) and writes eval/results/tables/runtime_*.csv:
per-measurement medians and interquartile ranges by part and grouping, the policy share of the
total checking time, throughput (events per second) of the policy pathway, and speedups.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import RESULTS  # noqa: E402

OUT = RESULTS / "tables"


def iqr(s):
    return s.quantile(0.75) - s.quantile(0.25)


def summarize(d, keys):
    med = d.pivot_table(index=keys, columns="pathway", values="seconds", aggfunc="median")
    spread = d.pivot_table(index=keys, columns="pathway", values="seconds", aggfunc=iqr).add_suffix("_iqr")
    extra = d.groupby(keys)[["events", "variants", "traces"]].median()
    unaligned = d[d.pathway == "alignment"].groupby(keys).unaligned.max().rename("unaligned")
    table = pd.concat([med, spread, extra, unaligned], axis=1)
    table["policy_share"] = table.policy / (table.policy + table.alignment)
    table["policy_events_per_s"] = table.events / table.policy
    return table


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    groupings = {"original": [["category"], ["activities"], ["noise"], ["model"]],
                 "models": [["category", "activities"]],
                 "traces": [["model", "traces"]]}
    for part, keys_list in groupings.items():
        path = RESULTS / f"runtime_{part}.csv"
        if not path.exists():
            continue
        d = pd.read_csv(path)
        for keys in keys_list:
            table = summarize(d, keys)
            table.round(5).to_csv(OUT / f"runtime_{part}_by_{'_'.join(keys)}.csv")
            print(f"== {part} by {keys}\n", table.round(4).to_string())
    path = RESULTS / "runtime_parallel.csv"
    if path.exists():
        d = pd.read_csv(path)
        med = d.groupby(["model", "pathway", "workers"]).seconds.median().unstack("workers")
        speedup = med.rdiv(med[1], axis=0)
        speedup.round(3).to_csv(OUT / "runtime_parallel_speedup.csv")
        print("== parallel speedup\n", speedup.round(2).to_string(), "\n", med.round(3).to_string())


if __name__ == "__main__":
    main()
