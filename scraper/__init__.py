"""
scraper package — fat-fetish hentai dataset scraper.

Pipeline (see PLAN.md §5):
    1. fetch metadata  -> scraper.metadata.*   (JSONL, no images yet)
    2. filter           -> scraper.filters
    3. dedup by md5     -> scraper.dedup
    4. download images  -> scraper.downloader  (concurrent, verify)
    5. build index      -> scraper.index       (index.jsonl)

Run it from the CLI:
    python -m scraper.main --help
"""

__all__ = [
    "config",
    "http_client",
    "metadata",
    "gelbooru",
    "danbooru",
    "filters",
    "dedup",
    "downloader",
    "index",
    "main",
]
