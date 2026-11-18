"""Matplotlib report: a static PDF quality summary that can be shared without a server."""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import PercentFormatter

from quality_lab import analytics as qa
from quality_lab.charts import BLUE, CRITICAL, GRID, HEAT_SCALE, INK, MUTED, ORANGE
from quality_lab.config import DB_PATH, PROCESS_VARIABLES, REPORT_PDF

A4 = (8.27, 11.69)
HEAT_MAP = LinearSegmentedColormap.from_list("quality", [c for _, c in HEAT_SCALE]).with_extremes(bad="#efeeea")

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "text.color": INK, "axes.labelcolor": MUTED,
    "axes.edgecolor": GRID, "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 11,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
    "pdf.fonttype": 42,
})


def _page_header(figure, title: str, subtitle: str) -> None:
    figure.text(0.08, 0.955, title, fontsize=18, fontweight="bold")
    figure.text(0.08, 0.933, subtitle, fontsize=10, color=MUTED)


def plot_p_chart(ax, trend) -> None:
    ax.step(trend["batch"], trend["ucl"], where="mid", color=CRITICAL, lw=1.3, label="Upper control limit")
    ax.plot(trend["batch"], trend["centre"], color=MUTED, lw=1.3, label="Average rate")
    ax.plot(trend["batch"], trend["rate"], color=BLUE, lw=1.8, marker="o", ms=4, label="Batch failure rate")
    flagged = trend[trend["out_of_control"]]
    ax.scatter(flagged["batch"], flagged["rate"], color=CRITICAL, marker="D", s=46, zorder=4, edgecolor="white", label="Out of control")
    for row in flagged.itertuples():
        ax.annotate(f"Batch {row.batch}", (row.batch, row.rate), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8)
    ax.set(title="Failure rate by production batch", xlabel="Production batch", ylabel="Failure rate")
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_ylim(bottom=0, top=max(trend["rate"].max(), trend["ucl"].max()) * 1.2)
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.legend(frameon=False, ncol=4, loc="upper left", bbox_to_anchor=(0, -0.2), fontsize=8)


def plot_pareto(ax, pareto) -> None:
    ax.bar(pareto["label"], pareto["share"], color=BLUE, width=0.55, label="Share of events")
    ax.plot(pareto["label"], pareto["cumulative_share"], color=ORANGE, marker="o", lw=1.8, label="Cumulative share")
    for x, row in enumerate(pareto.itertuples()):
        ax.text(x, row.share + 0.025, f"{row.events}", ha="center", fontsize=8)
    ax.set(title="Failure modes (Pareto)", ylabel="Share of failure events", ylim=(0, 1.08))
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, -0.12), ncol=2, fontsize=8)


def plot_heatmap(figure, ax, window, x: str, y: str) -> None:
    x_labels = window.sort_values("x_order")["x_bin"].drop_duplicates().tolist()
    y_labels = window.sort_values("y_order")["y_bin"].drop_duplicates().tolist()
    rate = window.pivot(index="y_bin", columns="x_bin", values="rate").reindex(index=y_labels, columns=x_labels)
    image = ax.imshow(np.ma.masked_invalid(rate.values.astype(float)), origin="lower", aspect="auto", cmap=HEAT_MAP, vmin=0)
    ax.set_xticks(range(len(x_labels)), x_labels, rotation=35, ha="right", fontsize=7.5)
    ax.set_yticks(range(len(y_labels)), y_labels, fontsize=7.5)
    ax.set_xticks(np.arange(-0.5, len(x_labels)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(y_labels)), minor=True)
    ax.grid(which="minor", color="white", lw=1.5)
    ax.tick_params(which="both", length=0)
    x_meta, y_meta = PROCESS_VARIABLES[x], PROCESS_VARIABLES[y]
    ax.set(title="Failure rate by operating window", xlabel=f"{x_meta.label} ({x_meta.unit})", ylabel=f"{y_meta.label} ({y_meta.unit})")
    bar = figure.colorbar(image, ax=ax, fraction=0.035, pad=0.02, format=PercentFormatter(1, decimals=0))
    bar.outline.set_visible(False)
    bar.set_label("Failure rate")


def plot_driver(ax, curve, variable: str, title: str) -> None:
    positions = np.arange(len(curve))
    meta = PROCESS_VARIABLES[variable]
    ax.fill_between(positions, curve["ci_low"], curve["ci_high"], color=BLUE, alpha=0.16, lw=0, label="95% confidence interval")
    ax.plot(positions, curve["rate"], color=BLUE, marker="o", ms=4, lw=1.8, label="Failure rate")
    ax.set_xticks(positions, curve["bin"], rotation=30, ha="right", fontsize=7.5)
    ax.set(title=title, xlabel=f"{meta.label} ({meta.unit})", ylabel="Failure rate")
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.legend(frameon=False, loc="upper left", fontsize=8)


