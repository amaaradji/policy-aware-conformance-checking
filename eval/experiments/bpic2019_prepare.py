"""Stream the BPI Challenge 2019 log (728 MB XES) into a compact table, in constant memory.

One row per event: case id, item category, GR-based invoice verification flag, goods receipt
flag, activity, user, timestamp. Output: data/bpic2019/bpic2019_events.csv.gz
"""
import csv
import gzip
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval.paths import DATA  # noqa: E402

SOURCE = DATA / "bpic2019" / "BPI_Challenge_2019.xes"
TARGET = DATA / "bpic2019" / "bpic2019_events.csv.gz"
CASE_KEYS = {"concept:name": "case", "Item Category": "category", "GR-Based Inv. Verif.": "gr_based_iv",
             "Goods Receipt": "goods_receipt"}


def attributes(element):
    return {child.get("key"): child.get("value") for child in element if child.tag.split("}")[-1] != "event"}


def main():
    cases = events = 0
    with gzip.open(TARGET, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["case", "category", "gr_based_iv", "goods_receipt", "activity", "user", "timestamp"])
        for _, trace in etree.iterparse(str(SOURCE), events=("end",), tag="{*}trace", huge_tree=True):
            case = {CASE_KEYS[k]: v for k, v in attributes(trace).items() if k in CASE_KEYS}
            for event in trace.iterfind("{*}event"):
                a = attributes(event)
                writer.writerow([case["case"], case["category"], case["gr_based_iv"], case["goods_receipt"],
                                 a.get("concept:name"), a.get("org:resource"), a.get("time:timestamp")])
                events += 1
            cases += 1
            trace.clear()
            while trace.getprevious() is not None:
                del trace.getparent()[0]
            if cases % 50000 == 0:
                print(cases, "cases", events, "events", flush=True)
    print(f"{cases} cases, {events} events -> {TARGET}")


if __name__ == "__main__":
    main()
