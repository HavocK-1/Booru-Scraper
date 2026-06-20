"""
metadata.py — generic metadata-fetching skeleton.

The two boorus (Gelbooru, Danbooru) have nearly identical flows:
    1. Build a query URL for a tag + page number.
    2. GET it (throttled, retried).
    3. Parse JSON into a list of normalized post dicts.
    4. Stop when we run out of results OR cross the score threshold.

Rather than duplicate that in two files, we put the loop here and let each
source subclass fill in the source-specific bits (URL building, JSON parsing,
field extraction). This is the Template Method pattern.

The output is a normalized "Post" dict so downstream filter/download code is
source-agnostic. Schema (PLAN.md §4 index.jsonl):
    {
      "source":     "gelbooru" | "danbooru",
      "id":         int,            # site post id
      "md5":        str,            # content hash — primary dedup key
      "file_url":   str,            # full-res image URL
      "sample_url": str | None,     # compressed preview (may equal file_url)
      "tags":       list[str],      # space-split tag list
      "rating":     str,            # "safe" | "questionable" | "explicit"
      "score":      int,            # source's popularity signal
      "tag_query":  str,            # which pass found it (for debugging)
      "width":      int | None,
      "height":     int | None,
      "file_ext":   str | None,
    }
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator

from . import config
from .http_client import session

log = logging.getLogger(__name__)


class MetadataFetcher(ABC):
    """Template Method base class for booru metadata paging.

    Subclasses implement the three abstract methods; `run()` drives the loop.
    """

    def __init__(self, src: config.SourceConfig, min_score: int):
        # Fill in api_key/user_id from environment at construction time.
        self.src = src.env_auth()
        self.default_min_score = min_score
        self.page_size = src.page_size

    def min_score_for(self, tag: str) -> int:
        """Resolve the score floor for a given tag.

        Per-tag overrides live in config.TAG_THRESHOLDS (tag -> {source: score});
        anything not listed falls back to the source default. This lets us relax
        the floor for small/rare tags (e.g. grab all of "obese") while keeping a
        strict cutoff on huge tags like "fat".
        """
        overrides = config.TAG_THRESHOLDS.get(tag)
        if overrides and self.src.name in overrides:
            return overrides[self.src.name]
        return self.default_min_score

    # ------------------------------------------------------------------
    # Abstract bits — each source knows its own URL/JSON shape.
    # ------------------------------------------------------------------
    @abstractmethod
    def build_url(self, tag: str, page: int) -> str:
        """Return the full API URL for one page of one tag."""

    @abstractmethod
    def parse_posts(self, payload: Any) -> list[dict]:
        """Turn raw API JSON into a list of normalized Post dicts."""

    @abstractmethod
    def post_score(self, post: dict) -> int:
        """Extract the popularity signal used for the stop threshold."""

    # ------------------------------------------------------------------
    # Shared loop
    # ------------------------------------------------------------------
    def fetch_pass(self, tag: str) -> Iterator[dict]:
        """Yield normalized posts for one tag, sorted desc, until threshold.

        We page forward (page 0,1,2,...). Because the API sorts by score desc,
        scores are monotonically non-increasing across pages, so the first
        time we see a post below the tag's threshold we can stop the whole
        pass — everything after it is guaranteed worse.
        """
        min_score = self.min_score_for(tag)
        log.info(
            "%s [%s]: using min_score=%d (default %d)",
            self.src.name, tag, min_score, self.default_min_score,
        )
        for page in range(config.MAX_PAGES_PER_PASS):
            url = self.build_url(tag, page)
            try:
                resp = session.get(url, delay=self.src.min_delay_s)
            except Exception as exc:  # network blip after retries exhausted
                log.error("%s: page %d failed: %s", self.src.name, page, exc)
                break

            if resp.status_code != 200:
                # 401 = missing API key (Gelbooru), 403 = banned, 429 handled by retry.
                log.warning("%s: HTTP %d on %s", self.src.name, resp.status_code, url)
                break

            try:
                payload = resp.json()
            except json.JSONDecodeError:
                # Some dapi endpoints (notably rule34.xxx) return HTTP 200 with
                # a plain-text "Missing authentication" body instead of JSON
                # when account creds are missing. Surface that clearly so the
                # user knows to set env vars rather than seeing an empty pass.
                body = resp.text.strip()
                if "auth" in body.lower():
                    log.error(
                        "%s: %s auth required on page %d (set %s_API_KEY / %s_USER_ID)",
                        self.src.name, self.src.name.upper(), page,
                        self.src.name.upper(), self.src.name.upper(),
                    )
                else:
                    log.error("%s: non-JSON response on page %d: %.120s",
                              self.src.name, page, body)
                break

            posts = self.parse_posts(payload)
            if not posts:
                log.info("%s [%s]: no more results at page %d", self.src.name, tag, page)
                break

            stopped = False
            for post in posts:
                if self.post_score(post) < min_score:
                    log.info(
                        "%s [%s]: score %d < %d, stopping pass",
                        self.src.name, tag, self.post_score(post), min_score,
                    )
                    stopped = True
                    break
                # Stamp which pass found it — useful for debugging gender balance.
                post["tag_query"] = tag
                yield post

            if stopped:
                break

            log.info(
                "%s [%s]: page %d ok, %d posts (lowest score %d)",
                self.src.name, tag, page, len(posts), self.post_score(posts[-1]),
            )
            # Small courtesy sleep on top of the throttle, in case the host
            # clock and our clock disagree slightly.
            time.sleep(0.05)

    def fetch_all(self) -> Iterator[dict]:
        """Run a pass for every configured tag and yield all posts."""
        for tag in self.src.tags:
            log.info("%s: starting pass for tag '%s'", self.src.name, tag)
            yield from self.fetch_pass(tag)


# ---------------------------------------------------------------------------
# JSONL sink — write normalized posts to disk for later filtering/download.
# Why JSONL (one JSON object per line) instead of a JSON array?
#   - Streaming: we can append one line at a time without holding all posts
#     in memory. For 10k+ posts that matters.
#   - Resumable: if the scraper crashes at post 7342, the file is still valid
#     up to that point — we don't lose everything to a malformed array.
# ---------------------------------------------------------------------------
def write_jsonl(posts: Iterator[dict], path: Path) -> int:
    """Write posts to `path` as JSONL. Returns count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for post in posts:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")
            count += 1
    log.info("wrote %d posts -> %s", count, path)
    return count


def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield posts from a JSONL file produced by write_jsonl."""
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
