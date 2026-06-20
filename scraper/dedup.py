"""
dedup.py — cross-source md5 deduplication.

Boorus share a LOT of images (Gelbooru/Danbooru/r34 overlap is ~30-40%,
per PLAN.md §4 Dedup). Since every post carries an md5 of the file content,
dedup is just: keep a set of seen md5s, drop any post whose md5 is already in it.

We persist the seen set to seen_md5.txt so re-runs of the scraper don't
re-download images we already have. The file is one md5 per line — simple,
grep-able, and survives Python restarts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Iterator

from . import config

log = logging.getLogger(__name__)


class Md5Set:
    """An in-memory set backed by a newline-delimited text file.

    Usage:
        seen = Md5Set.load(config.SEEN_MD5_TXT)
        for post in posts:
            if seen.add(post["md5"]):
                yield post          # first time we see this md5
    """

    def __init__(self) -> None:
        self._set: set[str] = set()

    @classmethod
    def load(cls, path: Path) -> "Md5Set":
        """Load existing md5s from disk. Missing file = empty set."""
        obj = cls()
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    md5 = line.strip().lower()
                    if md5:
                        obj._set.add(md5)
            log.info("loaded %d known md5s from %s", len(obj._set), path)
        return obj

    def __contains__(self, md5: str) -> bool:
        return md5.lower() in self._set

    def __len__(self) -> int:
        return len(self._set)

    def add(self, md5: str) -> bool:
        """Record md5. Returns True if it was new, False if already seen."""
        md5 = md5.lower()
        if md5 in self._set:
            return False
        self._set.add(md5)
        return True

    def save(self, path: Path) -> None:
        """Persist the full set (overwrites)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for md5 in sorted(self._set):
                f.write(md5 + "\n")
        log.info("saved %d md5s -> %s", len(self._set), path)


def dedup(posts: Iterable[dict], seen: Md5Set | None = None) -> Iterator[dict]:
    """Yield posts whose md5 hasn't been seen.

    If `seen` is provided, it's updated in place (and the caller is responsible
    for saving it). If None, a fresh in-memory set is used (not persisted).
    """
    if seen is None:
        seen = Md5Set()
    dropped = 0
    for post in posts:
        md5 = post.get("md5", "").lower()
        if not md5 or md5 in seen:
            dropped += 1
            continue
        seen.add(md5)
        yield post
    if dropped:
        log.info("dedup: dropped %d duplicate posts", dropped)