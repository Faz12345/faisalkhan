import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL must be configured for PostgreSQL.")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection string.")
    return url


@dataclass
class WriteResult:
    inserted_id: Optional[str] = None
    modified_count: int = 0
    deleted_count: int = 0


class Cursor:
    def __init__(self, collection: "Collection", filters: dict):
        self.collection = collection
        self.filters = filters
        self.order_by: list[tuple[str, int]] = []

    def sort(self, key_or_list, direction: Optional[int] = None):
        if isinstance(key_or_list, str):
            self.order_by = [(key_or_list, direction or 1)]
        else:
            self.order_by = list(key_or_list)
        return self

    async def to_list(self, length: Optional[int] = None) -> list[dict]:
        return await self.collection._find(self.filters, self.order_by, length)

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for document in await self.to_list(None):
            yield document


class Collection:
    def __init__(self, database: "PostgresDatabase", name: str):
        self.database = database
        self.name = name

    def find(self, filters: Optional[dict] = None) -> Cursor:
        return Cursor(self, filters or {})

    async def find_one(self, filters: Optional[dict] = None, sort=None) -> Optional[dict]:
        order_by = list(sort) if sort else []
        rows = await self._find(filters or {}, order_by, 1)
        return rows[0] if rows else None

    async def _find(self, filters: dict, order_by: list[tuple[str, int]], limit: Optional[int]):
        conditions, params = _compile_filter(filters)
        sql = f'SELECT id, data FROM "{self.name}" WHERE {conditions}'
        if order_by:
            order_parts = []
            for key, direction in order_by:
                _validate_key(key)
                order_parts.append(
                    f"(data ->> '{key}') {'DESC' if direction < 0 else 'ASC'}"
                )
            sql += " ORDER BY " + ", ".join(order_parts)
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        async with self.database.engine.connect() as connection:
            result = await connection.execute(text(sql), params)
            return [_document(row.id, row.data) for row in result]

    async def insert_one(
        self, document: dict, preserve_id: bool = False, upsert: bool = False
    ) -> WriteResult:
        document = dict(document)
        document_id = str(document.pop("_id", "")) if preserve_id else ""
        document_id = document_id or secrets.token_hex(12)
        payload = json.dumps(document, ensure_ascii=False)
        statement = (
            f'INSERT INTO "{self.name}" (id, data) '
            "VALUES (:id, CAST(:data AS jsonb))"
        )
        if upsert:
            statement += " ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data"
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text(statement),
                {"id": document_id, "data": payload},
            )
        return WriteResult(inserted_id=document_id)

    async def update_one(self, filters: dict, update: dict) -> WriteResult:
        return await self._update(filters, update, many=False)

    async def update_many(self, filters: dict, update: dict) -> WriteResult:
        return await self._update(filters, update, many=True)

    async def _update(self, filters: dict, update: dict, many: bool) -> WriteResult:
        conditions, params = _compile_filter(filters)
        expression = "data"
        assignments = update.get("$set", {})
        for index, (key, value) in enumerate(assignments.items()):
            _validate_key(key)
            params[f"set_{index}"] = json.dumps(value, ensure_ascii=False)
            params[f"path_{index}"] = key
            expression = (
                f"jsonb_set({expression}, ARRAY[:path_{index}]::text[], "
                f"CAST(:set_{index} AS jsonb), true)"
            )
        for index, key in enumerate(update.get("$unset", {})):
            _validate_key(key)
            params[f"unset_{index}"] = key
            expression = f"({expression} - :unset_{index})"
        sql = f'UPDATE "{self.name}" SET data = {expression} WHERE {conditions}'
        if not many:
            sql += " AND id = (SELECT id FROM " + f'"{self.name}" WHERE {conditions} LIMIT 1)'
        async with self.database.engine.begin() as connection:
            result = await connection.execute(text(sql), params)
            return WriteResult(modified_count=result.rowcount or 0)

    async def delete_one(self, filters: dict) -> WriteResult:
        conditions, params = _compile_filter(filters)
        sql = (
            f'DELETE FROM "{self.name}" WHERE id = '
            f'(SELECT id FROM "{self.name}" WHERE {conditions} LIMIT 1)'
        )
        async with self.database.engine.begin() as connection:
            result = await connection.execute(text(sql), params)
            return WriteResult(deleted_count=result.rowcount or 0)

    async def count_documents(self, filters: Optional[dict] = None) -> int:
        conditions, params = _compile_filter(filters or {})
        async with self.database.engine.connect() as connection:
            result = await connection.execute(
                text(f'SELECT count(*) FROM "{self.name}" WHERE {conditions}'),
                params,
            )
            return int(result.scalar_one())


