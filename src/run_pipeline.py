"""
run_pipeline.py — OPTIMIZED with detailed timing
"""

import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPTS = [
    ("1. model_simulator.py", "model_simulator.py", []),
    ("2. enrich_event.py", "enrich_event.py", []),
    ("3. inject_deviations.py", "inject_deviations.py", ["--noise", "0.1"]),
    ("4. policy_check.py", "policy_check.py", []),
    ("5. agg_event.py", "agg_event.py", []),
    ("6. deviate_flow.py", "deviate_flow.py", ["--noise", "0.1"]),
    ("7. conf_check.py", "conf_check.py", []),
]

# Optional: Skip LLM for faster runs
SKIP_LLM = os.environ.get("SKIP_LLM", "false").lower() == "true"
if SKIP_LLM:
    os.environ["SKIP_LLM"] = "true"
    print("⚠️  SKIP_LLM mode: LLM explanation will be skipped")

# ============================================================================

def print_header():
    print("\n" + "="*70)
    print(" " * 22 + "PIPELINE EXECUTION")
    print("="*70)
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")


def print_footer(total_time, timings, failed=False, failed_script=None):
    print("\n" + "="*70)
    if failed:
        print(" " * 22 + "EXECUTION FAILED!")
        print("="*70)
        print(f"Failed at: {failed_script}")
    else:
        print(" " * 22 + "EXECUTION COMPLETE!")
        print("="*70)
        print(f"End time:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total time: {total_time:.2f} seconds")
        print("-"*70)
        print("Detailed timings:")
        for name, duration in timings:
            bar = "█" * int(duration / total_time * 50) if total_time > 0 else ""
            print(f"  {name:25} {duration:6.2f}s  {bar}")
    print("="*70 + "\n")


def run_script(script_path, args):
    cmd = [sys.executable, str(script_path)] + args
    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        exit_code = result.returncode
    except Exception as e:
        print(f"Error: {e}")
        exit_code = 1
    duration = time.time() - start
    return exit_code, duration


def main():
    src_dir = Path(__file__).parent.absolute()
    
    if not src_dir.exists():
        print(f"ERROR: src directory not found")
        sys.exit(1)
    
    print_header()
    
    total_start = time.time()
    timings = []
    failed = False
    failed_script = None
    
    for idx, (name, script, args) in enumerate(SCRIPTS, 1):
        script_path = src_dir / script
        
        if not script_path.exists():
            print(f"\n❌ ERROR: Script not found: {script_path}")
            failed = True
            failed_script = script
            break
        
        print(f"\n[{idx}/{len(SCRIPTS)}] Running {name}...")
        
        exit_code, duration = run_script(script_path, args)
        timings.append((name, duration))
        
        if exit_code == 0:
            print(f"   ✅ {duration:.2f}s")
        else:
            print(f"   ❌ Failed (exit {exit_code}) after {duration:.2f}s")
            failed = True
            failed_script = name
            break
    
    total_time = time.time() - total_start
    print_footer(total_time, timings, failed, failed_script)
    
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()