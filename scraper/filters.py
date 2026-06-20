"""
filters.py — post-fetch filtering and gender detection.

The metadata fetcher already applies score thresholds and tag exclusions at the
API level, but we re-check here as a safety net (APIs change, fields go null).
This is also where we:
  - drop posts with blacklisted tags that slipped through,
  - drop non-image / too-small files,
  - classify gender for the balance step (PLAN.md §4 "Balance").

Gender classification is heuristic from tags: if a post has any male-focus tag
it's "male", any female-focus tag it's "female", both -> "mixed", neither ->
"unknown". This is deliberately coarse — it's only used to *count* and
rebalance, not to label the final dataset.
"""

from __future__ import annotations

import logging
from typing import Iterable

from . import config

log = logging.getLogger(__name__)


def has_blacklisted_tag(tags: Iterable[str]) -> bool:
    """True if any tag is in TAG_BLACKLIST."""
    tagset = set(tags)
    return bool(tagset & config.TAG_BLACKLIST)


def _ext_from_url(url: str) -> str:
    """Best-effort file extension from a URL (strips query string)."""
    base = url.split("?")[0]
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    # isalnum (not isalpha) so extensions with digits like "mp4" are caught.
    if ext and ext.isalnum() and len(ext) <= 5:
        return ext
    return ""


def is_bad_filetype(file_ext: str | None, file_url: str = "") -> bool:
    """True for video/animation extensions we never want.

    Some sources (notably Gelbooru) leave `file_ext` empty, so we fall back to
    parsing the extension out of `file_url` — otherwise mp4/gif posts slip past
    the filter and 404 at download time.
    """
    ext = (file_ext or "").lower().lstrip(".")
    if not ext and file_url:
        ext = _ext_from_url(file_url)
    if not ext:
        return False
    return ext in config.BAD_FILE_EXTS


def too_small(width: int | None, height: int | None) -> bool:
    """True if either dimension is below MIN_IMAGE_DIM (PLAN.md §2)."""
    if width is None or height is None:
        return False  # can't tell — let the downloader verify by reading bytes
    return width < config.MIN_IMAGE_DIM or height < config.MIN_IMAGE_DIM


def classify_gender(tags: Iterable[str]) -> str:
    """Coarse male/female/mixed/unknown classification from tags."""
    tagset = set(tags)
    has_male = bool(tagset & config.MALE_TAGS)
    has_female = bool(tagset & config.FEMALE_TAGS)
    if has_male and has_female:
        return "mixed"
    if has_male:
        return "male"
    if has_female:
        return "female"
    return "unknown"


def passes(post: dict) -> bool:
    """Return True if a post should be kept, False if it should be dropped.

    Centralizing the keep/drop logic here means the downloader and index
    builder can both call it and stay consistent.
    """
    tags = post.get("tags", [])
    if has_blacklisted_tag(tags):
        return False
    if is_bad_filetype(post.get("file_ext"), post.get("file_url", "")):
        return False
    if too_small(post.get("width"), post.get("height")):
        return False
    if not post.get("file_url"):
        # No downloadable URL (deleted/restricted post) — skip.
        return False
    if not post.get("md5"):
        # Without an md5 we can't dedup; skip rather than risk duplicates.
        return False
    return True


def annotate(post: dict) -> dict:
    """Add derived fields (gender) to a post in place and return it."""
    post["gender"] = classify_gender(post.get("tags", []))
    return post


def balance(posts: list[dict], target_male_ratio: float = 0.4) -> list[dict]:
    """Rebalance male/female representation.

    High-score fat art skews heavily female (PLAN.md §4). To avoid a dataset
    that's 90% BBW, we cap the female share and keep all male/mixed posts.

    `target_male_ratio` is the *minimum* fraction of male+mixed posts we want.
    0.4 means at least 40% male-leaning. We keep every male/mixed post and
    sample females down to (male_count / target_male_ratio * (1 - target_male_ratio)).

    This is a soft heuristic — adjust the ratio to taste.
    """
    males = [p for p in posts if p["gender"] in ("male", "mixed")]
    females = [p for p in posts if p["gender"] == "female"]
    unknown = [p for p in posts if p["gender"] == "unknown"]

    if target_male_ratio <= 0 or target_male_ratio >= 1:
        return posts

    # Desired female count so that males / (males + females) >= target_male_ratio.
    # Solve: males / (males + F) >= r  ->  F <= males * (1-r) / r
    max_females = int(len(males) * (1 - target_male_ratio) / target_male_ratio)
    if len(females) > max_females:
        # Keep the highest-score females (posts are already score-sorted per pass,
        # but across passes order isn't guaranteed, so sort explicitly here).
        females.sort(key=lambda p: p.get("score", 0), reverse=True)
        females = females[:max_females]
        log.info(
            "balance: capped females to %d (males=%d, unknown=%d)",
            len(females), len(males), len(unknown),
        )
    return males + females + unknown
