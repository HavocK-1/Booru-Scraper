"""
rule34.py — rule34.xxx metadata fetcher.

rule34.xxx runs Gelbooru software, so its `dapi` endpoint is nearly identical
to Gelbooru's:
    https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1
    &tags=<tag>+sort:score:desc&limit=100&pid=<page>&api_key=...&user_id=...

Key facts (PLAN.md §1/§2):
  - JSON output now REQUIRES account auth (anonymous = HTTP 403). Register at
    https://rule34.xxx/index.php?page=account&s=options and set env vars
    RULE34_API_KEY / RULE34_USER_ID.
  - `pid` is 0-indexed and multiplies by `limit` internally (pid=2 -> offset 200).
  - `sort:score:desc` is a tag-token, appended inside `tags=`, not a separate param.
  - rule34 exposes only `score` (upvotes - downvotes) — there is no fav_count or
    download counter, so score-desc is the only meaningful popularity sort.
  - Response JSON is a bare list of post objects when results exist, and `[]`
    (or a dict with "@attributes") when empty — same shape as Gelbooru.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from . import config
from .metadata import MetadataFetcher

log = logging.getLogger(__name__)


class Rule34Fetcher(MetadataFetcher):
    def __init__(self):
        super().__init__(src=config.RULE34, min_score=config.THRESHOLDS.rule34_score)

    def build_url(self, tag: str, page: int) -> str:
        # rule34 wants tags space-separated but URL-encoded as '+'.
        # We append the sort token so results come back score-descending.
        tags = f"{tag} {self.src.sort_token}".strip()
        # NOTE: base_url already contains page=dapi&s=post&q=index, so we only
        # append the *remaining* params here. Repeating page/s/q would mangle
        # the URL (rule34 would see duplicate keys and the request breaks).
        params: dict[str, Any] = {
            "json": "1",
            "tags": tags,
            "limit": self.page_size,
            "pid": page,  # 0-indexed page
        }
        # Auth is mandatory now; omit only if you want to see the 403.
        if self.src.api_key and self.src.user_id:
            params["api_key"] = self.src.api_key
            params["user_id"] = self.src.user_id
        return f"{self.src.base_url}&{urlencode(params)}"

    def parse_posts(self, payload: Any) -> list[dict]:
        # rule34 returns a list on success. An empty result can be [] or a
        # dict like {"@attributes": {...}} — handle both defensively.
        if isinstance(payload, list):
            raw = payload
        elif isinstance(payload, dict) and "post" in payload:
            # Some versions wrap results under a "post" key.
            raw = payload["post"] if isinstance(payload["post"], list) else [payload["post"]]
        else:
            return []

        posts: list[dict] = []
        for r in raw:
            # rule34 tags come as a single space-separated string.
            tags = r.get("tags", "")
            tag_list = tags.split() if isinstance(tags, str) else list(tags)
            posts.append(
                {
                    "source": self.src.name,
                    "id": r.get("id"),
                    "md5": r.get(self.src.md5_field, ""),
                    "file_url": r.get(self.src.file_url_field, "") or "",
                    "sample_url": r.get(self.src.sample_url_field) or r.get(self.src.file_url_field, ""),
                    "tags": tag_list,
                    "rating": r.get("rating", "unknown"),
                    "score": int(r.get(self.src.score_field, 0) or 0),
                    "width": r.get("width"),
                    "height": r.get("height"),
                    "file_ext": r.get("file_ext"),
                    "tag_query": "",  # filled by fetch_pass
                }
            )
        return posts

    def post_score(self, post: dict) -> int:
        return int(post.get("score", 0))
