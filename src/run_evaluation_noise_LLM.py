"""
run_evaluation_noise_LLM.py
───────────────────────────
Runs bpic2013.bpmn / activity_info_bpic2013.json at multiple noise levels in three modes:
  Alignment CC, Policy-aware CC, and PACC with LLM.

This version no longer loops over processModel_1..processModel_20.
"""

import os
import sys
import re
import json
import time
import subprocess
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional


# ============================================================================
# CONFIGURATION
# ============================================================================

SRC_DIR = Path(__file__).parent.absolute()

TARGET_BPMN = "bpic2013.bpmn"
TARGET_ACTIVITY_INFO = "activity_info_bpic2013.json"

PIPELINE_SCRIPTS = [
    "enrich_event.py",
    "inject_deviations.py",
    "policy_check.py",
    "agg_event.py",
    "conf_check.py",
]

BASELINE_SKIP_STEPS = {1, 2}  # inject_deviations.py and policy_check.py
LLM_SCRIPT = "conf_check.py"

_AZURE_ENV_KEYS = (
    "AZURE_OPENAI_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
)


def _build_subprocess_env(use_llm: bool) -> dict:
    """Return subprocess env. Azure credentials are removed when LLM is disabled."""
    env = os.environ.copy()
    if not use_llm:
        for key in _AZURE_ENV_KEYS:
            env.pop(key, None)
    return env


def ensure_target_files() -> None:
    """Make sure the single target BPMN and activity-info files exist."""
    bpmn_path = SRC_DIR / TARGET_BPMN
    activity_info_path = SRC_DIR / TARGET_ACTIVITY_INFO

    if not bpmn_path.exists():
        raise FileNotFoundError(f"Required BPMN file not found: {bpmn_path}")
    if not activity_info_path.exists():
        raise FileNotFoundError(f"Required activity info file not found: {activity_info_path}")


def normalize_pipeline_script_references() -> None:
    """
    Force all pipeline scripts to use bpic2013.bpmn and activity_info_bpic2013.json.
    This replaces old processModel_i/activity_info_i references, without looping over models.
    """
    model_pattern = re.compile(r"processModel_\d+\.bpmn")
    activity_pattern = re.compile(r"activity_info_\d+\.json")

    for script_name in PIPELINE_SCRIPTS:
        script_path = SRC_DIR / script_name
        if not script_path.exists():
            continue

        text = script_path.read_text(encoding="utf-8")
        new_text = model_pattern.sub(TARGET_BPMN, text)
        new_text = activity_pattern.sub(TARGET_ACTIVITY_INFO, new_text)

        if new_text != text:
            script_path.write_text(new_text, encoding="utf-8")
            print(f"  Patched references in {script_name} -> {TARGET_BPMN}, {TARGET_ACTIVITY_INFO}")


