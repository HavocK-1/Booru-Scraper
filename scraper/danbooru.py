"""
danbooru.py — Danbooru metadata fetcher.

Danbooru REST API:
    https://danbooru.donmai.us/posts.json?tags=<tag>+order:favcount&limit=100&page=<n>

Key facts (PLAN.md §1/§2):
  - Anonymous access works but is limited to 1 req/s and 1000 results per tag
    via plain numeric paging. For deeper paging use `page=b<id>` (a/b tags),
    but our score threshold stops us long before 1000 anyway.
  - `order:favcount` sorts by favorite count (cleaner than score — no downvotes).
  - `tags` is space-separated; negations like `-video` exclude tags.
  - Response is a JSON array of post objects.
  - Danbooru's `file_url` may be null for deleted/restricted posts; we skip those.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from . import config
from .metadata import MetadataFetcher

log = logging.getLogger(__name__)


class DanbooruFetcher(MetadataFetcher):
    def __init__(self):
        super().__init__(src=config.DANBOORU, min_score=config.THRESHOLDS.danbooru_fav_count)

    def build_url(self, tag: str, page: int) -> str:
        # Danbooru limits anonymous searches to 2 tags (incl. metatags like
        # order:favcount). `tag order:favcount` already uses both slots, so we
        # CANNOT append `-exclusion` tokens here — that triggers HTTP 422.
        # Blacklist filtering happens client-side in filters.py
        # (has_blacklisted_tag), which is just as effective, only slightly later.
        tags = " ".join(p for p in (tag, self.src.sort_token) if p)
        params: dict[str, Any] = {
            "tags": tags,
            "limit": self.page_size,
            "page": page + 1,  # Danbooru pages are 1-indexed
        }
        return f"{self.src.base_url}?{urlencode(params)}"

    def parse_posts(self, payload: Any) -> list[dict]:
        if not isinstance(payload, list):
            return []

        posts: list[dict] = []
        for r in payload:
            # Danbooru returns the full tag string in "tag_string"; there are
            # also category-split fields (tag_string_general, character, etc.)
            # but we want the union for captioning, so use the combined field.
            tag_str = r.get("tag_string", "")
            tag_list = tag_str.split() if tag_str else []
            file_url = r.get(self.src.file_url_field) or r.get("file_url") or ""
            posts.append(
                {
                    "source": self.src.name,
                    "id": r.get("id"),
                    "md5": r.get(self.src.md5_field, ""),
                    "file_url": file_url,
                    "sample_url": r.get(self.src.sample_url_field) or file_url,
                    "tags": tag_list,
                    "rating": r.get("rating", "unknown"),
                    "score": int(r.get("score", 0) or 0),
                    "fav_count": int(r.get("fav_count", 0) or 0),
                    "width": r.get("image_width"),
                    "height": r.get("image_height"),
                    "file_ext": r.get("file_ext"),
                    "tag_query": "",
                }
            )
        return posts

    def post_score(self, post: dict) -> int:
        # We sort by fav_count (set in config.sort_token=order:favcount), so the
        # threshold must compare against fav_count, not raw score.
        return int(post.get("fav_count", 0))
