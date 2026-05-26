"""
InsForge database layer — MongoDB-compatible shim.

Uses the InsForge REST API (/api/database/records/{table}) instead of the
Supabase Python SDK (which requires PostgREST at /rest/v1/ — not available on
InsForge). Provides the same async API as before so server.py is unchanged.

Audio files go through the InsForge Storage API (/api/storage/buckets/).
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
    os.environ.get("INSFORGE_URL")
    or os.environ.get("SUPABASE_URL")
    or ""
)
_API_KEY: str = (
    os.environ.get("INSFORGE_API_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or ""
)
AUDIO_BUCKET: str = (
    os.environ.get("INSFORGE_AUDIO_BUCKET")
    or os.environ.get("SUPABASE_AUDIO_BUCKET")
    or "audio-files"
)

_HEADERS = {
    "Authorization": f"Bearer {_API_KEY}",
    "Content-Type": "application/json",
}


def _records_url(table: str) -> str:
    return f"{_BASE_URL}/api/database/records/{table}"


def _storage_url(path: str = "") -> str:
    return f"{_BASE_URL}/api/storage{path}"


# ── Filter builder ─────────────────────────────────────────────────────────────

def _build_params(filter_dict: dict) -> dict:
    """Convert a MongoDB-style filter dict to InsForge query params."""
    params: dict = {}
    for k, v in (filter_dict or {}).items():
        if k.startswith("$"):
            continue  # skip top-level operators ($or etc — not used here)
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
        return r.json() or []


async def _post(table: str, body: list, prefer: str = "") -> list:
    headers = {**_HEADERS}
    if prefer:
        headers["Prefer"] = prefer
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_records_url(table), headers=headers, json=body)
        r.raise_for_status()
        return r.json() or []


async def _patch(table: str, params: dict, body: dict, prefer: str = "return=representation") -> list:
    headers = {**_HEADERS, "Prefer": prefer}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.patch(_records_url(table), headers=headers, params=params, json=body)
        r.raise_for_status()
        return r.json() or []


async def _delete(table: str, params: dict) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(_records_url(table), headers=_HEADERS, params=params)
        r.raise_for_status()


async def _count(table: str, params: dict) -> int:
    headers = {**_HEADERS, "Prefer": "count=exact"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(_records_url(table), headers=headers, params={**params, "limit": "0"})
        r.raise_for_status()
        total = r.headers.get("X-Total-Count", "0")
        return int(total)


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
        rows = await _get(self._table, params)
        return rows


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
        Applies $set / $unset operators by fetching the existing doc,
        merging changes, and PATCHing it back — required for JSONB columns.
        """
        existing = await self.find_one(filter_dict)
        if existing is None:
            return

        result = copy.deepcopy(existing)
        if "$set" in update:
            result = _apply_set(result, update["$set"])
        if "$unset" in update:
            result = _apply_unset(result, update["$unset"])

        # Remove internal InsForge meta-fields that can't be patched
        result.pop("_id", None)

        params = _build_params(filter_dict)
        await _patch(self._table, params, result)

    async def delete_one(self, filter_dict: dict) -> None:
        row = await self.find_one(filter_dict)
        if row is None:
            return
        # Build params from original filter — limit to 1 via a unique field if possible
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
    """Upload audio bytes to InsForge Storage and record the reference in audio_files."""
    filename = f"{audio_id}.mp3"

    # Step 1: get upload strategy
    async with httpx.AsyncClient(timeout=30) as c:
        strat_r = await c.post(
            _storage_url(f"/buckets/{AUDIO_BUCKET}/upload-strategy"),
            headers=_HEADERS,
            json={"filename": filename, "contentType": "audio/mpeg", "size": len(audio_bytes)},
        )
        strat_r.raise_for_status()
        strat = strat_r.json()

    method = strat.get("method", "direct")
    object_key: str

    if method == "presigned":
        # S3 presigned upload
        upload_url = strat["uploadUrl"]
        fields = strat.get("fields", {})
        object_key = fields.get("key", f"{AUDIO_BUCKET}/{filename}")

        async with httpx.AsyncClient(timeout=60) as c:
            form = {k: (None, v) for k, v in fields.items()}
            form["file"] = (filename, audio_bytes, "audio/mpeg")
            r = await c.post(upload_url, files=form)
            r.raise_for_status()

        # Step 3: confirm upload (S3 only)
        async with httpx.AsyncClient(timeout=30) as c:
            conf_r = await c.post(
                _storage_url(f"/buckets/{AUDIO_BUCKET}/upload-confirm"),
                headers=_HEADERS,
                json={"key": object_key},
            )
            conf_r.raise_for_status()
    else:
        # Direct upload
        upload_url = strat.get("uploadUrl", _storage_url(f"/buckets/{AUDIO_BUCKET}/objects/{filename}"))
        object_key = strat.get("objectKey", filename)

        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.put(
                upload_url,
                content=audio_bytes,
                headers={**_HEADERS, "Content-Type": "audio/mpeg"},
            )
            r.raise_for_status()

    # Record in audio_files table
    await _post("audio_files", [{
        "audio_id": audio_id,
        "exam_id": exam_id,
        "file_path": object_key,
        "bucket": AUDIO_BUCKET,
        **{k: v for k, v in metadata.items()},
    }])


async def get_audio_path(audio_id: str) -> str | None:
    rows = await _get("audio_files", {"audio_id": f"eq.{audio_id}", "limit": "1"})
    if not rows:
        return None
    return rows[0].get("file_path")


async def get_audio_public_url(audio_id: str) -> str | None:
    path = await get_audio_path(audio_id)
    if not path:
        return None
    # Public URL for InsForge storage
    return f"{_BASE_URL}/api/storage/buckets/{AUDIO_BUCKET}/objects/{path}?download=false"
