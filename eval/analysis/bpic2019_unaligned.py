"""BPI Challenge 2019: held-out cases whose variant was not aligned within the time budgets (Section 5.7).

Per item category: held-out cases and variants, unaligned cases and variants, the largest number of held-out cases of
an unaligned variant, the mean number of events of unaligned and aligned cases, the mean fitness of the aligned cases,
and the mean fitness over all held-out cases if every unaligned case had a fitness of 0 or of 1.
Variants are the activity sequences of the cases in the order of the event table, as in exp_bpic2019.py.
Outputs eval/results/tables/bpic2019_unaligned.csv.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import DATA, RESULTS  # noqa: E402


def main():
    cases = pd.read_csv(RESULTS / "bpic2019_cases.csv.gz")
    test = cases[cases.split == "test"]
    log = pd.read_csv(DATA / "bpic2019" / "bpic2019_events.csv.gz", dtype=str, usecols=["case", "activity"])
    test = test.assign(variant=test.case.map(log.groupby("case", sort=False).activity.agg(tuple)))
    rows = []
    for category, part in test.groupby("category"):
        cases_of_variant = part.variant.map(part.variant.value_counts())
        aligned, unaligned = part[part.f.notna()], part[part.f.isna()]
        share = len(aligned) / len(part)
        rows.append({"category": category, "held_out_cases": len(part), "variants": part.variant.nunique(),
                     "unaligned_cases": len(unaligned), "unaligned_variants": unaligned.variant.nunique(),
                     "max_cases_per_unaligned_variant": cases_of_variant[part.f.isna()].max() if len(unaligned) else 0,
                     "events_unaligned": unaligned.events.mean(), "events_aligned": aligned.events.mean(),
                     "f_aligned": aligned.f.mean(), "f_all_if_unaligned_0": aligned.f.mean() * share,
                     "f_all_if_unaligned_1": aligned.f.mean() * share + 1 - share})
    table = pd.DataFrame(rows).set_index("category").round(4)
    out = RESULTS / "tables"
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "bpic2019_unaligned.csv")
    print(table.to_string())


if __name__ == "__main__":
    main()