class PostgresDatabase:
    COLLECTIONS = ("users", "appointments", "treatments", "notifications")

    def __init__(self):
        self.engine: AsyncEngine = create_async_engine(
            _database_url(),
            pool_pre_ping=True,
        )
        for collection in self.COLLECTIONS:
            setattr(self, collection, Collection(self, collection))

    async def create_tables(self):
        async with self.engine.begin() as connection:
            for collection in self.COLLECTIONS:
                await connection.execute(
                    text(
                        f'CREATE TABLE IF NOT EXISTS "{collection}" '
                        "(id varchar(24) PRIMARY KEY, data jsonb NOT NULL)"
                    )
                )
            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email "
                    "ON users ((data->>'email'))"
                )
            )
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_appointments_patient "
                    "ON appointments ((data->>'patient_id'))"
                )
            )
            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_appointment_slot "
                    "ON appointments ((data->>'date'), (data->>'time_slot')) "
                    "WHERE COALESCE(data->>'status', '') <> 'cancelled'"
                )
            )
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_treatments_patient "
                    "ON treatments ((data->>'patient_id'))"
                )
            )
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_notifications_unread "
                    "ON notifications ((data->>'read'), (data->>'created_at'))"
                )
            )

    async def close(self):
        await self.engine.dispose()


_FILTER_OPERATORS = {"$ne", "$in", "$nin", "$lt", "$lte", "$gt", "$gte", "$regex", "$options"}


def _validate_key(key: str):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"Unsupported document field name: {key!r}")


def _compile_filter(filters: dict) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    next_param = 0

    def compile_field(key: str, expected: Any) -> str:
        nonlocal next_param
        _validate_key(key)
        if key == "_id":
            expression = "id"
            json_value = False
        else:
            expression = f"(data -> '{key}')"
            json_value = True
        text_expression = "id" if key == "_id" else f"(data ->> '{key}')"

        def bind(value: Any) -> str:
            nonlocal next_param
            name = f"p{next_param}"
            next_param += 1
            params[name] = json.dumps(value, ensure_ascii=False) if json_value else str(value)
            return f"CAST(:{name} AS jsonb)" if json_value else f":{name}"

        if not isinstance(expected, dict) or not any(k in _FILTER_OPERATORS for k in expected):
            return f"COALESCE({expression}, 'null'::jsonb) = {bind(expected)}" if json_value else f"{expression} = {bind(expected)}"

        parts = []
        for operator, value in expected.items():
            if operator == "$options":
                continue
            if operator == "$regex":
                name = f"p{next_param}"
                next_param += 1
                params[name] = "%" + str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                parts.append(f"{text_expression} ILIKE :{name} ESCAPE '\\'")
            elif operator in {"$in", "$nin"}:
                values = list(value)
                if not values:
                    parts.append("FALSE" if operator == "$in" else "TRUE")
                    continue
                matches = [f"COALESCE({expression}, 'null'::jsonb) = {bind(item)}" if json_value else f"{expression} = {bind(item)}" for item in values]
                joined = "(" + " OR ".join(matches) + ")"
                parts.append(joined if operator == "$in" else f"NOT {joined}")
            elif operator == "$ne":
                comparison = f"COALESCE({expression}, 'null'::jsonb) <> {bind(value)}" if json_value else f"({expression} IS NULL OR {expression} <> {bind(value)})"
                parts.append(comparison)
            elif operator in {"$lt", "$lte", "$gt", "$gte"}:
                sql_operator = {"$lt": "<", "$lte": "<=", "$gt": ">", "$gte": ">="}[operator]
                name = f"p{next_param}"
                next_param += 1
                params[name] = str(value)
                parts.append(f"{text_expression} {sql_operator} :{name}")
            else:
                raise ValueError(f"Unsupported query operator: {operator}")
        return "(" + " AND ".join(parts) + ")"

    def compile_group(group: dict) -> str:
        clauses = []
        for key, expected in group.items():
            if key == "$or":
                clauses.append("(" + " OR ".join(compile_group(item) for item in expected) + ")")
            else:
                clauses.append(compile_field(key, expected))
        return " AND ".join(clauses) if clauses else "TRUE"

    return compile_group(filters), params


def _document(document_id: str, data: dict) -> dict:
    return {"_id": document_id, **data}


def parse_document_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", value):
        raise ValueError("Invalid document ID")
    return value.lower()


db = PostgresDatabase()
