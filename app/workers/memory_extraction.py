"""运行方式：uv run python -m app.workers.memory_extraction"""

import argparse
import logging
from time import sleep

from sqlalchemy.exc import SQLAlchemyError

from app.db.database import SessionLocal
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.memory_pipeline import MemoryPipelineService


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued memory extraction jobs")
    parser.add_argument("--once", action="store_true", help="attempt to process one available job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    service = MemoryExtractionService()
    pipeline = MemoryPipelineService(service.settings)
    if not (service.model.available or pipeline.asr.available):
        parser.exit(2, "The AI text and ASR providers are unavailable.\n")
    while True:
        try:
            with SessionLocal() as db:
                processed = False
                for execute in (
                    lambda: pipeline.run(db, "asr"),
                    lambda: pipeline.run(db, "organization"),
                    lambda: service.run(db),
                ):
                    processed = execute() or processed
                    if processed and args.once:
                        break
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
