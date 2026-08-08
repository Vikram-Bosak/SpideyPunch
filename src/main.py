"""Hollywood Movie Clips - 24h Automated Upload Workflow entry point.

Usage:
    python -m src.main --dry-run
    python -m src.main
"""

from __future__ import annotations

import argparse
import sys

from .common.logger import get_logger
from .orchestrator import Orchestrator

logger = get_logger("main")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hollywood-clips",
        description="24-hour automated Hollywood movie clips upload workflow.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the pipeline without contacting Google Drive, YouTube, "
        "Facebook or Discord.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Treat the next scheduled slot as due regardless of the clock.",
    )
    args = parser.parse_args(argv)

    logger.info("Starting Hollywood Movie Clips workflow (dry_run=%s)", args.dry_run)
    orch = Orchestrator(dry_run=args.dry_run)
    if args.force:
        import os
        os.environ["_FORCE_SLOT"] = "1"
    exit_code = orch.run_once()
    logger.info("Workflow finished with exit code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
