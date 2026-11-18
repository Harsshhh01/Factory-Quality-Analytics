"""Paths, domain labels and analysis settings shared across the project."""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data" / "raw" / "ai4i2020.csv"
DB_PATH = ROOT / "data" / "quality.db"
REPORT_PDF = ROOT / "reports" / "quality_report.pdf"

# The dataset has no timestamps, so UDI order is used as the production sequence.
BATCH_SIZE = 250

GRADES = {"L": "Low", "M": "Medium", "H": "High"}

FAILURE_MODES = {
    "TWF": "Tool wear",
    "HDF": "Heat dissipation",
    "PWF": "Power",
    "OSF": "Overstrain",
    "RNF": "Random",
}


class ProcessVariable(NamedTuple):
    label: str
    unit: str
    decimals: int


PROCESS_VARIABLES = {
    "torque_nm": ProcessVariable("Torque", "Nm", 1),
    "rotational_speed_rpm": ProcessVariable("Rotational speed", "rpm", 0),
    "tool_wear_min": ProcessVariable("Tool wear", "min", 0),
    "temp_delta_k": ProcessVariable("Process-air temperature gap", "K", 1),
    "power_w": ProcessVariable("Power", "W", 0),
    "strain_min_nm": ProcessVariable("Tool strain", "min Nm", 0),
    "air_temp_k": ProcessVariable("Air temperature", "K", 1),
    "process_temp_k": ProcessVariable("Process temperature", "K", 1),
}
