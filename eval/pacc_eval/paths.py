from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
CONFIG = REPO / "config"
POLICIES = REPO / "policies"
MODELS = REPO / "models"
EVAL = REPO / "eval"
RESULTS = EVAL / "results"
FIGURES = EVAL / "figures"
DATA = REPO.parent / "data"
