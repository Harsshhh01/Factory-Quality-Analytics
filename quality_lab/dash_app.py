"""Dash app: the quality overview used to monitor production at a glance."""
from __future__ import annotations

from pathlib import Path

from dash import Dash, Input, Output, dash_table, dcc, html

from quality_lab import analytics as qa
from quality_lab import charts
from quality_lab.config import BATCH_SIZE, DB_PATH, FAILURE_MODES, GRADES, PROCESS_VARIABLES, ROOT

VARIABLE_OPTIONS = [{"label": f"{v.label} ({v.unit})", "value": k} for k, v in PROCESS_VARIABLES.items()]
MODE_OPTIONS = [{"label": "Any failure", "value": qa.ANY_MODE}] + [
    {"label": label, "value": code} for code, label in FAILURE_MODES.items()
]


def _kpi(label: str, value: str, note: str = "") -> html.Div:
    return html.Div(
        [html.Span(label, className="kpi-label"), html.Strong(value, className="kpi-value"), html.Span(note, className="kpi-note")],
        className="kpi",
    )


def _control(label: str, component) -> html.Div:
    return html.Div([html.Label(label, className="control-label"), component], className="control")


def build_kpis(summary: dict) -> list[html.Div]:
    has_units = summary["units"] > 0
    return [
        _kpi("Units inspected", f"{summary['units']:,}", f"{summary['batches']} batches"),
        _kpi("Failure rate", f"{summary['failure_rate']:.2%}" if has_units else "N/A", f"{summary['failures']} failed units"),
        _kpi("First-pass yield", f"{summary['first_pass_yield']:.2%}" if has_units else "N/A", "Units without failure"),
        _kpi("Top failure mode", summary["top_mode"], "Most recorded events"),
        _kpi("Out-of-control batches", str(summary["out_of_control_batches"]), "Outside 3-sigma limits"),
    ]


def build_layout(batch_count: int) -> html.Div:
    marks = {b: str(b) for b in range(5, batch_count + 1, 5)} | {1: "1"}
    filters = html.Div(
        [
            _control("Product grade", dcc.Checklist(
                id="grades", options=[{"label": f" {v}", "value": k} for k, v in GRADES.items()],
                value=list(GRADES), inline=True, className="grade-list",
            )),
            _control(f"Production batches ({BATCH_SIZE} units each)", dcc.RangeSlider(
                id="batches", min=1, max=batch_count, step=1, value=[1, batch_count], marks=marks,
                tooltip={"placement": "bottom", "always_visible": False}, allowCross=False,
            )),
        ],
        className="filters",
    )
    window_controls = html.Div(
        [
            _control("Horizontal axis", dcc.Dropdown(id="window-x", options=VARIABLE_OPTIONS, value="rotational_speed_rpm", clearable=False)),
            _control("Vertical axis", dcc.Dropdown(id="window-y", options=VARIABLE_OPTIONS, value="torque_nm", clearable=False)),
            _control("Failure mode", dcc.Dropdown(id="window-mode", options=MODE_OPTIONS, value=qa.ANY_MODE, clearable=False)),
        ],
        className="window-controls",
    )
    return html.Div(
        [
            html.Header([
                html.H1("Factory Quality Overview"),
                html.P("Production quality, failure modes and process risk across 10,000 machining operations."),
            ]),
            filters,
            html.Div(id="kpis", className="kpis"),
            html.Section(dcc.Graph(id="p-chart", config={"displaylogo": False}), className="card"),
            html.Div(
                [
                    html.Section(dcc.Graph(id="pareto", config={"displaylogo": False}), className="card"),
                    html.Section(dcc.Graph(id="grades-chart", config={"displaylogo": False}), className="card"),
                ],
                className="two-up",
            ),
            html.Section([window_controls, dcc.Graph(id="heatmap", config={"displaylogo": False})], className="card"),
            html.Section(
                [
                    html.H2("Batches to investigate"),
                    html.P(id="batch-note", className="table-note"),
                    dash_table.DataTable(
                        id="batch-table",
                        columns=[
                            {"name": "Batch", "id": "batch"},
                            {"name": "Unit IDs", "id": "unit_ids"},
                            {"name": "Units", "id": "units"},
                            {"name": "Failures", "id": "failures"},
                            {"name": "Failure rate", "id": "rate_text"},
                            {"name": "Upper limit", "id": "ucl_text"},
                        ],
                        style_as_list_view=True,
                        style_header={"fontWeight": "600", "backgroundColor": "transparent", "borderBottom": "1px solid #d9d8d3"},
                        style_cell={"textAlign": "left", "padding": "9px 10px", "backgroundColor": "transparent",
                                    "fontFamily": "Inter, Segoe UI, Arial, sans-serif", "fontSize": "14px", "border": "none"},
                    ),
                ],
                className="card table-card",
            ),
        ],
        className="shell",
    )


def create_app(db_path: Path = DB_PATH) -> Dash:
    app = Dash(__name__, title="Factory Quality Overview", assets_folder=str(ROOT / "quality_lab" / "assets"))
    batch_count = int(qa.batch_trend(db_path=db_path)["batch"].max())
    app.layout = build_layout(batch_count)

    @app.callback(
        Output("kpis", "children"),
        Output("p-chart", "figure"),
        Output("pareto", "figure"),
        Output("grades-chart", "figure"),
        Output("batch-table", "data"),
        Output("batch-note", "children"),
        Input("grades", "value"),
        Input("batches", "value"),
    )
    def update_overview(grades, batches):
        filters = qa.Filters(tuple(grades or ()), int(batches[0]), int(batches[1]))
        trend = qa.batch_trend(filters, db_path)
        flagged = trend[trend["out_of_control"].astype(bool)] if len(trend) else trend
        rows = [
            {
                "batch": int(r.batch), "unit_ids": f"{int(r.first_udi)} to {int(r.last_udi)}", "units": int(r.units),
                "failures": int(r.failures), "rate_text": f"{r.rate:.2%}", "ucl_text": f"{r.ucl:.2%}",
            }
            for r in flagged.itertuples()
        ]
        note = (
            f"{len(rows)} batch{'es' if len(rows) != 1 else ''} outside the control limits for this selection."
            if rows else "No batches are outside the control limits for this selection."
        )
        return (
            build_kpis(qa.kpis(filters, db_path)),
            charts.p_chart(trend),
            charts.pareto_chart(qa.mode_pareto(filters, db_path)),
            charts.grade_chart(qa.grade_rates(filters, db_path)),
            rows,
            note,
        )

    @app.callback(
        Output("heatmap", "figure"),
        Input("grades", "value"),
        Input("batches", "value"),
        Input("window-x", "value"),
        Input("window-y", "value"),
        Input("window-mode", "value"),
    )
    def update_heatmap(grades, batches, x, y, mode):
        if x == y:
            return charts.empty_figure("Choose two different process variables", 450)
        filters = qa.Filters(tuple(grades or ()), int(batches[0]), int(batches[1]))
        return charts.operating_heatmap(qa.operating_window(x, y, mode, filters=filters, db_path=db_path), x, y)

    return app
