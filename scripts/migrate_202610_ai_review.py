"""Create the durable AI review task table without modifying assessment records.

Run from the backend directory after deploying the code and before enabling AI review:
    python scripts/migrate_202610_ai_review.py
"""

import sys
from pathlib import Path

from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import engine  # noqa: E402
from app.models.ai_review import AIReviewTask  # noqa: E402


def main() -> None:
    existed = inspect(engine).has_table(AIReviewTask.__tablename__)
    AIReviewTask.__table__.create(engine, checkfirst=True)
    print("ai_review_task 已存在" if existed else "已创建 ai_review_task")


if __name__ == "__main__":
    main()
