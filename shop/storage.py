"""Supabase Storage for product photos and sub category icons (two buckets: photos and icons).

Plain REST calls with `requests` (already in requirements.txt), so no extra package is needed.
Set SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_BUCKET (photos) and SUPABASE_ICON_BUCKET (icons) in the environment.
When they are not set, enabled() is False and the code keeps saving images in the database exactly like before
(handy for local development and for the test suite).
"""
import logging
import uuid

import requests
from django.conf import settings

log = logging.getLogger(__name__)
TIMEOUT = 30
CACHE = "max-age=31536000"  # every upload gets a brand new file name, so a long cache is safe


PHOTO, ICON = "photo", "icon"  # which bucket a file lives in


class StorageError(Exception):
    pass


def bucket(kind):
    return settings.SUPABASE_ICON_BUCKET if kind == ICON else settings.SUPABASE_BUCKET


def enabled():
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


def _headers(extra=None):
    key = settings.SUPABASE_SERVICE_KEY
    h = {"apikey": key}
    if not key.startswith("sb_"):  # new sb_secret_... keys go in the apikey header only; the old service_role key is a JWT and also goes in Authorization
        h["Authorization"] = f"Bearer {key}"
    h.update(extra or {})
    return h


def _object_url(kind, path):
    return f"{settings.SUPABASE_URL}/storage/v1/object/{bucket(kind)}/{path}"


def public_url(kind, path):
    """Public link of a stored file ('' when there is no file). Both buckets must be public."""
    if not path or not settings.SUPABASE_URL:
        return ""
    return f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket(kind)}/{path}"


def new_path(folder, ext):
    return f"{folder}/{uuid.uuid4().hex}.{ext}"


def upload(kind, path, data, content_type):
    """Saves bytes at `path` and returns the path. Raises StorageError when Supabase says no."""
    try:
        r = requests.post(
            _object_url(kind, path), data=data, timeout=TIMEOUT,
            headers=_headers({"Content-Type": content_type, "x-upsert": "true", "cache-control": CACHE}),
        )
    except requests.RequestException as e:
        log.error("Supabase upload failed (network): %s", e)
        raise StorageError(f"Could not reach the photo storage ({type(e).__name__})") from e
    if r.status_code >= 300:
        log.error("Supabase upload failed (%s): %s", r.status_code, r.text[:300])
        raise StorageError(f"Photo storage refused the upload ({r.status_code})")
    return path


def delete(kind, path):
    """Best effort: a failed delete only leaves an unused file behind, it never breaks the request."""
    if not path or not enabled():
        return
    try:
        r = requests.delete(_object_url(kind, path), headers=_headers(), timeout=TIMEOUT)
        if r.status_code >= 300 and r.status_code != 404:
            log.warning("Supabase delete failed (%s) for %s", r.status_code, path)
    except requests.RequestException as e:
        log.warning("Supabase delete failed (network) for %s: %s", path, e)
