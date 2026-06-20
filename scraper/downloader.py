"""
downloader.py — concurrent image download with verification.

Pipeline step 3 (PLAN.md §5): download the filtered, deduped posts to disk,
named <md5>.<ext>, then verify each file is a real image of acceptable size.

Concurrency model:
  - We use ThreadPoolExecutor because image downloading is I/O-bound (waiting
    on the network), not CPU-bound. Threads release the GIL during socket
    reads, so N threads give near-N speedup for network waits.
  - The shared ThrottledSession already serializes requests *per host*, so
    adding more workers doesn't violate the 1 req/s politeness limit — it just
    lets us overlap the "download from host A" wait with "download from host B".

Verification:
  - After download, we re-hash the file bytes with md5. If it doesn't match
    the post's declared md5, the file is corrupt/tampered — delete and skip.
  - We also check file size >= MIN_FILE_BYTES (drops broken/pixel placeholders).
  - Dimension check would require decoding the image (Pillow); we keep it
    optional here to avoid a Pillow dependency in the core path. The metadata
    already carries width/height for a cheap pre-check (see filters.too_small).

Resizing (PLAN.md: train at 1024):
  - After the md5 check passes, if the longest side exceeds MAX_SAVE_DIM we
    downscale with Pillow's LANCZOS filter, preserving aspect ratio, and
    overwrite the saved file. This happens *after* verification so we never
    accept a corrupt file, and *before* recording local_path so the index
    points at the final on-disk bytes.
  - Important: the md5 is of the *original*, not the resized file. We keep the
    md5 as the filename/key for provenance, not as a hash of what's on disk.
"""

from __future__ import annotations

import hashlib
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from PIL import Image

from . import config
from .http_client import session

log = logging.getLogger(__name__)


def _ext_from_url(url: str, fallback: str | None = None) -> str:
    """Best-effort file extension from a URL or post's file_ext field."""
    # URLs often look like .../abcdef123.jpg?token=... — strip the query string.
    base = url.split("?")[0]
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    if ext and ext.isalnum() and len(ext) <= 5:
        return ext
    if fallback:
        return fallback.lower().lstrip(".")
    return "jpg"  # boorus are overwhelmingly jpg; safe default


def _verify_md5_bytes(data: bytes, expected_md5: str) -> bool:
    """Hash in-memory bytes and compare to the expected md5.

    We verify against the *downloaded* bytes (the original file) rather than
    the on-disk file, because we may resize before writing — see _resize_bytes.
    """
    return hashlib.md5(data).hexdigest().lower() == expected_md5.lower()


