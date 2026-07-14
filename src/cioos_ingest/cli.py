#!/usr/bin/env python3
"""
Single CLI entry point for all CIOOS ingestion pipelines.

    cioos-ingest eccc              # ECCC SWOB-ML AMQP consumer (runs forever)
    cioos-ingest meds [--run-now]  # MEDS buoy batch flow (serves on MEDS_CRON)
    cioos-ingest argo [--run-now]  # Argo Canada batch flow (serves on ARGO_CRON)

Imports are lazy per-subcommand so e.g. `argo` doesn't pull in pika/pandas.
"""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cioos-ingest")
    sub = parser.add_subparsers(dest="pipeline", required=True)

    sub.add_parser("eccc", help="Run the ECCC SWOB-ML AMQP consumer (runs forever)")
    for name, help_ in (
        ("meds", "Serve the MEDS buoy flow on its cron (MEDS_CRON)"),
        ("argo", "Serve the Argo Canada flow on its cron (ARGO_CRON)"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--run-now", action="store_true",
                        help="run the flow once and exit instead of serving")

    args = parser.parse_args(argv)

    if args.pipeline == "eccc":
        from cioos_ingest.eccc.consumer import main as run
        run()
    elif args.pipeline == "meds":
        from cioos_ingest.meds.flow import main as run
        run(run_now=args.run_now)
    elif args.pipeline == "argo":
        from cioos_ingest.argo.flow import main as run
        run(run_now=args.run_now)


if __name__ == "__main__":
    main()
