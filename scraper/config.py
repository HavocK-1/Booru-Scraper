"""
config.py — all tunable knobs in one place.

This mirrors PLAN.md §3 (tags), §2 (quality sort + thresholds), §4 (storage),
and §6 (rate-limit / etiquette). Change values here instead of editing the
scrapers themselves.

NOTE on paths: everything is relative to the *Scraped/* folder (the parent of
this `scraper/` package). We compute it from __file__ so the script works no
matter what directory you launch it from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# __file__ = .../Scraped/scraper/config.py  ->  parents[1] = .../Scraped
SCRAPE_ROOT: Path = Path(__file__).resolve().parents[1]

# Per-source image + metadata directories (PLAN.md §4 storage layout).
GELBOORU_DIR: Path = SCRAPE_ROOT / "gelbooru"
DANBOORU_DIR: Path = SCRAPE_ROOT / "danbooru"
RULE34_DIR: Path = SCRAPE_ROOT / "rule34"
EHENTAI_DIR: Path = SCRAPE_ROOT / "ehentai"

# Unified outputs.
INDEX_JSONL: Path = SCRAPE_ROOT / "index.jsonl"        # final dataset index
SEEN_MD5_TXT: Path = SCRAPE_ROOT / "seen_md5.txt"      # cross-source dedup set
RAW_DIR: Path = SCRAPE_ROOT / "raw_metadata"          # one JSONL per source pass


# ---------------------------------------------------------------------------
# Tag sets (PLAN.md §3)
# ---------------------------------------------------------------------------
# Core fat tags we always query. Each source has its own pass per tag so we can
# sort by score within that tag and stop early once quality drops.
CORE_TAGS: tuple[str, ...] = ("fat", "fat_man", "obese", "bbw", "bbm", "ssbbw", "gainer", "weight_gain", "chubby")


# Tag blacklist. Posts with ANY of these are dropped during filtering.
# PLAN.md §2: top-scored fat results are full of video/animated/3d/photo junk,
# so we must exclude them or the "best" slice is unscrapable clips.
TAG_BLACKLIST: frozenset[str] = frozenset(
    {
        "video",
        "animated",
        "animated_gif",
        "3d",
        "comic",
        "photo",
        "cosplay",
        "ugly_bastard", # fatness not in positive light, not the focus
        "real_life_insert",
        "loli",          # cuz im not a fucking pedo
        "age_difference",
        "fat_suit",     # costume, not actual fat
        "fat_cow",      # unrelated
        # "furry",       # uncomment to enforce pure anime
        # "monochrome", # optional
    }
)

# File extensions we treat as non-image / scrapable-junk and drop.
BAD_FILE_EXTS: frozenset[str] = frozenset({"mp4", "webm", "gif", "swf"})


# ---------------------------------------------------------------------------
# Quality thresholds (PLAN.md §2)
# ---------------------------------------------------------------------------
# These are *lower bounds*. The fetcher sorts by score descending and stops
# paging once it crosses the threshold, so we never even download long-tail
# junk. Tune after a sample pull.
@dataclass(frozen=True)
class Threshold:
    """Score floor below which we stop paging for a given source."""

    gelbooru_score: int = 10          # Gelbooru `score >= 10`
    danbooru_fav_count: int = 50       # Danbooru `fav_count >= 50`
    rule34_score: int = 10             # rule34.xxx `score >= 10`
    ehentai_rating: float = 4.0        # e-hentai gallery star rating
    ehentai_favcount: int = 100        # e-hentai gallery fav count


THRESHOLDS = Threshold()

# Per-tag quality overrides. Keys are tag names; values are dicts mapping
# source name -> min score for that tag on that source. Tags NOT listed here
# fall back to the source default in THRESHOLDS above.
#
# Use this to relax the floor for small/rare tags where you want maximum
# coverage even at low scores, or to tighten it for huge tags full of junk.
# A value of 0 means "accept everything with score >= 0" (i.e. grab all).
#
# Example: "obese" is a small tag, so we drop the floor to 0 to collect as
# much as possible instead of stopping at the default fav_count>=50.
TAG_THRESHOLDS: dict[str, dict[str, int]] = {
    # Small / rare fat tags — grab everything.
    "obese":       {"gelbooru": 0,  "danbooru": 0, "rule34": 0},
    "ssbbw":       {"gelbooru": 0,  "danbooru": 0, "rule34": 0},
    "gainer":      {"gelbooru": 0,  "danbooru": 0, "rule34": 0},
    "weight_gain": {"gelbooru": 0,  "danbooru": 0, "rule34": 0},
    "fat_man":     {"gelbooru": 10,  "danbooru": 50, "rule34": 10},
    "bbm":         {"gelbooru": 10,  "danbooru": 50, "rule34": 10},
    # "fat", "bbw", "chubby" etc. use the source default (not listed here).
}

# Minimum image dimensions. Drops thumbnails / tiny pixiv previews.
MIN_IMAGE_DIM: int = 512
# Minimum file size in bytes. Drops broken/pixel placeholders.
MIN_FILE_BYTES: int = 20_000
# Cap the longest side of saved images to this many pixels (PLAN.md: train at
# 1024). Larger originals are downscaled *after* md5 verification, preserving
# aspect ratio. Set to 0 to disable resizing and keep originals as-is.
MAX_SAVE_DIM: int = 1024


# ---------------------------------------------------------------------------
# Per-source settings
# ---------------------------------------------------------------------------
# A dataclass per source keeps the fetcher code generic: each scraper just
# reads its own SourceConfig and the base class handles paging/throttling.
@dataclass(frozen=True)
class SourceConfig:
    name: str
    base_url: str
    # tags to run a separate pass for. We do NOT combine them into one query
    # because score-ranking is per-tag (a high-score "fat_man" post would be
    # drowned out if merged with "fat" which is mostly female).
    tags: tuple[str, ...]
    page_size: int                # results per request
    min_delay_s: float            # throttle between requests (PLAN.md §6)
    # sort directive injected into the query string, e.g. "sort:score:desc"
    sort_token: str
    # field names on the post object (the API shape differs per source)
    score_field: str
    md5_field: str
    file_url_field: str
    sample_url_field: str
    # API key/user_id (Gelbooru now 401s without them; Danbooru optional).
    # Read from env so secrets never live in the repo.
    api_key: str = ""
    user_id: str = ""

    def env_auth(self) -> "SourceConfig":
        """Return a copy with api_key/user_id filled from env vars.

        We keep this a method (not __post_init__) so config can be created
        with empty creds for tests, then `env_auth()` called right before use.
        """
        env_prefix = self.name.upper()
        return SourceConfig(
            **{
                **self.__dict__,
                "api_key": os.environ.get(f"{env_prefix}_API_KEY", ""),
                "user_id": os.environ.get(f"{env_prefix}_USER_ID", ""),
            }
        )


GELBOORU = SourceConfig(
    name="gelbooru",
    base_url="https://gelbooru.com/index.php?page=dapi&s=post&q=index",
    tags=CORE_TAGS,            # fat, fat_man, obese, bbw, bbm
    page_size=100,
    min_delay_s=1.0,           # ~1 req/s (PLAN.md §6)
    sort_token="sort:score:desc",
    score_field="score",
    md5_field="md5",
    file_url_field="file_url",
    sample_url_field="sample_url",
)

DANBOORU = SourceConfig(
    name="danbooru",
    base_url="https://danbooru.donmai.us/posts.json",
    tags=CORE_TAGS,
    page_size=100,
    min_delay_s=1.0,           # 1 req/s anonymous (PLAN.md §6)
    # Danbooru exposes `order:score` and `order:favcount` as tag tokens.
    # fav_count is cleaner (no downvote noise) — PLAN.md §2.
    sort_token="order:favcount",
    score_field="fav_count",
    md5_field="md5",
    file_url_field="file_url",
    sample_url_field="large_file_url",
)

# rule34.xxx runs Gelbooru software, so its `dapi` JSON API is nearly identical
# to Gelbooru's: same tag tokens (sort:score:desc), same post field names
# (score/md5/file_url/sample_url), same 0-indexed `pid` paging.
# Difference: account auth is mandatory (anonymous JSON calls 403 now).
# Set env vars RULE34_API_KEY / RULE34_USER_ID before running. Unlike Gelbooru
# the auth is passed as `api_key` + `user_id` query params, same as GELBOORU.
# Note: rule34 exposes only `score` (upvotes-downvotes) — no fav_count and no
# download counter, so score-desc is the only meaningful popularity sort.
RULE34 = SourceConfig(
    name="rule34",
    base_url="https://api.rule34.xxx/index.php?page=dapi&s=post&q=index",
    tags=CORE_TAGS,
    page_size=100,
    min_delay_s=1.0,           # ~1 req/s (PLAN.md §6)
    sort_token="sort:score:desc",
    score_field="score",
    md5_field="md5",
    file_url_field="file_url",
    sample_url_field="sample_url",
)


# ---------------------------------------------------------------------------
# Download / concurrency
# ---------------------------------------------------------------------------
# ThreadPoolExecutor workers for image downloads. 4-8 is polite and fast.
DOWNLOAD_WORKERS: int = 8
# Max retries per file on network errors.
DOWNLOAD_RETRIES: int = 3
# Hard timeout per image download (seconds).
DOWNLOAD_TIMEOUT_S: float = 60.0

# Deep-paging safety: Danbooru refuses `page > 1000` for plain numeric paging.
# We cap total pages per pass to avoid spinning forever on huge tags.
MAX_PAGES_PER_PASS: int = 300


# ---------------------------------------------------------------------------
# Gender balance (PLAN.md §4 "Balance")
# ---------------------------------------------------------------------------
# Tags that indicate a male vs female subject. Used to count and rebalance.
MALE_TAGS: frozenset[str] = frozenset({"fat_man", "bbm", "1boy", "male_focus"})
FEMALE_TAGS: frozenset[str] = frozenset({"bbw", "1girl", "female_focus", "ssbbw"})


# ---------------------------------------------------------------------------
# Network identity
# ---------------------------------------------------------------------------
# A descriptive UA is polite and helps site admins contact you if something
# breaks. Some boorus block generic python-requests UA strings.
USER_AGENT: str = (
    "FatDatasetScraper/0.1 "
    "(research; contact: anon) "
    "python-requests"
)