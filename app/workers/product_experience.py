"""[新增] 运行产品预览队列以及过期清理。"""

import argparse
import logging
from time import sleep

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.services.object_storage_service import ObjectStorageService
from app.services.product_experience_worker import cleanup_product_experiences, run_next_preview


def main() -> None:
    parser = argparse.ArgumentParser(description="执行产品体验预览任务")
    parser.add_argument("--once", action="store_true", help="只处理一次任务和清理")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    storage = ObjectStorageService(
        settings.object_storage_mock_public_base_url or "http://localhost:8000"
    )
    while True:
        try:
            with SessionLocal() as db:
                processed = run_next_preview(db, storage)
                cleanup_product_experiences(db, storage)
        except SQLAlchemyError:
            logging.getLogger(__name__).error("产品体验 Worker 的数据库操作失败")
            if args.once:
                raise SystemExit(1) from None
            processed = False
        if args.once:
            return
        sleep(0.25 if processed else 2)


if __name__ == "__main__":
    main()
