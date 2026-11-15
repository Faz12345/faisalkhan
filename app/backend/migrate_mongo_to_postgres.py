import asyncio
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).parent
load_dotenv(BACKEND_DIR / ".env")
sys.path.insert(0, str(BACKEND_DIR))

from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from bson.decimal128 import Decimal128
from postgres_store import db


COLLECTIONS = ("users", "appointments", "treatments", "notifications")


def json_compatible(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_compatible(item) for item in value]
    return value


async def migrate():
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    database_name = os.environ.get("DB_NAME", "").strip()
    if not mongo_url or not database_name:
        raise RuntimeError("Set MONGO_URL and DB_NAME to the source MongoDB database.")

    mongo_client = AsyncIOMotorClient(mongo_url)
    try:
        await db.create_tables()
        source = mongo_client[database_name]
        for collection_name in COLLECTIONS:
            count = 0
            async for document in source[collection_name].find({}):
                await getattr(db, collection_name).insert_one(
                    json_compatible(document),
                    preserve_id=True,
                    upsert=True,
                )
                count += 1
            print(f"Imported {count} {collection_name} records.")
    finally:
        mongo_client.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(migrate())
