"""ETL pipeline: extract the raw CSV, validate it, engineer features and load SQLite."""
from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from quality_lab.config import BATCH_SIZE, DB_PATH, FAILURE_MODES, GRADES, RAW_CSV

COLUMN_MAP = {
    "UDI": "udi",
    "Product ID": "product_id",
    "Type": "grade",
    "Air temperature [K]": "air_temp_k",
    "Process temperature [K]": "process_temp_k",
    "Rotational speed [rpm]": "rotational_speed_rpm",
    "Torque [Nm]": "torque_nm",
    "Tool wear [min]": "tool_wear_min",
    "Machine failure": "machine_failure",
    **{mode: mode.lower() for mode in FAILURE_MODES},
}

SCHEMA = """
CREATE TABLE production_units (
    udi                  INTEGER PRIMARY KEY,
    product_id           TEXT    NOT NULL,
    grade                TEXT    NOT NULL CHECK (grade IN ('L', 'M', 'H')),
    batch                INTEGER NOT NULL,
    air_temp_k           REAL    NOT NULL,
    process_temp_k       REAL    NOT NULL,
    temp_delta_k         REAL    NOT NULL,
    rotational_speed_rpm INTEGER NOT NULL,
    torque_nm            REAL    NOT NULL,
    tool_wear_min        INTEGER NOT NULL,
    power_w              REAL    NOT NULL,
    strain_min_nm        REAL    NOT NULL,
    machine_failure      INTEGER NOT NULL,
    twf INTEGER NOT NULL, hdf INTEGER NOT NULL, pwf INTEGER NOT NULL,
    osf INTEGER NOT NULL, rnf INTEGER NOT NULL,
    mode_count           INTEGER NOT NULL
);
CREATE TABLE failure_events (
    udi  INTEGER NOT NULL REFERENCES production_units (udi),
    mode TEXT    NOT NULL,
    PRIMARY KEY (udi, mode)
);
CREATE TABLE data_quality_checks (
    name     TEXT PRIMARY KEY,
    severity TEXT    NOT NULL,
    affected INTEGER NOT NULL,
    detail   TEXT    NOT NULL
);
CREATE INDEX idx_units_batch_grade ON production_units (batch, grade);
CREATE INDEX idx_events_mode ON failure_events (mode);
"""


class DataValidationError(ValueError):
    """Raised when the raw data breaks a rule that makes the analysis unsafe."""


@dataclass(frozen=True)
class Check:
    name: str
    severity: str  # "error" stops the pipeline, "warning" is recorded and reported
    affected: int
    detail: str


def extract(csv_path: Path = RAW_CSV) -> pd.DataFrame:
    return pd.read_csv(csv_path)


def validate(raw: pd.DataFrame) -> list[Check]:
    """Run data-quality rules on the raw extract and return every result."""
    missing = sorted(set(COLUMN_MAP) - set(raw.columns))
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")

    modes = list(FAILURE_MODES)
    mode_count = raw[modes].sum(axis=1)
    failed = raw["Machine failure"] == 1
    grade_ok = raw["Type"].isin(GRADES)
    prefix_ok = raw["Product ID"].astype(str).str[0] == raw["Type"]
    flags_ok = raw[["Machine failure", *modes]].isin([0, 1]).all(axis=1)
    sensors_ok = (
        raw["Air temperature [K]"].between(250, 350)
        & raw["Process temperature [K]"].between(250, 350)
        & (raw["Rotational speed [rpm]"] > 0)
        & (raw["Torque [Nm]"] > 0)
        & (raw["Tool wear [min]"] >= 0)
    )

    return [
        Check("unique_unit_ids", "error", int(raw["UDI"].duplicated().sum()), "Each UDI appears once"),
        Check("no_missing_values", "error", int(raw[list(COLUMN_MAP)].isna().any(axis=1).sum()), "All required fields are filled"),
        Check("valid_grades", "error", int((~grade_ok).sum()), "Grade is L, M or H"),
        Check("product_id_matches_grade", "error", int((~prefix_ok).sum()), "Product ID prefix equals the grade"),
        Check("binary_failure_flags", "error", int((~flags_ok).sum()), "Failure flags are 0 or 1"),
        Check("sensor_ranges", "error", int((~sensors_ok).sum()), "Sensor readings are physically plausible"),
        Check("failure_without_mode", "warning", int((failed & (mode_count == 0)).sum()), "Machine failure recorded with no failure mode"),
        Check("mode_without_failure", "warning", int((~failed & (mode_count > 0)).sum()), "Failure mode recorded with no machine failure"),
        Check("multiple_failure_modes", "warning", int((mode_count > 1).sum()), "Unit records more than one failure mode"),
    ]


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    """Rename columns and add the engineering features used in the analysis."""
    units = raw[list(COLUMN_MAP)].rename(columns=COLUMN_MAP).copy()
    mode_columns = [mode.lower() for mode in FAILURE_MODES]
    units["batch"] = (units["udi"] - 1) // BATCH_SIZE + 1
    units["temp_delta_k"] = (units["process_temp_k"] - units["air_temp_k"]).round(2)
    units["power_w"] = (units["torque_nm"] * units["rotational_speed_rpm"] * 2 * math.pi / 60).round(1)
    units["strain_min_nm"] = (units["tool_wear_min"] * units["torque_nm"]).round(1)
    units["mode_count"] = units[mode_columns].sum(axis=1)
    return units


def failure_events(units: pd.DataFrame) -> pd.DataFrame:
    """Reshape the wide failure flags into one row per unit and failure mode."""
    long = units.melt(id_vars="udi", value_vars=[m.lower() for m in FAILURE_MODES], var_name="mode", value_name="flag")
    events = long.loc[long["flag"] == 1, ["udi", "mode"]].copy()
    events["mode"] = events["mode"].str.upper()
    return events.sort_values(["udi", "mode"]).reset_index(drop=True)


def load(units: pd.DataFrame, checks: list[Check], db_path: Path = DB_PATH) -> None:
    """Write to a temporary database first so a failed run never damages the last good one."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_suffix(".tmp")
    temp_path.unlink(missing_ok=True)

    connection = sqlite3.connect(temp_path)
    try:
        connection.executescript(SCHEMA)
        units.to_sql("production_units", connection, if_exists="append", index=False)
        failure_events(units).to_sql("failure_events", connection, if_exists="append", index=False)
        pd.DataFrame([asdict(c) for c in checks]).to_sql("data_quality_checks", connection, if_exists="append", index=False)
        connection.commit()
    finally:
        connection.close()
    os.replace(temp_path, db_path)


def run_etl(csv_path: Path = RAW_CSV, db_path: Path = DB_PATH) -> dict:
    raw = extract(csv_path)
    checks = validate(raw)
    blocking = [c for c in checks if c.severity == "error" and c.affected]
    if blocking:
        details = "; ".join(f"{c.name}: {c.affected} rows" for c in blocking)
        raise DataValidationError(f"Validation failed: {details}")

    units = transform(raw)
    load(units, checks, db_path)
    return {
        "units": len(units),
        "batches": int(units["batch"].nunique()),
        "failures": int(units["machine_failure"].sum()),
        "failure_events": len(failure_events(units)),
        "warnings": {c.name: c.affected for c in checks if c.severity == "warning"},
    }
