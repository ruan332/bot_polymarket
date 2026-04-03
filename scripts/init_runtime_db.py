from __future__ import annotations

import asyncio
import os
import sys

from core.database import Database


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    db = Database(database_url)
    try:
        await db.connect()
        await db.init_schema()
    finally:
        await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
