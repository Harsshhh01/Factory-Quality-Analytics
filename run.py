"""Command-line entry point: python run.py [etl|report|dash|panel|all]."""
from __future__ import annotations

import argparse

from quality_lab.config import DB_PATH


def ensure_database() -> None:
    if not DB_PATH.exists():
        run_etl()


def run_etl() -> None:
    from quality_lab.etl import run_etl as pipeline

    result = pipeline()
    print(f"Loaded {result['units']:,} units in {result['batches']} batches ({result['failures']} failures).")
    print(f"Data-quality warnings: {result['warnings']}")


def run_report() -> None:
    from quality_lab.report import build_report

    ensure_database()
    print(f"Report written to {build_report()}")


def run_dash(port: int) -> None:
    from quality_lab.dash_app import create_app

    ensure_database()
    create_app().run(host="127.0.0.1", port=port, debug=False)


def run_panel(port: int) -> None:
    import panel as pn

    from quality_lab.panel_app import create_app

    ensure_database()
    pn.serve(create_app, port=port, address="127.0.0.1", show=True, title="Root-Cause Explorer")


def main() -> None:
    parser = argparse.ArgumentParser(description="Factory quality analytics")
    parser.add_argument("command", choices=["etl", "report", "dash", "panel", "all"])
    parser.add_argument("--port", type=int, help="Port for the dash or panel app")
    args = parser.parse_args()

    if args.command == "etl":
        run_etl()
    elif args.command == "report":
        run_report()
    elif args.command == "dash":
        run_dash(args.port or 8050)
    elif args.command == "panel":
        run_panel(args.port or 5006)
    else:
        run_etl()
        run_report()


if __name__ == "__main__":
    main()
