import xml.etree.ElementTree as ET

TASK_TAGS = {"task", "userTask", "serviceTask", "manualTask", "scriptTask", "sendTask",
             "receiveTask", "businessRuleTask"}


def model_profile(bpmn_path):
    """Structural category (Seq, AND, XOR, Comb) and number of activities of a BPMN model."""
    kinds = [e.tag.split("}")[-1] for e in ET.parse(bpmn_path).getroot().iter()]
    n_xor, n_and = kinds.count("exclusiveGateway"), kinds.count("parallelGateway")
    category = "Comb" if n_xor and n_and else "XOR" if n_xor else "AND" if n_and else "Seq"
    return category, sum(k in TASK_TAGS for k in kinds)