def _resize_bytes(data: bytes, max_dim: int) -> tuple[bytes, str | None]:
    """Downscale image so its longest side <= max_dim, preserving aspect ratio.

    Returns (new_bytes, format). If the image is already within the limit (or
    can't be decoded), the original bytes are returned unchanged with format=None.

    Why LANCZOS: it's Pillow's highest-quality downscale filter, recommended
    in their docs for reductions. It's slower than BILINEAR but quality matters
    for a training dataset.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # force decode so we catch truncated/corrupt images here
    except Exception as exc:
        log.debug("resize: could not decode image (%s); keeping original", exc)
        return data, None

    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return data, None  # already small enough

    # Compute new size keeping aspect ratio. round() avoids off-by-one floats.
    scale = max_dim / longest
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    # Pick the output format: keep PNG (preserves transparency), fall back to
    # JPEG for everything else (jpg/gif/webp/etc) to keep file sizes sane.
    fmt = (img.format or "JPEG").upper()
    save_fmt = "PNG" if fmt == "PNG" else "JPEG"

    # Convert modes that the output format can't encode. JPEG has no alpha or
    # palette support, so anything P/LA/RGBA must flatten to RGB. PNG handles
    # those natively, so leave it alone.
    if save_fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    resized = img.resize(new_size, Image.LANCZOS)

    out = io.BytesIO()
    # save() infers format from extension, but we pass it explicitly to be safe.
    resized.save(out, format=save_fmt, quality=95 if save_fmt == "JPEG" else None)
    return out.getvalue(), save_fmt


def download_one(post: dict, dest_dir: Path) -> dict | None:
    """Download a single post's image. Returns an augmented post dict on
    success, or None on failure.

    The returned dict gains a `local_path` field so the index builder knows
    where the file lives on disk.
    """
    md5 = post["md5"]
    ext = _ext_from_url(post["file_url"], post.get("file_ext"))
    path = dest_dir / f"{md5}.{ext}"

    # Skip if already downloaded (resumability — re-runs shouldn't re-fetch).
    if path.exists() and path.stat().st_size >= config.MIN_FILE_BYTES:
        # Trust the existing file; optionally re-verify md5 here.
        post["local_path"] = str(path)
        return post

    try:
        # delay=None uses the session's default per-host throttle (1 req/s).
        # We must NOT pass delay=0.0 — that disables throttling and 6 workers
        # hammer the CDN, which responds with 404 (not 429) under load. The
        # throttle's lock serializes workers per host, so concurrency still
        # helps when downloading from multiple hosts at once.
        resp = session.get(post["file_url"], delay=None, timeout=config.DOWNLOAD_TIMEOUT_S)
        if resp.status_code != 200:
            log.warning("download %s: HTTP %d", md5, resp.status_code)
            return None
        data = resp.content
    except Exception as exc:
        log.warning("download %s: %s", md5, exc)
        return None

    # Size guard before writing — cheap and catches empty/error responses.
    if len(data) < config.MIN_FILE_BYTES:
        log.warning("download %s: too small (%d bytes)", md5, len(data))
        return None

    # Integrity check on the ORIGINAL downloaded bytes (before any resize).
    # The booru's md5 is of the source file, so we must verify before we
    # transform it. Mismatch means a corrupt/poisoned download — skip it.
    if not _verify_md5_bytes(data, md5):
        log.warning("download %s: md5 mismatch, skipping", md5)
        return None

    # Downscale to MAX_SAVE_DIM (1024) longest side, preserving aspect ratio.
    # Returns the original bytes unchanged if already small enough or if the
    # image can't be decoded (we still keep it in that case — md5 already OK).
    if config.MAX_SAVE_DIM > 0:
        data, save_fmt = _resize_bytes(data, config.MAX_SAVE_DIM)
        # If resizing changed the format (e.g. PNG->JPEG), fix the extension
        # so the filename matches the actual bytes on disk.
        if save_fmt == "JPEG" and ext.lower() not in ("jpg", "jpeg"):
            path = dest_dir / f"{md5}.jpg"
        elif save_fmt == "PNG" and ext.lower() != "png":
            path = dest_dir / f"{md5}.png"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    post["local_path"] = str(path)
    return post


def download_all(
    posts: list[dict],
    dest_dir: Path,
    on_success: Callable[[dict], None] | None = None,
    workers: int = config.DOWNLOAD_WORKERS,
) -> tuple[list[dict], int]:
    """Download a list of posts concurrently.

    Args:
        posts: filtered, deduped post dicts.
        dest_dir: where to write <md5>.<ext> files.
        on_success: optional callback (e.g. append to index.jsonl) per post.
        workers: thread pool size.

    Returns:
        (succeeded_posts, failed_count)
    """
    succeeded: list[dict] = []
    failed = 0

    # as_completed yields futures in the order they *finish*, not the order
    # they were submitted. That lets us log progress incrementally and start
    # writing successful downloads immediately rather than waiting for all.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_one, p, dest_dir): p for p in posts}
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            if result is None:
                failed += 1
            else:
                succeeded.append(result)
                if on_success:
                    on_success(result)
            if i % 100 == 0:
                log.info("downloaded %d/%d (failed %d)", i, len(posts), failed)

    log.info("download complete: %d ok, %d failed", len(succeeded), failed)
    return succeeded, failed
