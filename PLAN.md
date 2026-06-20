# Fat-Fetish Hentai Dataset — Scraping Plan

Goal: ~10k+ anime-styled images focused on **fat fetish** (chubby/fat/obese/weight-gain),
both male (BBM) and female (BBW). For multimodal LLM training (pairs with `../prompt.txt`).

## 1. Source research (verified counts, Jun 2026)

| Source | Style | Fat-relevant tags & counts | API | Notes |
|---|---|---|---|---|
| **Gelbooru** | anime | `fat` 55k, `bbw` 39.5k (deprecated→fat), `bbm` 8.2k (→fat_man) | JSON API (`index.php?page=dapi&s=post&q=index`), free key raises limit | Largest pool, anime-styled, easy. **Primary.** |
| **Danbooru** | anime | `fat` 18.7k, `fat_man` 13k, `obese`, `plump`, `belly` 22k, `thick_thighs` 150k | REST JSON (`/posts.json?tags=...`), 100/req, 1 req/s anon | Best tag quality → great for captions. **Primary.** |
| **e-hentai / exhentai** | anime (doujin) | tags: `fat man`, `bbw`, `weight gain`, `ssbbw`, `gainer`, `inflation`, `chubby` | HTML + `api.php` (gid/token), existing downloader in this repo | Sequential gallery pages. **Secondary.** |
| **nhentai** | anime (doujin) | `bbm`, `bbw`, `fat`, `weight gain` | JSON (`/api/galleries/search?query=`) | Mirrors e-hentai; dedup needed. **Optional.** |
| **rule34.xxx** | mixed | `fat`, `bbw`, `bbm`, `obese`, `weight_gain` | `api.rule34.xxx` dapi — **now requires account auth** | Anime + western mix. **Optional.** |
| **rule34.paheal** | mixed | same | RSS/HTML | Western-heavy. **Low priority.** |
| **Pixiv** | anime | `デブ`, `ぽっちゃり`, `BBW`, `BBM`, `肥胖体型`, `腹肉` | none official, login + anti-scrape | Highest quality originals but hardest. **Stretch.** |
| **e621** | furry (NOT anime) | `fat`, `obese`, `weight_gain` | REST JSON | Skip — violates "anime styled" priority. |
| **DeviantArt** | mostly western | WG community | — | Already have a folder; western-styled, low priority for anime goal. |

## 2. Quality sorting (we only want ~10k, so take the best)

Every source exposes a popularity signal — sort by it and take the top slice.

| Source | Sort query | Signal field | Notes |
|---|---|---|---|
| Danbooru | `tags=fat order:score ...` | `score`, `fav_count` | `fav_count` is cleaner (no downvote noise); prefer it. |
| Gelbooru | `tags=fat sort:score:desc` | `score` | JSON dapi now **requires a free API key** (401 without). |
| e-hentai | search `sort=rating` (or `favcount`) | gallery star rating + favcount | Sort galleries, then sample top pages. |

**Mandatory exclusions** (top-scored fat results are full of these — verified):
`-video -animated -3d -ugly_bastard?(optional) -comic -photo -cosplay -real_life`
Also drop `file_ext` in `{mp4,webm,gif}` and any `image_width<768 or image_height<768`.

**Score thresholds** (tune after a sample pull):
- Danbooru: keep `fav_count >= 50` (or top-N by score per tag).
- Gelbooru: keep `score >= 10`.
- e-hentai: keep galleries `rating >= 4.0` and `favcount >= 100`.

**Strategy**: pull metadata sorted by score desc, page through until you cross the threshold, then stop. This naturally gives you the "best 10k" without grabbing long-tail junk. Re-check gender balance after — high-score fat art skews even harder female, so run a separate `fat_man`/`bbm` top-score pass and merge.

## 3. Target tag set (use across boorus)

Core (must-have): `fat`, `fat_man`, `obese`, `bbw`, `bbm`
Expansion (chubby/adjacent, filter manually): `plump`, `chubby`, `thick_thighs`, `muffin_top`, `belly`, `weight_gain`, `ssbbw`, `gainer`, `inflation`(selective)
Exclude: `fat_cow`(unrelated), `fat_suit`, `furry`(if you want pure anime), `3d`, `photo`, `cosplay`, `monochrome`(optional), `comic`(optional — multi-panel hurts single-image captioning)

## 4. Strategy — tiered pull to hit 10k+ (sorted by popularity)

**Tier 1 — Gelbooru (target ~6k)**
- Query `fat sort:score:desc` (covers bbw alias) + separate `bbm sort:score:desc` pass. Pull `file_url` + `sample_url` + full tag string + md5 + rating + score + source.
- API: `https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&tags=fat+sort:score:desc&limit=100&pid=N&api_key=...&user_id=...` (free key **required** now).
- Stop paging once `score < 10`. Keep rating mix as desired.

**Tier 2 — Danbooru (target ~4k)**
- `/posts.json?tags=fat+order:score+-video+-animated+-3d+-comic&limit=100&page=N` plus `fat_man`, `obese`, `plump` passes.
- Prefer `order:favcount` (cleaner than score). Stop when `fav_count < 50`.
- Danbooru tags are cleaner → use its tag set as the canonical caption labels.
- Respect 1 req/s; paginate by `page:b<id>` for deep paging past 1000.

**Tier 3 — e-hentai galleries (target ~2–3k pages, optional fill)**
- Use the existing `ehentai_downloader` tooling. Search tags `fat man`, `bbw`, `weight gain`, `ssbbw`.
- Download full galleries, then sample N pages per gallery (e.g. 5) to avoid one artist dominating.
- Mark these as `doujin`/sequential so captioner knows context.

**Dedup**: across all tiers, hash file md5 (boorus share md5s heavily — Gelbooru/Danbooru/r34 overlap is large). Drop duplicates. Expect ~30–40% overlap reduction.

**Balance**: enforce male/female balance. Track `1boy`/`1girl`/`fat_man`/`bbw` counts; oversample `bbm`/`fat_man` since female fat art dominates.

## 4. Storage layout

```
dataset/Scraped/
  gelbooru/   <md5>.<ext>  +  <md5>.json   (tags, rating, source_url, score, api_id)
  danbooru/   <md5>.<ext>  +  <md5>.json
  ehentai/    <gallery_id>_<page>.<ext>  +  manifest.jsonl
  index.jsonl         # unified: {path, source, md5, tags[], rating, gender, caption?}
  seen_md5.txt        # dedup
```

## 5. Pipeline steps

1. **Fetch metadata** (tags + URLs) per source into JSONL — no images yet. Lets you preview/curate before downloading.
2. **Filter** by tag blacklist, score threshold, gender balance, dedup by md5.
3. **Download images** with concurrency (~4–8), retries, rate-limit per host. Save md5-named.
4. **Verify**: re-hash, drop corrupt/undersized (<20KB or <512px), drop non-image.
5. **Caption** with `../prompt.txt` via your multimodal model → write `<md5>.txt`.
6. **Build final dataset**: `index.jsonl` with `{image, tags, gender, rating, source, caption}`.

## 6. Rate-limit / etiquette

- Gelbooru: ~1 req/s, User-Agent set, optional API key.
- Danbooru: 1 req/s anon; account raises limits.
- e-hentai: be gentle, use `Hath`-style delays, respect `ipb_member_id`/`ipb_pass_hash` cookies for exhentai.
- Don't hammer; total runtime for 10k is a few hours, not minutes.

## 7. Next action

Pick Tier 1 (Gelbooru) as the first scraper to build — it alone can supply 10k after dedup.
I can scaffold the scraper (metadata fetch → filter → download → index) in Python when you're ready.
