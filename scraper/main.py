"""
main.py — CLI entry point that wires the pipeline together.

Pipeline (PLAN.md §5):
    1. fetch  metadata  (per source)  -> raw_metadata/<source>_<tag>.jsonl
    2. filter             (blacklist, size, gender)
    3. dedup              (md5 across all sources)
    4. download           (concurrent, verified) -> <source>/<md5>.<ext>
    5. build index        -> index.jsonl

Usage:
    # Full run (metadata + download + index) for both sources:
    python -m scraper.main

    # Just Gelbooru, metadata only (no image downloads) — good for a dry run
    # to inspect what would be downloaded:
    python -m scraper.main --source gelbooru --metadata-only

    # Both sources, but skip Gelbooru (no API key yet):
    python -m scraper.main --source danbooru

Env vars (PLAN.md §6):
    GELBOORU_API_KEY, GELBOORU_USER_ID   # required for Gelbooru JSON API now
    DANBOORU_API_KEY, DANBOORU_LOGIN      # optional, raises Danbooru limits
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config
from .dedup import Md5Set, dedup
from .downloader import download_all
from .filters import annotate, balance, passes
from .index import build_index
from .metadata import read_jsonl, write_jsonl

log = logging.getLogger("scraper")


# ---------------------------------------------------------------------------
# Source registry — maps a name to (fetcher class, output dir, raw jsonl path).
# Adding a new source = add one entry here and implement a MetadataFetcher.
# ---------------------------------------------------------------------------
SOURCES: dict[str, tuple[str, Path]] = {
    # name: (module attr for fetcher, image dest dir)
    "gelbooru": ("gelbooru.GelbooruFetcher", config.GELBOORU_DIR),
    "danbooru": ("danbooru.DanbooruFetcher", config.DANBOORU_DIR),
    "rule34": ("rule34.Rule34Fetcher", config.RULE34_DIR),
}


def get_fetcher(name: str):
    """Import and instantiate a fetcher by name (lazy import avoids pulling
    network deps when the user only wants one source)."""
    if name == "gelbooru":
        from .gelbooru import GelbooruFetcher
        return GelbooruFetcher()
    if name == "danbooru":
        from .danbooru import DanbooruFetcher
        return DanbooruFetcher()
    if name == "rule34":
        from .rule34 import Rule34Fetcher
        return Rule34Fetcher()
    raise ValueError(f"unknown source: {name}")


def cmd_metadata(source: str) -> Path:
    """Step 1: fetch metadata for one source and write raw JSONL. Returns path."""
    fetcher = get_fetcher(source)
    raw_path = config.RAW_DIR / f"{source}.jsonl"
    count = write_jsonl(fetcher.fetch_all(), raw_path)
    log.info("[%s] fetched %d posts", source, count)
    return raw_path


def load_and_filter(source: str, raw_path: Path) -> list[dict]:
    """Steps 2: read raw JSONL, apply filters, annotate gender."""
    kept: list[dict] = []
    dropped = 0
    for post in read_jsonl(raw_path):
        if not passes(post):
            dropped += 1
            continue
        kept.append(annotate(post))
    log.info("[%s] kept %d, dropped %d after filtering", source, len(kept), dropped)
    return kept


def run(source: str, metadata_only: bool, skip_metadata: bool, balance_ratio: float = 0.4) -> None:
    """Run the full pipeline for one source."""
    raw_path = config.RAW_DIR / f"{source}.jsonl"

    # Step 1: metadata (skippable if already on disk from a prior run).
    if not skip_metadata and not raw_path.exists():
        cmd_metadata(source)
    elif not raw_path.exists():
        log.error("[%s] no raw metadata at %s (run without --skip-metadata first)", source, raw_path)
        return

    # Step 2: filter + annotate.
    posts = load_and_filter(source, raw_path)
    if not posts:
        log.warning("[%s] no posts survived filtering", source)
        return

    if metadata_only:
        log.info("[%s] --metadata-only: stopping after filter. %d posts ready.", source, len(posts))
        return

    # Step 3: dedup (cross-source). Load the persistent seen set so we don't
    # re-download images fetched by other sources in previous runs.
    seen = Md5Set.load(config.SEEN_MD5_TXT)
    deduped = list(dedup(posts, seen))
    log.info("[%s] %d posts after dedup", source, len(deduped))

    # Step 3b: gender balance (PLAN.md §4). High-score fat art skews female,
    # so cap the female share to keep male/mixed representation healthy.
    balanced = balance(deduped, target_male_ratio=balance_ratio)
    if len(balanced) != len(deduped):
        log.info("[%s] %d posts after gender balance", source, len(balanced))

    # Step 4: download.
    _, dest_dir = SOURCES[source]
    succeeded, failed = download_all(balanced, dest_dir)
    seen.save(config.SEEN_MD5_TXT)  # persist newly-seen md5s

    # Step 5: index (per-source; main() merges at the end).
    per_source_index = config.RAW_DIR / f"{source}.index.jsonl"
    build_index(succeeded, per_source_index)
    log.info("[%s] done: %d images indexed", source, len(succeeded))


def merge_indexes(sources: list[str]) -> None:
    """Concatenate per-source index.jsonl files into the unified index.jsonl."""
    entries = 0
    with config.INDEX_JSONL.open("w", encoding="utf-8") as out:
        for source in sources:
            idx = config.RAW_DIR / f"{source}.index.jsonl"
            if not idx.exists():
                continue
            for line in idx.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.write(line + "\n")
                    entries += 1
    log.info("merged index: %d entries -> %s", entries, config.INDEX_JSONL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scraper",
        description="Fat-fetish hentai dataset scraper (see PLAN.md).",
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES) + ["all"],
        default="all",
        help="Which source to scrape (default: all)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Fetch+filter metadata only; don't download images. Good for dry runs.",
    )
    parser.add_argument(
        "--skip-metadata",
        action="store_true",
        help="Reuse existing raw_metadata/*.jsonl instead of re-fetching.",
    )
    parser.add_argument(
        "--balance-ratio",
        type=float,
        default=0.4,
        help="Minimum male+mixed share after balancing (default 0.4).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose (DEBUG) logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    sources = list(SOURCES) if args.source == "all" else [args.source]

    for source in sources:
        log.info("=== %s ===", source)
        run(source, args.metadata_only, args.skip_metadata, args.balance_ratio)

    if not args.metadata_only and len(sources) > 1:
        merge_indexes(sources)

    return 0


if __name__ == "__main__":
    sys.exit(main())
