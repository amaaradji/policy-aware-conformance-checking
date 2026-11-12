"""BPI Challenge 2019: goods receipts of the items that break a documented rule (Section 5.7).

Per item category: the items breaking the rule and, among them, the items with no Record Goods Receipt event at all,
the items whose first Record Goods Receipt comes after their first Clear Invoice, and the items with no Record Goods
Receipt but a Cancel Goods Receipt before their first Clear Invoice. Events are taken in the order of the event table,
as in exp_bpic2019.py.
Outputs eval/results/tables/bpic2019_violations.csv.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import DATA, RESULTS  # noqa: E402

RECORD, CANCEL, CLEAR = "Record Goods Receipt", "Cancel Goods Receipt", "Clear Invoice"


def main():
    cases = pd.read_csv(RESULTS / "bpic2019_cases.csv.gz")
    breaking = cases[cases.violation == 1]
    log = pd.read_csv(DATA / "bpic2019" / "bpic2019_events.csv.gz", dtype=str, usecols=["case", "activity"])
    log = log[log.case.isin(set(breaking.case)) & log.activity.isin([RECORD, CANCEL, CLEAR])]
    first = log.reset_index().groupby(["case", "activity"])["index"].min().unstack()
    first = first.reindex(index=breaking.case, columns=[RECORD, CANCEL, CLEAR])
    record, cancel, clear = first[RECORD], first[CANCEL], first[CLEAR]
    items = breaking.set_index("case").assign(
        no_goods_receipt=record.isna(),
        goods_receipt_after_clearing=record.notna() & (record > clear),
        cancelled_receipt_before_clearing=record.isna() & cancel.notna() & (cancel < clear))
    table = items.groupby("category").agg(
        items_breaking_rule=("violation", "size"), no_goods_receipt=("no_goods_receipt", "sum"),
        goods_receipt_after_clearing=("goods_receipt_after_clearing", "sum"),
        cancelled_receipt_before_clearing=("cancelled_receipt_before_clearing", "sum"))
    out = RESULTS / "tables"
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "bpic2019_violations.csv")
    print(table.to_string())


if __name__ == "__main__":
    main()
