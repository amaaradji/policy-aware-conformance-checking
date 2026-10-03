"""
conf_check.py — OPTIMIZED (removes duplicate file reads)
"""

import os
from pm4py.objects.log.importer.xes import importer as xes_importer
from pm4py.objects.bpmn.importer import importer as bpmn_importer
from pm4py.algo.conformance.alignments.petri_net import algorithm as alignments
from pm4py.objects.conversion.bpmn import converter as bpmn_converter

# ── Paths ──────────────────────────────────────────────────────────────────
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

AGG_LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "agg_log.xes")
ENRICHED_LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "enriched_log.xes")
MODEL_PATH = os.path.join(SRC_DIR, "bpic2013.bpmn")


def _event_compliance_score(event: dict) -> float:
    """Get compliance_score directly from event"""
    return event.get("compliance_score", 1.0)


def _read_file_content(filepath: str, max_chars: int = 15000) -> str:
    """Read file content efficiently"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            # Read in chunks for large files
            content = f.read(max_chars + 1000)
            if len(content) > max_chars:
                content = content[:max_chars] + "\n... [truncated for length]"
            return content
    except Exception as e:
        return f"[Error reading file: {e}]"


def run_conformance():
    print("Loading BPMN model and aggregated log...")
    bpmn_graph = bpmn_importer.apply(MODEL_PATH)
    net, im, fm = bpmn_converter.apply(bpmn_graph)
    log = xes_importer.apply(AGG_LOG_PATH)
    
    # Read files only once
    print("Reading file contents for LLM context...")
    bpmn_content = _read_file_content(MODEL_PATH)
    agg_log_content = _read_file_content(AGG_LOG_PATH)
    enriched_log_content = _read_file_content(ENRICHED_LOG_PATH)
    
    print("Calculating alignments...")
    aligned_traces = alignments.apply_log(log, net, im, fm)
    
    total_trace_scores = []
    report_lines = []
    
    print("\n--- Conformance Report ---")
    
    for i, trace in enumerate(log):
        # Fast compliance calculation
        total = 0.0
        for e in trace:
            total += _event_compliance_score(e)
        avg_compliance = total / len(trace) if trace else 0.0
        
        fitness_score = aligned_traces[i]["fitness"]
        trace_score = 0.7 * fitness_score + 0.3 * avg_compliance
        total_trace_scores.append(trace_score)
        
        trace_id = trace.attributes.get("concept:name", str(i))
        line = f"Trace {trace_id}: Conformance: {fitness_score:.3f}, Compliance: {avg_compliance:.3f}, Total: {trace_score:.3f}"
        print(line)
        report_lines.append(line)
    
    overall_score = sum(total_trace_scores) / len(total_trace_scores)
    overall_line = f"Overall score: {overall_score:.4f}"
    print("-" * 26)
    print(overall_line)
    report_lines.append(overall_line)
    
    try:
        from llm_explainer import explain_report_with_context
        full_report = "\n".join(report_lines)
        print("\n--- LLM Explanation ---")
        explanation = explain_report_with_context(
            report_text=full_report,
            bpmn_content=bpmn_content,
            agg_log_content=agg_log_content,
            enriched_log_content=enriched_log_content
        )
        print(explanation)
    except EnvironmentError as e:
        print(f"\n[llm_explainer] Skipped — no Azure credentials configured: {e}")
    except Exception as e:
        print(f"\n[llm_explainer] Error: {e}")


if __name__ == "__main__":
    run_conformance()