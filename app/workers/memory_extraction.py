"""运行方式：uv run python -m app.workers.memory_extraction"""

import argparse
import logging
from time import sleep

from sqlalchemy.exc import SQLAlchemyError

from app.db.database import SessionLocal
from app.services.memory_extraction_service import MemoryExtractionService


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued memory extraction jobs")
    parser.add_argument("--once", action="store_true", help="attempt to process one available job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    service = MemoryExtractionService()
    if not service.model.available:
        parser.exit(2, "AI text extraction is disabled or the provider is not configured.\n")
    while True:
        try:
            with SessionLocal() as db:
                processed = service.run(db)
        except SQLAlchemyError:
            # Avoid logging query parameters or model input.
            logger.error("Memory extraction worker encountered a database error.")
            if args.once:
                raise SystemExit(1) from None
            sleep(5)
            continue
        if args.once:
            return
        if not processed:
            sleep(1)


if __name__ == "__main__":
    main()
