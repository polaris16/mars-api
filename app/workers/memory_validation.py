"""[新增] 消费回忆录复验队列，支持单次运行和持续轮询。"""

import argparse
import logging
from time import sleep

from sqlalchemy.exc import SQLAlchemyError

from app.db.database import SessionLocal
from app.services.memory_validation_service import MemoryValidationService


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued memory validation jobs")
    parser.add_argument("--once", action="store_true", help="process one available job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    service = MemoryValidationService()
    while True:
        try:
            with SessionLocal() as db:
                processed = service.run(db)
        except SQLAlchemyError:
            logger.error("Memory validation worker encountered a database error.")
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
