"""SQL queries and statistical analysis behind the dashboards and the report."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from quality_lab.config import BATCH_SIZE, DB_PATH, FAILURE_MODES, GRADES, PROCESS_VARIABLES

ANY_MODE = "ANY"


@dataclass(frozen=True)
class Filters:
    grades: tuple[str, ...] = tuple(GRADES)
    batch_min: int = 1
    batch_max: int = 10_000

    def where(self) -> tuple[str, list]:
        """Build a parameterized WHERE clause, keeping user input out of the SQL text."""
        grades = [g for g in self.grades if g in GRADES]
        if not grades:
            return "1 = 0", []
        marks = ", ".join("?" for _ in grades)
        return f"grade IN ({marks}) AND batch BETWEEN ? AND ?", [*grades, self.batch_min, self.batch_max]


def _query(sql: str, params: list | None = None, db_path: Path = DB_PATH) -> pd.DataFrame:
    with closing(sqlite3.connect(db_path)) as connection:
        return pd.read_sql_query(sql, connection, params=params or [])


def target_column(mode: str) -> str:
    if mode == ANY_MODE:
        return "machine_failure"
    if mode in FAILURE_MODES:
        return mode.lower()
    raise ValueError(f"Unknown failure mode: {mode}")


def _check_variable(variable: str) -> None:
    if variable not in PROCESS_VARIABLES:
        raise ValueError(f"Unknown process variable: {variable}")


def wilson_interval(failures, units, z: float = 1.96) -> tuple[np.ndarray, np.ndarray]:
    """95% Wilson confidence interval, which stays reliable for rare failures."""
    failures, units = np.asarray(failures, float), np.asarray(units, float)
    rate = np.divide(failures, units, out=np.zeros_like(units), where=units > 0)
    denominator = 1 + z**2 / units
    centre = (rate + z**2 / (2 * units)) / denominator
    margin = z * np.sqrt(rate * (1 - rate) / units + z**2 / (4 * units**2)) / denominator
    return np.clip(centre - margin, 0, 1), np.clip(centre + margin, 0, 1)


def load_units(filters: Filters = Filters(), db_path: Path = DB_PATH) -> pd.DataFrame:
    where, params = filters.where()
    return _query(f"SELECT * FROM production_units WHERE {where} ORDER BY udi", params, db_path)


def batch_trend(filters: Filters = Filters(), db_path: Path = DB_PATH) -> pd.DataFrame:
    """Failure rate per batch with p-chart control limits (3 sigma)."""
    where, params = filters.where()
    sql = f"""
        SELECT batch, COUNT(*) AS units, SUM(machine_failure) AS failures,
               MIN(udi) AS first_udi, MAX(udi) AS last_udi
        FROM production_units WHERE {where}
        GROUP BY batch ORDER BY batch
    """
    trend = _query(sql, params, db_path)
    if trend.empty:
        return trend.assign(rate=[], centre=[], ucl=[], lcl=[], out_of_control=[])

    centre = trend["failures"].sum() / trend["units"].sum()
    sigma = np.sqrt(centre * (1 - centre) / trend["units"])
    trend["rate"] = trend["failures"] / trend["units"]
    trend["centre"] = centre
    trend["ucl"] = np.minimum(centre + 3 * sigma, 1)
    trend["lcl"] = np.maximum(centre - 3 * sigma, 0)
    trend["out_of_control"] = (trend["rate"] > trend["ucl"]) | (trend["rate"] < trend["lcl"])
    return trend


def mode_pareto(filters: Filters = Filters(), db_path: Path = DB_PATH) -> pd.DataFrame:
    """Failure events by mode, ranked, with the cumulative share."""
    where, params = filters.where()
    sql = f"""
        SELECT e.mode, COUNT(*) AS events
        FROM failure_events AS e
        JOIN production_units AS u ON u.udi = e.udi
        WHERE {where}
        GROUP BY e.mode ORDER BY events DESC, e.mode
    """
    pareto = _query(sql, params, db_path)
    pareto["label"] = pareto["mode"].map(FAILURE_MODES)
    pareto["share"] = pareto["events"] / pareto["events"].sum() if len(pareto) else []
    pareto["cumulative_share"] = pareto["share"].cumsum() if len(pareto) else []
    return pareto


def grade_rates(filters: Filters = Filters(), db_path: Path = DB_PATH) -> pd.DataFrame:
    where, params = filters.where()
    sql = f"""
        SELECT grade, COUNT(*) AS units, SUM(machine_failure) AS failures
        FROM production_units WHERE {where} GROUP BY grade
    """
    rates = _query(sql, params, db_path)
    if rates.empty:
        return rates.assign(label=[], rate=[], ci_low=[], ci_high=[])
    rates["label"] = rates["grade"].map(GRADES)
    rates["rate"] = rates["failures"] / rates["units"]
    rates["ci_low"], rates["ci_high"] = wilson_interval(rates["failures"], rates["units"])
    order = {grade: i for i, grade in enumerate(GRADES)}
    return rates.sort_values("grade", key=lambda s: s.map(order)).reset_index(drop=True)


def kpis(filters: Filters = Filters(), db_path: Path = DB_PATH) -> dict:
    where, params = filters.where()
    row = _query(
        f"SELECT COUNT(*) AS units, COALESCE(SUM(machine_failure), 0) AS failures FROM production_units WHERE {where}",
        params,
        db_path,
    ).iloc[0]
    units, failures = int(row["units"]), int(row["failures"])
    pareto = mode_pareto(filters, db_path)
    trend = batch_trend(filters, db_path)
    return {
        "units": units,
        "failures": failures,
        "failure_rate": failures / units if units else 0.0,
        "first_pass_yield": 1 - failures / units if units else 0.0,
        "top_mode": pareto["label"].iloc[0] if len(pareto) else "None",
        "out_of_control_batches": int(trend["out_of_control"].sum()) if len(trend) else 0,
        "batches": len(trend),
    }


def _bin_labels(series: pd.Series, variable: str, bins: int) -> pd.Series:
    """Split a variable into equal-count bins with readable labels."""
    decimals = PROCESS_VARIABLES[variable].decimals
    binned = pd.qcut(series, q=bins, duplicates="drop")
    labels = [
        f"{(series.min() if position == 0 else interval.left):.{decimals}f} to {interval.right:.{decimals}f}"
        for position, interval in enumerate(binned.cat.categories)
    ]
    return binned.cat.rename_categories(labels)


def driver_curve(
    variable: str,
    mode: str = ANY_MODE,
    bins: int = 8,
    filters: Filters = Filters(),
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Failure rate across equal-count bins of one process variable."""
    _check_variable(variable)
    target = target_column(mode)
    units = load_units(filters, db_path)
    if units.empty:
        return pd.DataFrame(columns=["bin", "units", "failures", "rate", "ci_low", "ci_high"])
    grouped = units.groupby(_bin_labels(units[variable], variable, bins), observed=True)[target].agg(["count", "sum"])
    curve = grouped.rename(columns={"count": "units", "sum": "failures"}).reset_index(names="bin")
    curve["bin"] = curve["bin"].astype(str)
    curve["rate"] = curve["failures"] / curve["units"]
    curve["ci_low"], curve["ci_high"] = wilson_interval(curve["failures"], curve["units"])
    return curve