def plot_grades(ax, rates) -> None:
    positions = np.arange(len(rates))
    lower = rates["rate"] - rates["ci_low"]
    upper = rates["ci_high"] - rates["rate"]
    ax.barh(positions, rates["rate"], color=BLUE, height=0.5, xerr=[lower, upper], ecolor=INK, capsize=3)
    ax.set_yticks(positions, rates["label"])
    ax.invert_yaxis()
    ax.set(title="Failure rate by product grade (95% CI)", xlabel="Failure rate")
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(axis="x", color=GRID, lw=0.7)
    ax.set_axisbelow(True)


def _overview_page(pdf: PdfPages, db_path: Path) -> None:
    summary = qa.kpis(db_path=db_path)
    figure = plt.figure(figsize=A4)
    _page_header(figure, "Factory Quality Report", "AI4I 2020 machining dataset, overview")
    tiles = [
        ("Units inspected", f"{summary['units']:,}"),
        ("Failure rate", f"{summary['failure_rate']:.2%}"),
        ("First-pass yield", f"{summary['first_pass_yield']:.2%}"),
        ("Out-of-control batches", f"{summary['out_of_control_batches']} of {summary['batches']}"),
    ]
    for i, (label, value) in enumerate(tiles):
        x = 0.08 + i * 0.225
        figure.text(x, 0.893, label, fontsize=8.5, color=MUTED)
        figure.text(x, 0.866, value, fontsize=16, fontweight="bold")
    grid = figure.add_gridspec(2, 1, left=0.1, right=0.94, top=0.82, bottom=0.09, hspace=0.42)
    plot_p_chart(figure.add_subplot(grid[0]), qa.batch_trend(db_path=db_path))
    plot_pareto(figure.add_subplot(grid[1]), qa.mode_pareto(db_path=db_path))
    pdf.savefig(figure)
    plt.close(figure)


def _process_page(pdf: PdfPages, db_path: Path) -> None:
    figure = plt.figure(figsize=A4)
    _page_header(figure, "Process Risk", "Where failures concentrate across operating conditions")
    grid = figure.add_gridspec(3, 2, left=0.12, right=0.93, top=0.89, bottom=0.08, hspace=0.62, wspace=0.34, height_ratios=[1.45, 1, 0.7])
    x, y = "rotational_speed_rpm", "torque_nm"
    plot_heatmap(figure, figure.add_subplot(grid[0, :]), qa.operating_window(x, y, db_path=db_path), x, y)
    plot_driver(figure.add_subplot(grid[1, 0]), qa.driver_curve("tool_wear_min", db_path=db_path), "tool_wear_min", "Failure rate by tool wear")
    plot_driver(figure.add_subplot(grid[1, 1]), qa.driver_curve("temp_delta_k", db_path=db_path), "temp_delta_k", "Failure rate by temperature gap")
    plot_grades(figure.add_subplot(grid[2, :]), qa.grade_rates(db_path=db_path))
    pdf.savefig(figure)
    plt.close(figure)


def _findings_page(pdf: PdfPages, db_path: Path) -> None:
    figure = plt.figure(figsize=A4)
    _page_header(figure, "Findings and Data Quality", "Calculated from the data each time the report runs")
    y = 0.885
    figure.text(0.08, y, "Key findings", fontsize=12, fontweight="bold")
    y -= 0.032
    for number, finding in enumerate(qa.key_findings(db_path), start=1):
        lines = textwrap.wrap(finding, 82)
        figure.text(0.08, y, f"{number}.", fontsize=10, fontweight="bold", va="top")
        figure.text(0.115, y, "\n".join(lines), fontsize=10, va="top", linespacing=1.4)
        y -= 0.0185 * len(lines) + 0.014

    y -= 0.03
    figure.text(0.08, y, "Data-quality checks", fontsize=12, fontweight="bold")
    checks = qa.data_quality(db_path)
    rows = [
        [c.detail, "Stops pipeline" if c.severity == "error" else "Reported", f"{c.affected:,}", "Pass" if c.affected == 0 else "Review"]
        for c in checks.itertuples()
    ]
    ax = figure.add_axes([0.08, y - 0.34, 0.85, 0.32])
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=["Rule", "Severity", "Rows affected", "Result"], colWidths=[0.56, 0.17, 0.15, 0.12], loc="upper left", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1, 1.75)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.visible_edges = "B"
        cell.set_text_props(ha="left")
        if row == 0:
            cell.set_text_props(fontweight="bold", color=MUTED, ha="left")
    figure.text(
        0.08, 0.06,
        "Source: AI4I 2020 Predictive Maintenance Dataset, UCI Machine Learning Repository, "
        "CC BY 4.0, doi.org/10.24432/C5HS5C.\nThe dataset is synthetic. Batches follow unit ID order because it has no timestamps.",
        fontsize=7.5, color=MUTED, linespacing=1.5,
    )
    pdf.savefig(figure)
    plt.close(figure)


def build_report(output: Path = REPORT_PDF, db_path: Path = DB_PATH) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output, metadata={"Title": "Factory Quality Report"}) as pdf:
        _overview_page(pdf, db_path)
        _process_page(pdf, db_path)
        _findings_page(pdf, db_path)
    return output
