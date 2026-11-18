"""Reusable interactive Plotly figures for the Dash and Panel apps."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from quality_lab.config import PROCESS_VARIABLES

BLUE = "#2a78d6"
BLUE_BAND = "rgba(42, 120, 214, 0.16)"
ORANGE = "#eb6834"
CRITICAL = "#d03b3b"
INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e8e7e3"
SURFACE = "#fcfcfb"
HEAT_SCALE = [[0, "#eef5fd"], [0.25, "#9ec5f4"], [0.5, "#3987e5"], [0.75, "#1c5cab"], [1, "#0d366b"]]


def _style(figure: go.Figure, title: str, height: int = 360, legend: bool = True, top_margin: int | None = None) -> go.Figure:
    figure.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "y": 0.985, "yanchor": "top", "yref": "container", "font": {"size": 15, "color": INK}},
        height=height,
        margin={"l": 60, "r": 20, "t": top_margin or (82 if legend else 52), "b": 58},
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font={"family": "Inter, Segoe UI, Arial, sans-serif", "size": 12, "color": MUTED},
        showlegend=legend,
        legend={"orientation": "h", "x": 0, "y": 1.03, "yanchor": "bottom", "bgcolor": "rgba(0,0,0,0)"},
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
    )
    figure.update_xaxes(showgrid=False, linecolor=GRID, ticks="", zeroline=False)
    figure.update_yaxes(gridcolor=GRID, linecolor=GRID, ticks="", zeroline=False)
    return figure


def empty_figure(message: str = "No units match the selected filters", height: int = 360) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(text=message, showarrow=False, font={"size": 14, "color": MUTED})
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    figure.update_layout(height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, margin={"l": 20, "r": 20, "t": 20, "b": 20})
    return figure


def p_chart(trend: pd.DataFrame) -> go.Figure:
    if trend.empty:
        return empty_figure()
    figure = go.Figure()
    figure.add_scatter(
        x=trend["batch"], y=trend["ucl"], name="Upper control limit", mode="lines",
        line={"color": CRITICAL, "width": 1.5, "shape": "hv"}, hovertemplate="UCL %{y:.2%}<extra></extra>",
    )
    figure.add_scatter(
        x=trend["batch"], y=trend["centre"], name="Average rate", mode="lines",
        line={"color": MUTED, "width": 1.5}, hovertemplate="Average %{y:.2%}<extra></extra>",
    )
    figure.add_scatter(
        x=trend["batch"], y=trend["rate"], name="Batch failure rate", mode="lines+markers",
        line={"color": BLUE, "width": 2}, marker={"size": 7, "color": BLUE, "line": {"color": SURFACE, "width": 1.5}},
        customdata=trend[["units", "failures"]],
        hovertemplate="Batch %{x}<br>Failure rate %{y:.2%}<br>%{customdata[1]} of %{customdata[0]} units<extra></extra>",
    )
    flagged = trend[trend["out_of_control"]]
    figure.add_scatter(
        x=flagged["batch"], y=flagged["rate"], name="Out of control", mode="markers",
        marker={"size": 13, "color": CRITICAL, "symbol": "diamond", "line": {"color": SURFACE, "width": 2}},
        hovertemplate="Batch %{x} is out of control<br>Failure rate %{y:.2%}<extra></extra>",
    )
    _style(figure, "Failure rate by production batch (p-chart)")
    figure.update_xaxes(title="Production batch", tick0=0, dtick=5, range=[trend["batch"].min() - 0.7, trend["batch"].max() + 0.7])
    figure.update_yaxes(title="Failure rate", tickformat=".0%", rangemode="tozero")
    return figure


def pareto_chart(pareto: pd.DataFrame) -> go.Figure:
    if pareto.empty:
        return empty_figure()
    figure = go.Figure()
    figure.add_bar(
        x=pareto["label"], y=pareto["share"], name="Share of events", marker={"color": BLUE, "cornerradius": 4},
        customdata=pareto["events"], hovertemplate="%{x}<br>%{customdata} events (%{y:.1%})<extra></extra>",
    )
    figure.add_scatter(
        x=pareto["label"], y=pareto["cumulative_share"], name="Cumulative share", mode="lines+markers",
        line={"color": ORANGE, "width": 2}, marker={"size": 8, "color": ORANGE, "line": {"color": SURFACE, "width": 1.5}},
        hovertemplate="%{x}<br>Cumulative %{y:.1%}<extra></extra>",
    )
    _style(figure, "Failure modes (Pareto)")
    figure.update_layout(bargap=0.45)
    figure.update_yaxes(title="Share of failure events", tickformat=".0%", range=[0, 1.05])
    return figure


def grade_chart(rates: pd.DataFrame) -> go.Figure:
    if rates.empty:
        return empty_figure()
    figure = go.Figure()
    figure.add_bar(
        x=rates["rate"], y=rates["label"], orientation="h", marker={"color": BLUE, "cornerradius": 4},
        error_x={"type": "data", "symmetric": False, "array": rates["ci_high"] - rates["rate"],
                 "arrayminus": rates["rate"] - rates["ci_low"], "color": INK, "thickness": 1.5, "width": 5},
        customdata=rates[["failures", "units", "ci_low", "ci_high"]],
        hovertemplate="%{y} grade<br>Failure rate %{x:.2%}<br>%{customdata[0]} of %{customdata[1]} units"
                      "<br>95% CI %{customdata[2]:.2%} to %{customdata[3]:.2%}<extra></extra>",
    )
    _style(figure, "Failure rate by product grade, with 95% confidence interval", legend=False)
    figure.update_layout(bargap=0.5)
    figure.update_xaxes(title="Failure rate", tickformat=".0%", rangemode="tozero", showgrid=True, gridcolor=GRID)
    figure.update_yaxes(showgrid=False, autorange="reversed")
    return figure


def operating_heatmap(window: pd.DataFrame, x: str, y: str, height: int = 450, min_units: int = 20) -> go.Figure:
    if window.empty or window["rate"].notna().sum() == 0:
        return empty_figure("Not enough units in any operating window", height)
    x_labels = window.sort_values("x_order")["x_bin"].drop_duplicates().tolist()
    y_labels = window.sort_values("y_order")["y_bin"].drop_duplicates().tolist()
    rate = window.pivot(index="y_bin", columns="x_bin", values="rate").reindex(index=y_labels, columns=x_labels)
    units = window.pivot(index="y_bin", columns="x_bin", values="units").reindex(index=y_labels, columns=x_labels)
    failures = window.pivot(index="y_bin", columns="x_bin", values="failures").reindex(index=y_labels, columns=x_labels)
    x_meta, y_meta = PROCESS_VARIABLES[x], PROCESS_VARIABLES[y]

    figure = go.Figure(
        go.Heatmap(
            z=rate.values, x=x_labels, y=y_labels, colorscale=HEAT_SCALE, zmin=0, xgap=2, ygap=2, hoverongaps=False,
            customdata=pd.concat([failures.stack(future_stack=True), units.stack(future_stack=True)], axis=1).values.reshape(len(y_labels), len(x_labels), 2),
            colorbar={"title": "Failure rate", "tickformat": ".0%", "thickness": 12, "outlinewidth": 0},
            hovertemplate=f"{x_meta.label}: %{{x}} {x_meta.unit}<br>{y_meta.label}: %{{y}} {y_meta.unit}"
                          "<br>Failure rate %{z:.1%}<br>%{customdata[0]:.0f} of %{customdata[1]:.0f} units<extra></extra>",
        )
    )
    _style(figure, f"Operating windows: {y_meta.label.lower()} against {x_meta.label.lower()}", height=height, legend=False, top_margin=70)
    figure.update_layout(title_subtitle={"text": f"Blank cells contain fewer than {min_units} units", "font": {"size": 12, "color": MUTED}})
    figure.update_xaxes(title=f"{x_meta.label} ({x_meta.unit})", tickangle=-30, type="category")
    figure.update_yaxes(title=f"{y_meta.label} ({y_meta.unit})", showgrid=False, type="category")
    return figure


def driver_chart(curve: pd.DataFrame, variable: str, baseline: float, mode_label: str) -> go.Figure:
    if curve.empty:
        return empty_figure()
    meta = PROCESS_VARIABLES[variable]
    figure = go.Figure()
    figure.add_scatter(
        x=curve["bin"], y=curve["ci_high"], mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip",
    )
    figure.add_scatter(
        x=curve["bin"], y=curve["ci_low"], mode="lines", line={"width": 0}, fill="tonexty", fillcolor=BLUE_BAND,
        name="95% confidence interval", hoverinfo="skip",
    )
    figure.add_scatter(
        x=curve["bin"], y=[baseline] * len(curve), mode="lines", name="Selection average",
        line={"color": MUTED, "width": 1.5}, hovertemplate="Average %{y:.2%}<extra></extra>",
    )
    figure.add_scatter(
        x=curve["bin"], y=curve["rate"], mode="lines+markers", name="Failure rate",
        line={"color": BLUE, "width": 2}, marker={"size": 8, "color": BLUE, "line": {"color": SURFACE, "width": 1.5}},
        customdata=curve[["failures", "units"]],
        hovertemplate=f"{meta.label}: %{{x}} {meta.unit}<br>Failure rate %{{y:.2%}}<br>%{{customdata[0]}} of %{{customdata[1]}} units<extra></extra>",
    )
    _style(figure, f"{mode_label} failure rate by {meta.label.lower()}", top_margin=112)
    figure.update_xaxes(title=f"{meta.label} ({meta.unit})", tickangle=-30, type="category")
    figure.update_yaxes(title="Failure rate", tickformat=".1%", rangemode="tozero")
    return figure


def outcome_distribution(units: pd.DataFrame, variable: str, target: str, mode_label: str) -> go.Figure:
    if units.empty:
        return empty_figure()
    meta = PROCESS_VARIABLES[variable]
    figure = go.Figure()
    for flag, name, color in ((0, "No failure", BLUE), (1, "Failure", ORANGE)):
        values = units.loc[units[target] == flag, variable]
        figure.add_box(y=[name] * len(values), x=values, name=name, orientation="h", marker_color=color, line_width=1.5, boxpoints=False)
    _style(figure, f"{meta.label} distribution by {mode_label.lower()} outcome", legend=False)
    figure.update_xaxes(title=f"{meta.label} ({meta.unit})", showgrid=True, gridcolor=GRID)
    figure.update_yaxes(showgrid=False)
    return figure
