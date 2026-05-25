"""
Supabase database layer — MongoDB-compatible shim.

Provides a `DB` class whose collection attributes expose the same
async API as Motor (find_one, insert_one, update_one / $set / $unset,
delete_one, delete_many, count_documents, find().sort().to_list()).
This lets server.py keep all its `db.collection.method()` calls unchanged.

Audio files are the exception: call `insert_audio_file()` directly so
bytes go to Supabase Storage instead of a base64 column.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client

logger = logging.getLogger(__name__)

AUDIO_BUCKET: str = os.environ.get("SUPABASE_AUDIO_BUCKET", "audio-files")

_supabase: Client | None = None


def _get() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _supabase


async def _run(fn) -> Any:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, fn)
    except Exception as e:
        # Supabase drops HTTP/2 connections after idle; recreate client and retry once
        if "RemoteProtocolError" in type(e).__name__ or "ConnectionTerminated" in str(e):
            global _supabase
            _supabase = None
            logger.warning("Supabase connection dropped, reconnecting...")
            return await loop.run_in_executor(None, fn)
        raise


# ── JSONB path helpers ────────────────────────────────────────────────────────

def _set_at_path(obj: Any, path: list, value: Any) -> None:
    for key in path[:-1]:
        try:
            key = int(key)
        except (ValueError, TypeError):
            pass
        obj = obj[key] if isinstance(obj, list) else obj.setdefault(str(key), {})
    final = path[-1]
    try:
        final = int(final)
    except (ValueError, TypeError):
        pass
    if isinstance(obj, list):
        obj[final] = value
    else:
        obj[str(final)] = value


def _del_at_path(obj: Any, path: list) -> None:
    for key in path[:-1]:
        try:
            key = int(key)
        except (ValueError, TypeError):
            pass
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj.get(str(key), {})
    final = path[-1]
    try:
        final = int(final)
        if isinstance(obj, list):
            obj[final] = None
    except (ValueError, TypeError):
        if isinstance(obj, dict) and str(final) in obj:
            del obj[str(final)]


# ── Query builder (returned by Collection.find()) ────────────────────────────

class FindQuery:
    def __init__(self, table: str, filter_: dict):
        self._table = table
        self._filter = filter_
        self._sort_field: str | None = None
        self._sort_desc = True

    def sort(self, field: str, direction: int) -> "FindQuery":
        self._sort_field = field
        self._sort_desc = direction == -1
        return self

    async def to_list(self, max_count: int) -> list:
        table = self._table
        filter_ = self._filter
        sort_field = self._sort_field
        sort_desc = self._sort_desc

        def _():
            q = _get().table(table).select("*")
            q = _apply_filter(q, filter_)
            if sort_field:
                q = q.order(sort_field, desc=sort_desc)
            r = q.limit(max_count).execute()
            return r.data or []

        return await _run(_)


def _apply_filter(q, filter_: dict):
    for k, v in filter_.items():
        if isinstance(v, dict):
            if "$in" in v:
                vals = v["$in"]
                non_null = [x for x in vals if x is not None]
                has_null = any(x is None for x in vals)
                if non_null and has_null:
                    q = q.or_(
                        f"{k}.in.({','.join(str(x) for x in non_null)}),{k}.is.null"
                    )
                elif non_null:
                    q = q.in_(k, non_null)
                elif has_null:
                    q = q.is_(k, "null")
        elif v is None:
            q = q.is_(k, "null")
        elif "." in k:
            # Dot-notation → JSONB text access: subscription.tier → subscription->>'tier'
            col, jsonb_key = k.split(".", 1)
            q = q.filter(f"{col}->>{jsonb_key!r}", "eq", str(v))
        else:
            q = q.eq(k, v)
    return q


# ── DeleteResult shim ─────────────────────────────────────────────────────────

class DeleteResult:
    def __init__(self, count: int):
        self.deleted_count = count


# ── Collection ────────────────────────────────────────────────────────────────

class Collection:
    def __init__(self, table: str):
        self._table = table

    async def find_one(self, filter_: dict, projection: dict = None) -> dict | None:
        table = self._table

        def _():
            q = _apply_filter(_get().table(table).select("*"), filter_)
            r = q.limit(1).execute()
            return r.data[0] if r.data else None

        return await _run(_)

    async def insert_one(self, doc: dict) -> None:
        table = self._table
        await _run(lambda: _get().table(table).insert(doc).execute())

    async def update_one(self, filter_: dict, update: dict) -> None:
        pk_field, pk_value = next(iter(filter_.items()))

        if "$set" in update:
            await self._apply_set(pk_field, pk_value, update["$set"])
        if "$unset" in update:
            await self._apply_unset(pk_field, pk_value, update["$unset"])
        if not any(k.startswith("$") for k in update):
            await self._apply_set(pk_field, pk_value, update)

    async def _apply_set(self, pk_field: str, pk_value: Any, updates: dict) -> None:
        flat: dict[str, Any] = {}
        nested: dict[str, list[tuple[str, Any]]] = {}

        for key, value in updates.items():
            if "." in key:
                col, subpath = key.split(".", 1)
                nested.setdefault(col, []).append((subpath, value))
            else:
                flat[key] = value

        table = self._table

        if flat:
            await _run(
                lambda: _get().table(table).update(flat).eq(pk_field, pk_value).execute()
            )

        for col, path_updates in nested.items():
            col_snap = col

            def _fetch(col_snap=col_snap):
                r = (
                    _get()
                    .table(table)
                    .select(col_snap)
                    .eq(pk_field, pk_value)
                    .limit(1)
                    .execute()
                )
                return r.data[0].get(col_snap) or {} if r.data else {}

            col_data = await _run(_fetch)

            for subpath, value in path_updates:
                _set_at_path(col_data, subpath.split("."), value)

            snap = col_data

            await _run(
                lambda snap=snap, col_snap=col_snap: _get()
                .table(table)
                .update({col_snap: snap})
                .eq(pk_field, pk_value)
                .execute()
            )

    async def _apply_unset(self, pk_field: str, pk_value: Any, unsets: dict) -> None:
        flat_nulls: list[str] = []
        nested: dict[str, list[str]] = {}

        for key in unsets:
            if "." in key:
                col, subpath = key.split(".", 1)
                nested.setdefault(col, []).append(subpath)
            else:
                flat_nulls.append(key)

        table = self._table

        if flat_nulls:
            await _run(
                lambda: _get()
                .table(table)
                .update({k: None for k in flat_nulls})
                .eq(pk_field, pk_value)
                .execute()
            )

        for col, paths in nested.items():
            col_snap = col

            def _fetch(col_snap=col_snap):
                r = (
                    _get()
                    .table(table)
                    .select(col_snap)
                    .eq(pk_field, pk_value)
                    .limit(1)
                    .execute()
                )
                return r.data[0].get(col_snap) or {} if r.data else {}

            col_data = await _run(_fetch)

            for path in paths:
                _del_at_path(col_data, path.split("."))

            snap = col_data
            await _run(
                lambda snap=snap, col_snap=col_snap: _get()
                .table(table)
                .update({col_snap: snap})
                .eq(pk_field, pk_value)
                .execute()
            )

    async def delete_one(self, filter_: dict) -> None:
        table = self._table
        pk_field, pk_value = next(iter(filter_.items()))
        await _run(
            lambda: _get().table(table).delete().eq(pk_field, pk_value).execute()
        )

    async def delete_many(self, filter_: dict) -> DeleteResult:
        table = self._table
        pk_field, pk_value = next(iter(filter_.items()))
        r = await _run(
            lambda: _get().table(table).delete().eq(pk_field, pk_value).execute()
        )
        return DeleteResult(len(r.data) if r.data else 0)

    async def count_documents(self, filter_: dict) -> int:
        table = self._table

        def _():
            q = _apply_filter(
                _get().table(table).select("*", count="exact"), filter_
            )
            r = q.execute()
            return r.count or 0

        return await _run(_)

    def find(self, filter_: dict, projection: dict = None) -> FindQuery:
        return FindQuery(self._table, filter_)


# ── DB facade (drop-in for `motor` db object) ─────────────────────────────────

class DB:
    def __init__(self):
        self.users         = Collection("users")
        self.user_sessions = Collection("user_sessions")
        self.exams         = Collection("exams")
        self.audio_files   = Collection("audio_files")
        self.attempts      = Collection("attempts")


# ── Audio storage helpers ─────────────────────────────────────────────────────

async def insert_audio_file(
    audio_id: str,
    exam_id: str,
    audio_bytes: bytes,
    meta: dict,
) -> None:
    """Upload audio to Supabase Storage and record metadata in audio_files table."""
    storage_path = f"{exam_id}/{audio_id}.mp3"

    def _():
        _get().storage.from_(AUDIO_BUCKET).upload(
            storage_path,
            audio_bytes,
            {"content-type": "audio/mpeg", "upsert": "true"},
        )
        _get().table("audio_files").insert(
            {
                "audio_id": audio_id,
                "exam_id": exam_id,
                "storage_path": storage_path,
                "format": "mp3",
                "created_at": datetime.now(timezone.utc).isoformat(),
                **{k: v for k, v in meta.items() if v is not None},
            }
        ).execute()

    await _run(_)


async def get_audio_path(audio_id: str) -> str | None:
    def _():
        r = (
            _get()
            .table("audio_files")
            .select("storage_path")
            .eq("audio_id", audio_id)
            .limit(1)
            .execute()
        )
        return r.data[0]["storage_path"] if r.data else None

    return await _run(_)


def get_audio_public_url(storage_path: str) -> str:
    return _get().storage.from_(AUDIO_BUCKET).get_public_url(storage_path)
