"""Tests for the ETL pipeline, the analysis and every output."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pandas as pd
import pytest

from quality_lab import analytics as qa
from quality_lab import charts
from quality_lab.config import RAW_CSV
from quality_lab.etl import DataValidationError, extract, run_etl, transform, validate


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "quality.db"
    run_etl(RAW_CSV, path)
    return path


@pytest.fixture()
def raw():
    return extract(RAW_CSV)


def check(checks, name):
    return next(c for c in checks if c.name == name)


def test_source_matches_published_dataset(raw):
    assert raw.shape == (10_000, 14)
    assert raw["Machine failure"].sum() == 339
    assert raw[["HDF", "PWF", "OSF"]].sum().tolist() == [115, 95, 98]


def test_validation_passes_clean_rules_and_reports_label_conflicts(raw):
    checks = validate(raw)
    assert all(c.affected == 0 for c in checks if c.severity == "error")
    assert check(checks, "failure_without_mode").affected == 9
    assert check(checks, "mode_without_failure").affected == 18


def test_validation_detects_duplicates_and_bad_sensors(raw):
    raw.loc[1, "UDI"] = raw.loc[0, "UDI"]
    raw.loc[2, "Torque [Nm]"] = -5
    checks = validate(raw)
    assert check(checks, "unique_unit_ids").affected == 1
    assert check(checks, "sensor_ranges").affected == 1


def test_pipeline_rejects_missing_columns(raw, tmp_path):
    bad_csv = tmp_path / "bad.csv"
    raw.drop(columns="Torque [Nm]").to_csv(bad_csv, index=False)
    with pytest.raises(DataValidationError, match="Missing required columns"):
        run_etl(bad_csv, tmp_path / "bad.db")


def test_transform_calculates_engineering_features(raw):
    units = transform(raw.head(1))
    row = units.iloc[0]
    assert row["temp_delta_k"] == pytest.approx(row["process_temp_k"] - row["air_temp_k"], abs=0.01)
    assert row["power_w"] == pytest.approx(row["torque_nm"] * row["rotational_speed_rpm"] * 2 * 3.14159265 / 60, abs=0.1)
    assert row["strain_min_nm"] == pytest.approx(row["tool_wear_min"] * row["torque_nm"], abs=0.1)
    assert row["batch"] == 1


def test_load_creates_expected_tables(db_path):
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM production_units").fetchone()[0] == 10_000
        assert connection.execute("SELECT COUNT(*) FROM failure_events").fetchone()[0] == 373
        assert connection.execute("SELECT COUNT(DISTINCT batch) FROM production_units").fetchone()[0] == 40


def test_kpis_match_source(db_path):
    summary = qa.kpis(db_path=db_path)
    assert summary["units"] == 10_000
    assert summary["failures"] == 339
    assert summary["failure_rate"] == pytest.approx(0.0339)
    assert summary["first_pass_yield"] == pytest.approx(0.9661)


def test_p_chart_limits_follow_the_formula(db_path):
    trend = qa.batch_trend(db_path=db_path)
    centre = 339 / 10_000
    expected = centre + 3 * (centre * (1 - centre) / 250) ** 0.5
    assert trend["centre"].iloc[0] == pytest.approx(centre)
    assert trend["ucl"].iloc[0] == pytest.approx(expected)
    assert (trend["lcl"] >= 0).all()


def test_pareto_is_ranked_and_reaches_one_hundred_percent(db_path):
    pareto = qa.mode_pareto(db_path=db_path)
    assert pareto["events"].is_monotonic_decreasing
    assert pareto["cumulative_share"].iloc[-1] == pytest.approx(1)


def test_filters_narrow_results_and_block_sql_injection(db_path):
    low = qa.kpis(qa.Filters(("L",)), db_path)
    assert low["units"] == 6_000
    injected = qa.kpis(qa.Filters(("L' OR '1'='1",)), db_path)
    assert injected["units"] == 0


def test_unknown_variable_and_mode_are_rejected(db_path):
    with pytest.raises(ValueError):
        qa.driver_curve("udi; DROP TABLE production_units", db_path=db_path)
    with pytest.raises(ValueError):
        qa.driver_curve("torque_nm", "NOPE", db_path=db_path)


def test_wilson_interval_contains_the_observed_rate():
    low, high = qa.wilson_interval([5], [100])
    assert low[0] < 0.05 < high[0]


def test_operating_window_hides_small_samples(db_path):
    window = qa.operating_window(min_units=10_000, db_path=db_path)
    assert window["rate"].isna().all()


def test_empty_selection_returns_empty_figures(db_path):
    empty = qa.Filters(())
    assert isinstance(qa.batch_trend(empty, db_path), pd.DataFrame)
    assert len(charts.p_chart(qa.batch_trend(empty, db_path)).data) == 0
    assert len(charts.pareto_chart(qa.mode_pareto(empty, db_path)).data) == 0
    assert len(charts.grade_chart(qa.grade_rates(empty, db_path)).data) == 0


def test_key_findings_cover_the_main_results(db_path):
    findings = qa.key_findings(db_path)
    assert len(findings) == 7
    assert "339 of 10,000 units failed" in findings[0]


def test_dash_app_builds_with_all_components(db_path):
    from quality_lab.dash_app import create_app

    layout = str(create_app(db_path).layout)
    for component in ("p-chart", "pareto", "grades-chart", "heatmap", "batch-table"):
        assert component in layout


def test_panel_app_builds(db_path):
    from quality_lab.panel_app import create_app

    assert create_app(db_path).title == "Root-Cause Explorer"


def test_matplotlib_report_has_three_pages(db_path, tmp_path):
    from pypdf import PdfReader

    from quality_lab.report import build_report

    output = build_report(tmp_path / "report.pdf", db_path)
    assert len(PdfReader(output).pages) == 3