def operating_window(
    x: str = "rotational_speed_rpm",
    y: str = "torque_nm",
    mode: str = ANY_MODE,
    bins: int = 8,
    min_units: int = 20,
    filters: Filters = Filters(),
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Failure rate for each combination of two binned process variables."""
    _check_variable(x)
    _check_variable(y)
    if x == y:
        raise ValueError("Choose two different process variables")
    target = target_column(mode)
    units = load_units(filters, db_path)
    columns = ["x_bin", "y_bin", "x_order", "y_order", "units", "failures", "rate"]
    if units.empty:
        return pd.DataFrame(columns=columns)

    x_bins, y_bins = _bin_labels(units[x], x, bins), _bin_labels(units[y], y, bins)
    grouped = units.groupby([x_bins.rename("x_bin"), y_bins.rename("y_bin")], observed=True)[target]
    window = grouped.agg(units="count", failures="sum").reset_index()
    window["x_order"] = window["x_bin"].cat.codes
    window["y_order"] = window["y_bin"].cat.codes
    window[["x_bin", "y_bin"]] = window[["x_bin", "y_bin"]].astype(str)
    window["rate"] = (window["failures"] / window["units"]).where(window["units"] >= min_units)
    return window[columns]


def risk_windows(
    x: str = "rotational_speed_rpm",
    y: str = "torque_nm",
    mode: str = ANY_MODE,
    bins: int = 8,
    min_units: int = 20,
    top: int = 10,
    filters: Filters = Filters(),
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Highest-risk operating windows, compared with the baseline failure rate."""
    window = operating_window(x, y, mode, bins, min_units, filters, db_path)
    columns = ["x_bin", "y_bin", "units", "failures", "rate", "lift"]
    if window.empty:
        return pd.DataFrame(columns=columns)
    baseline = window["failures"].sum() / window["units"].sum()
    ranked = window.dropna(subset=["rate"]).query("failures > 0").copy()
    ranked["lift"] = ranked["rate"] / baseline if baseline else np.nan
    return ranked.sort_values(["rate", "failures"], ascending=False).head(top)[columns].reset_index(drop=True)


def spike_drivers(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Compare the failure-mode mix of out-of-control batches with all other batches."""
    trend = batch_trend(db_path=db_path)
    flagged = set(trend.loc[trend["out_of_control"], "batch"])
    events = _query(
        "SELECT e.mode, u.batch FROM failure_events AS e JOIN production_units AS u ON u.udi = e.udi",
        db_path=db_path,
    )
    events["group"] = events["batch"].isin(flagged).map({True: "flagged", False: "other"})
    mix = events.groupby(["mode", "group"]).size().unstack(fill_value=0).reindex(columns=["flagged", "other"], fill_value=0)
    shares = mix / mix.sum()
    result = mix.join(shares, rsuffix="_share").sort_values("flagged", ascending=False).reset_index()
    result["label"] = result["mode"].map(FAILURE_MODES)
    return result


def data_quality(db_path: Path = DB_PATH) -> pd.DataFrame:
    return _query("SELECT name, severity, affected, detail FROM data_quality_checks", db_path=db_path)


def key_findings(db_path: Path = DB_PATH) -> list[str]:
    """Plain-language findings, calculated from the data on every run."""
    summary = kpis(db_path=db_path)
    pareto = mode_pareto(db_path=db_path)
    trend = batch_trend(db_path=db_path)
    grades = grade_rates(db_path=db_path).set_index("grade")
    window = risk_windows(db_path=db_path, top=1).iloc[0]
    checks = data_quality(db_path).set_index("name")["affected"]
    top_three = pareto.head(3)
    driver = spike_drivers(db_path).iloc[0]
    units = load_units(db_path=db_path)
    in_spike = units["batch"].isin(trend.loc[trend["out_of_control"], "batch"])
    flagged = trend.loc[trend["out_of_control"], "batch"].astype(str).tolist()

    return [
        f"{summary['failures']} of {summary['units']:,} units failed, a failure rate of {summary['failure_rate']:.2%}.",
        f"{', '.join(top_three['label'])} account for {top_three['share'].sum():.0%} of "
        f"{int(pareto['events'].sum())} recorded failure events.",
        f"{len(flagged)} of {len(trend)} batches of {BATCH_SIZE} units exceed the upper control limit"
        + (f" (batch {', '.join(flagged)})." if flagged else "."),
        f"{driver['label']} causes {driver['flagged_share']:.0%} of failure events in those batches, "
        f"against {driver['other_share']:.0%} in all other batches. Their average process-air temperature gap is "
        f"{units.loc[in_spike, 'temp_delta_k'].mean():.1f} K, against {units.loc[~in_spike, 'temp_delta_k'].mean():.1f} K elsewhere.",
        f"Low-grade units fail at {grades.loc['L', 'rate']:.2%} against {grades.loc['H', 'rate']:.2%} for high-grade units.",
        f"The riskiest operating window is {window['y_bin']} Nm at {window['x_bin']} rpm: {window['rate']:.1%} failure rate, "
        f"{window['lift']:.1f} times the baseline ({int(window['failures'])} of {int(window['units'])} units).",
        f"Data checks found {checks['failure_without_mode']} failures with no recorded mode and "
        f"{checks['mode_without_failure']} recorded modes with no machine failure.",
    ]