def count_activities(activity_info_path: Path) -> int:
    """Count activities from activity_info_bpic2013.json."""
    with open(activity_info_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return len(data.get("activities", []))
    return len(data)


def run_pipeline(mode: str, noise: float = 0.1) -> Optional[float]:
    """
    Run the pipeline once using bpic2013.bpmn only.

    Modes:
      alignment    -> skip inject_deviations.py and policy_check.py, no LLM
      policy_aware -> all steps, no LLM
      pacc_llm     -> all steps, LLM enabled
      normal       -> all steps, no LLM
      baseline     -> skip inject_deviations.py and policy_check.py, no LLM
    """
    use_llm = (mode == "pacc_llm")
    subprocess_env = _build_subprocess_env(use_llm=use_llm)
    start = time.time()
    noise_str = str(noise)

    for step_idx, script_name in enumerate(PIPELINE_SCRIPTS):
        if mode in {"alignment", "baseline"} and step_idx in BASELINE_SKIP_STEPS:
            print(f"      [SKIP] {script_name}  ({mode} mode)")
            continue

        script_path = SRC_DIR / script_name
        if not script_path.exists():
            print(f"      [ERROR] Not found: {script_path}")
            return None

        args = []
        if script_name in {"inject_deviations.py", "deviate_flow.py"}:
            args = ["--noise", noise_str]

        if script_name == LLM_SCRIPT and not use_llm:
            args.append("--no-llm")

        print(f"      [{step_idx + 1}/5] {script_name} ...", end="", flush=True)
        t0 = time.time()
        result = subprocess.run(
            [sys.executable, str(script_path)] + args,
            text=True,
            env=subprocess_env,
        )
        elapsed = time.time() - t0

        if result.returncode != 0:
            print(f" ❌ FAILED (exit {result.returncode}) after {elapsed:.1f}s")
            return None

        print(f" ✓ {elapsed:.1f}s")

    return time.time() - start


NOISE_LEVELS = [0.0, 0.05, 0.1, 0.2, 0.3]
PLOT_PATH = SRC_DIR / "bpic_eval_noise_LLM.png"


def main() -> None:
    eval_start = time.time()
    print("\n" + "=" * 70)
    print("       NOISE EVALUATION  (bpic2013 only × noise levels × 3 modes)")
    print("=" * 70)
    print(f"Start time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Source dir   : {SRC_DIR}")
    print(f"Model        : {TARGET_BPMN}")
    print(f"Noise levels : {NOISE_LEVELS}")
    print("=" * 70 + "\n")

    ensure_target_files()
    normalize_pipeline_script_references()

    results = {"alignment": [], "policy_aware": [], "pacc_llm": [], "noise": []}

    for noise in NOISE_LEVELS:
        noise_pct = f"{int(noise * 100)}%"
        print(f"\n{'#' * 70}")
        print(f"#  NOISE LEVEL: {noise_pct}  ({noise}) | model={TARGET_BPMN}")
        print(f"{'#' * 70}")

        alignment_t = run_pipeline("alignment", noise=noise)
        if alignment_t is None:
            continue

        policy_aware_t = run_pipeline("policy_aware", noise=noise)
        if policy_aware_t is None:
            continue

        pacc_llm_t = run_pipeline("pacc_llm", noise=noise)
        if pacc_llm_t is None:
            continue

        results["noise"].append(noise)
        results["alignment"].append(alignment_t)
        results["policy_aware"].append(policy_aware_t)
        results["pacc_llm"].append(pacc_llm_t)
        print(f"Summary {noise_pct} → alignment={alignment_t:.2f}s, policy_aware={policy_aware_t:.2f}s, pacc_llm={pacc_llm_t:.2f}s")

    if not results["noise"]:
        print("\nNo results to plot.")
        sys.exit(0)

    inc_alignment = results["alignment"]
    inc_policy_aware = [p - a for p, a in zip(results["policy_aware"], results["alignment"])]
    inc_pacc_llm = [l - p for l, p in zip(results["pacc_llm"], results["policy_aware"])]
    bottom_policy = results["alignment"]
    bottom_llm = results["policy_aware"]

    x_labels = [f"{int(n * 100)}%" for n in results["noise"]]
    x = np.arange(len(x_labels))
    width = 0.50
    fig, ax = plt.subplots(figsize=(max(10, len(x_labels) * 1.8 + 2), 6))

    ax.bar(x, inc_alignment, width, color="#7BA7BC", label="Alignment CC")
    ax.bar(x, inc_policy_aware, width, bottom=bottom_policy, color="#E8B84B", label="Policy-aware CC")
    ax.bar(x, inc_pacc_llm, width, bottom=bottom_llm, color="#B03A2E", label="PACC with LLM")

    max_val = max(results["pacc_llm"])
    for xi, total in enumerate(results["pacc_llm"]):
        ax.text(xi, total + max_val * 0.015, f"{total:.1f}s", ha="center", va="bottom",
                fontsize=9, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", edgecolor="#B03A2E", facecolor="white", linewidth=1.4, linestyle="--"))

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=12)
    ax.set_xlabel("Noise Level", fontsize=12)
    ax.set_ylabel("Execution Time (seconds)", fontsize=12)
    ax.legend(fontsize=11, loc="upper left", framealpha=0.9)
    ax.grid(True, axis="y", linestyle="--", alpha=0.45, color="grey")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(0, max_val * 1.18)
    fig.tight_layout()
    fig.savefig(str(PLOT_PATH), dpi=150)

    total_eval_time = time.time() - eval_start
    print("\n" + "=" * 75)
    print("  NOISE EVALUATION COMPLETE")
    print("=" * 75)
    print(f"Model           : {TARGET_BPMN}")
    print(f"Total wall time : {total_eval_time:.1f}s  ({total_eval_time / 60:.1f} min)")
    print(f"📊 Plot saved to: {PLOT_PATH}")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    main()
