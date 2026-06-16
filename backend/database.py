"""
Supabase PostgREST database layer.

Talks directly to Supabase PostgREST (/rest/v1/{table}) using the service
role key, which bypasses RLS.  Audio files are stored in Supabase Storage
(/storage/v1/object/{bucket}/{path}).

The public Collection / DB / FindQuery interface is unchanged so server.py
needs no modifications.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
_BASE_URL: str = (
    os.environ.get("SUPABASE_URL")
    or os.environ.get("INSFORGE_URL")
    or ""
).rstrip("/")

_API_KEY: str = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("INSFORGE_API_KEY")
    or ""
)

AUDIO_BUCKET: str = (
    os.environ.get("SUPABASE_AUDIO_BUCKET")
    or os.environ.get("INSFORGE_AUDIO_BUCKET")
    or "audio-files"
)

_HEADERS = {
    "Authorization": f"Bearer {_API_KEY}",
    "apikey": _API_KEY,
    "Content-Type": "application/json",
}


def _records_url(table: str) -> str:
    return f"{_BASE_URL}/rest/v1/{table}"


def _storage_url(path: str = "") -> str:
    return f"{_BASE_URL}/storage/v1{path}"


# ── Filter builder ─────────────────────────────────────────────────────────────

def _build_params(filter_dict: dict) -> dict:
    """Convert a MongoDB-style filter dict to PostgREST query params."""
    params: dict = {}
    for k, v in (filter_dict or {}).items():
        if k.startswith("$"):
            continue
        if isinstance(v, dict):
            for op, val in v.items():
                if op == "$eq":
                    params[k] = f"eq.{val}"
                elif op == "$ne":
                    params[k] = f"neq.{val}"
                elif op == "$gt":
                    params[k] = f"gt.{val}"
                elif op == "$gte":
                    params[k] = f"gte.{val}"
                elif op == "$lt":
                    params[k] = f"lt.{val}"
                elif op == "$lte":
                    params[k] = f"lte.{val}"
                elif op == "$in":
                    params[k] = f"in.({','.join(str(x) for x in val)})"
        else:
            params[k] = f"eq.{v}"
    return params


# ── Deep-merge helpers for $set / $unset ──────────────────────────────────────

def _apply_set(doc: dict, set_dict: dict) -> dict:
    """Return a new doc with $set values applied (supports dot-notation paths)."""
    result = copy.deepcopy(doc)
    for path, value in set_dict.items():
        parts = path.split(".")
        node = result
        for part in parts[:-1]:
            try:
                idx = int(part)
                node = node[idx]
            except (ValueError, TypeError):
                if part not in node or not isinstance(node[part], (dict, list)):
                    node[part] = {}
                node = node[part]
        leaf = parts[-1]
        try:
            leaf_idx = int(leaf)
            node[leaf_idx] = value
        except (ValueError, TypeError):
            node[leaf] = value
    return result


def _apply_unset(doc: dict, unset_dict: dict) -> dict:
    """Return a new doc with $unset keys removed."""
    result = copy.deepcopy(doc)
    for path in unset_dict:
        parts = path.split(".")
        node = result
        for part in parts[:-1]:
            try:
                node = node[int(part)]
            except (ValueError, TypeError, KeyError, IndexError):
                node = node.get(part, {})
        leaf = parts[-1]
        try:
            del node[int(leaf)]
        except (ValueError, TypeError, KeyError, IndexError):
            node.pop(leaf, None)
    return result


# ── HTTP helpers ───────────────────────────────────────────────────────────────

async def _get(table: str, params: dict) -> list:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(_records_url(table), headers=_HEADERS, params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


async def _post(table: str, body: list, prefer: str = "") -> list:
    headers = {**_HEADERS}
    if prefer:
        headers["Prefer"] = prefer
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_records_url(table), headers=headers, json=body)
        r.raise_for_status()
        if r.status_code == 204:
            return body
        data = r.json()
        return data if isinstance(data, list) else (body if not data else [data])


async def _patch(table: str, params: dict, body: dict, prefer: str = "return=representation") -> list:
    headers = {**_HEADERS, "Prefer": prefer}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.patch(_records_url(table), headers=headers, params=params, json=body)
        r.raise_for_status()
        if r.status_code == 204:
            return [body]
        data = r.json()
        return data if isinstance(data, list) else [data]


async def _delete(table: str, params: dict) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(_records_url(table), headers=_HEADERS, params=params)
        r.raise_for_status()


async def _count(table: str, params: dict) -> int:
    headers = {**_HEADERS, "Prefer": "count=exact"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            _records_url(table),
            headers=headers,
            params={**params, "limit": "0"},
        )
        r.raise_for_status()
        # PostgREST returns Content-Range: 0-9/42  (or */42 when limit=0)
        cr = r.headers.get("Content-Range", "")
        if "/" in cr:
            total_str = cr.split("/")[-1]
            if total_str.isdigit():
                return int(total_str)
        return 0


# ── Collection interface ───────────────────────────────────────────────────────

class FindQuery:
    def __init__(self, table: str, params: dict, projection: dict | None = None):
        self._table = table
        self._params = dict(params)
        self._projection = projection
        self._sort_field: str | None = None
        self._sort_dir: str = "asc"
        self._limit_val: int | None = None

    def sort(self, field: str, direction: int = 1) -> "FindQuery":
        self._sort_field = field
        self._sort_dir = "asc" if direction >= 0 else "desc"
        return self

    def limit(self, n: int) -> "FindQuery":
        self._limit_val = n
        return self

    async def to_list(self, length: int | None = None) -> list:
        params = dict(self._params)
        if self._sort_field:
            params["order"] = f"{self._sort_field}.{self._sort_dir}"
        cap = length or self._limit_val or 1000
        params["limit"] = str(cap)
        return await _get(self._table, params)


class Collection:
    def __init__(self, table: str):
        self._table = table

    async def find_one(self, filter_dict: dict, projection: dict | None = None) -> dict | None:
        params = _build_params(filter_dict)
        params["limit"] = "1"
        rows = await _get(self._table, params)
        return rows[0] if rows else None

    def find(self, filter_dict: dict | None = None, projection: dict | None = None) -> FindQuery:
        params = _build_params(filter_dict or {})
        return FindQuery(self._table, params, projection)

    async def insert_one(self, doc: dict) -> dict:
        rows = await _post(self._table, [doc], prefer="return=representation")
        return rows[0] if rows else doc

    async def update_one(self, filter_dict: dict, update: dict) -> None:
        """
        Fetch-merge-patch: required for nested JSONB column updates.
        Supports $set and $unset operators.
        """
        existing = await self.find_one(filter_dict)
        if existing is None:
            return

        result = copy.deepcopy(existing)
        if "$set" in update:
            result = _apply_set(result, update["$set"])
        if "$unset" in update:
            result = _apply_unset(result, update["$unset"])

        params = _build_params(filter_dict)
        await _patch(self._table, params, result)

    async def delete_one(self, filter_dict: dict) -> None:
        row = await self.find_one(filter_dict)
        if row is None:
            return
        params = _build_params(filter_dict)
        params["limit"] = "1"
        await _delete(self._table, params)

    async def delete_many(self, filter_dict: dict) -> None:
        await _delete(self._table, _build_params(filter_dict))

    async def count_documents(self, filter_dict: dict) -> int:
        return await _count(self._table, _build_params(filter_dict))


class DB:
    def __init__(self):
        self.exams         = Collection("exams")
        self.users         = Collection("users")
        self.user_sessions = Collection("user_sessions")
        self.attempts      = Collection("attempts")
        self.audio_files   = Collection("audio_files")


# ── Audio / Storage helpers ────────────────────────────────────────────────────

async def insert_audio_file(audio_id: str, exam_id: str, audio_bytes: bytes, metadata: dict) -> None:
    """Upload audio bytes to Supabase Storage and record metadata in audio_files."""
    object_key = f"{audio_id}.mp3"

    upload_headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "apikey": _API_KEY,
        "Content-Type": "audio/mpeg",
        "x-upsert": "true",
    }

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            _storage_url(f"/object/{AUDIO_BUCKET}/{object_key}"),
            content=audio_bytes,
            headers=upload_headers,
        )
        r.raise_for_status()

    await _post("audio_files", [{
        "audio_id": audio_id,
        "exam_id": exam_id,
        "storage_path": object_key,
        "audio_type": metadata.get("audio_type", "content"),
    }])


async def get_audio_path(audio_id: str) -> str | None:
    rows = await _get("audio_files", {"audio_id": f"eq.{audio_id}", "limit": "1"})
    if not rows:
        return None
    return rows[0].get("storage_path")


def get_audio_public_url(storage_path: str) -> str:
    """Construct public URL for an object in the audio-files bucket."""
    return f"{_BASE_URL}/storage/v1/object/public/{AUDIO_BUCKET}/{storage_path}"
