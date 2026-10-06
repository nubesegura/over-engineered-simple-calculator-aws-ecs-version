"""Entrypoint of the CSV ingestion job: ingests the object named by INGEST_BUCKET and INGEST_KEY."""

import sys

from calculator_core.config.ingest_job import main

if __name__ == "__main__":
    sys.exit(main())
