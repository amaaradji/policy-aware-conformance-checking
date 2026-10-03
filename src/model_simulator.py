import os
from pm4py.objects.bpmn.importer import importer as bpmn_importer
from pm4py.objects.conversion.bpmn import converter as bpmn_converter
from pm4py.algo.simulation.playout.petri_net import algorithm as pn_playout
from pm4py.objects.log.exporter.xes import exporter as xes_exporter

MODEL_FILE = "bpic2013.bpmn"
N_TRACES = 20000

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
MODEL_PATH = os.path.join(CURRENT_DIR, MODEL_FILE)
LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "event_log.xes")

print("Loading BPMN model:", MODEL_PATH)
bpmn_model = bpmn_importer.apply(MODEL_PATH)

print("Converting BPMN to Petri net...")
net, initial_marking, final_marking = bpmn_converter.apply(bpmn_model)

print("Simulating traces...")
parameters = {pn_playout.Variants.BASIC_PLAYOUT.value.Parameters.NO_TRACES: N_TRACES}
event_log = pn_playout.apply(net, initial_marking, final_marking, variant=pn_playout.Variants.BASIC_PLAYOUT, parameters=parameters)

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
print("Saving event log to:", LOG_PATH)
xes_exporter.apply(event_log, LOG_PATH)
print("Done. Generated traces:", len(event_log))