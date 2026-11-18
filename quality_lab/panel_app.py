"""Panel app: an interactive root-cause explorer for quality engineers."""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import panel as pn

from quality_lab import analytics as qa
from quality_lab import charts
from quality_lab.config import DB_PATH, FAILURE_MODES, GRADES, PROCESS_VARIABLES

pn.extension("plotly", "tabulator", sizing_mode="stretch_width")

MODE_CHOICES = {"Any failure": qa.ANY_MODE, **{label: code for code, label in FAILURE_MODES.items()}}
VARIABLE_CHOICES = {f"{v.label} ({v.unit})": k for k, v in PROCESS_VARIABLES.items()}
GRADE_CHOICES = {label: code for code, label in GRADES.items()}


def mode_label(mode: str) -> str:
    return FAILURE_MODES.get(mode, "Any")


def summary_cards(mode: str, grades: list[str], db_path: Path) -> pn.Row:
    filters = qa.Filters(tuple(grades))
    units = qa.load_units(filters, db_path)
    target = qa.target_column(mode)
    count = len(units)
    failures = int(units[target].sum()) if count else 0
    rate = failures / count if count else 0
    return pn.Row(
        pn.indicators.Number(label="Units in selection", value=count, format="{value:,.0f}", font_size="26pt", title_size="11pt"),
        pn.indicators.Number(label=f"{mode_label(mode)} failures", value=failures, format="{value:,.0f}", font_size="26pt", title_size="11pt"),
        pn.indicators.Number(label="Failure rate", value=rate * 100, format="{value:.2f}%", font_size="26pt", title_size="11pt"),
    )


def driver_view(variable: str, mode: str, grades: list[str], bins: int, db_path: Path):
    filters = qa.Filters(tuple(grades))
    curve = qa.driver_curve(variable, mode, bins, filters, db_path)
    baseline = curve["failures"].sum() / curve["units"].sum() if len(curve) else 0
    return charts.driver_chart(curve, variable, baseline, mode_label(mode))


def distribution_view(variable: str, mode: str, grades: list[str], db_path: Path):
    units = qa.load_units(qa.Filters(tuple(grades)), db_path)
    return charts.outcome_distribution(units, variable, qa.target_column(mode), mode_label(mode))


def heatmap_view(variable: str, second: str, mode: str, grades: list[str], bins: int, min_units: int, db_path: Path):
    if variable == second:
        return charts.empty_figure("Choose two different process variables", 450)
    window = qa.operating_window(variable, second, mode, bins, min_units, qa.Filters(tuple(grades)), db_path)
    return charts.operating_heatmap(window, variable, second, min_units=min_units)


def risk_table(variable: str, second: str, mode: str, grades: list[str], bins: int, min_units: int, db_path: Path):
    if variable == second:
        return pd.DataFrame()
    risks = qa.risk_windows(variable, second, mode, bins, min_units, 10, qa.Filters(tuple(grades)), db_path)
    first_meta, second_meta = PROCESS_VARIABLES[variable], PROCESS_VARIABLES[second]
    table = risks.rename(columns={
        "x_bin": f"{first_meta.label} ({first_meta.unit})",
        "y_bin": f"{second_meta.label} ({second_meta.unit})",
        "units": "Units", "failures": "Failures", "rate": "Failure rate", "lift": "Times baseline",
    })
    if len(table):
        table["Failure rate"] = (table["Failure rate"] * 100).round(1).astype(str) + "%"
        table["Times baseline"] = table["Times baseline"].astype(float).round(1)
    return table


def create_app(db_path: Path = DB_PATH) -> pn.template.FastListTemplate:
    mode = pn.widgets.Select(label="Failure mode", options=MODE_CHOICES, value="OSF")
    variable = pn.widgets.Select(label="Process variable", options=VARIABLE_CHOICES, value="strain_min_nm")
    second = pn.widgets.Select(label="Compare with", options=VARIABLE_CHOICES, value="torque_nm")
    grades = pn.widgets.CheckButtonGroup(label="Product grade", options=GRADE_CHOICES, value=list(GRADES), color="primary", button_style="outline")
    bins = pn.widgets.IntSlider(label="Number of bins", start=4, end=12, value=8)
    min_units = pn.widgets.IntSlider(label="Minimum units per window", start=10, end=100, step=5, value=20)

    def table_panel(first, other, selected_mode, selected_grades, bin_count, minimum):
        table = risk_table(first, other, selected_mode, selected_grades, bin_count, minimum, db_path)
        if table.empty:
            return pn.pane.Markdown("No operating windows match this selection.")
        return pn.widgets.Tabulator(table, show_index=False, disabled=True, layout="fit_data_stretch", theme="simple")

    def csv_file():
        table = risk_table(variable.value, second.value, mode.value, grades.value, bins.value, min_units.value, db_path)
        return StringIO(table.to_csv(index=False))

    download = pn.widgets.FileDownload(callback=csv_file, filename="risk_windows.csv", label="Download risk windows (CSV)", color="primary")

    sidebar = [
        pn.pane.Markdown("### Investigate a failure\nPick a failure mode and a process variable to see where the risk rises."),
        mode, variable, second, pn.widgets.StaticText(value="Product grade"), grades, bins, min_units, download,
    ]
    main = [
        pn.Column(
            pn.bind(summary_cards, mode, grades, db_path),
            pn.Row(
                pn.pane.Plotly(pn.bind(driver_view, variable, mode, grades, bins, db_path), config={"displaylogo": False}),
                pn.pane.Plotly(pn.bind(distribution_view, variable, mode, grades, db_path), config={"displaylogo": False}),
            ),
            pn.pane.Plotly(pn.bind(heatmap_view, variable, second, mode, grades, bins, min_units, db_path), config={"displaylogo": False}),
            pn.pane.Markdown("### Highest-risk operating windows"),
            pn.bind(table_panel, variable, second, mode, grades, bins, min_units),
        )
    ]
    return pn.template.FastListTemplate(
        title="Root-Cause Explorer", sidebar=sidebar, main=main, accent_base_color=charts.BLUE,
        header_background=charts.INK, theme_toggle=False,
    )
