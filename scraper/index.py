"""
index.py — build the unified index.jsonl (PLAN.md §4, §5 step 6).

After all sources are downloaded, we walk the per-source image directories and
emit one JSON line per image with everything a downstream trainer needs:

    {
      "image":     "gelbooru/abcd1234.jpg",
      "source":    "gelbooru",
      "md5":       "abcd1234...",
      "tags":      ["fat", "1girl", ...],
      "rating":    "explicit",
      "gender":    "female",
      "score":     42,
      "tag_query": "fat",
      "caption":   null            # filled later by the captioning step
    }

The `caption` field is intentionally null here — captioning is a separate
pipeline step (PLAN.md §5 step 5) that calls a multimodal model using
../prompt.txt. Keeping it null lets the trainer detect "not yet captioned".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from . import config

log = logging.getLogger(__name__)


def build_index(posts: Iterable[dict], index_path: Path = config.INDEX_JSONL) -> int:
    """Write index.jsonl from the list of successfully-downloaded posts.

    `posts` should already have `local_path` set (from downloader.download_one).
    Paths are stored relative to SCRAPE_ROOT so the index is portable.
    """
    index_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with index_path.open("w", encoding="utf-8") as f:
        for post in posts:
            local = post.get("local_path")
            if not local:
                continue
            # Store path relative to SCRAPE_ROOT for portability.
            try:
                rel = str(Path(local).resolve().relative_to(config.SCRAPE_ROOT.resolve()))
            except ValueError:
                rel = str(local)  # fallback: absolute if outside SCRAPE_ROOT

            entry = {
                "image": rel.replace("\\", "/"),  # normalize Windows backslashes
                "source": post.get("source"),
                "md5": post.get("md5"),
                "tags": post.get("tags", []),
                "rating": post.get("rating"),
                "gender": post.get("gender", "unknown"),
                "score": post.get("score", 0),
                "tag_query": post.get("tag_query", ""),
                "caption": None,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            count += 1
    log.info("wrote %d entries -> %s", count, index_path)
    return count
