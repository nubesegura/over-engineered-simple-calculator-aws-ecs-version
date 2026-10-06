"""Entrypoint of the migration job: schema migrations and the application login password."""

import sys

from calculator_core.config.migrate_job import main

if __name__ == "__main__":
    sys.exit(main())
