# Booru-Scraper

pulls high-score fat fetish art off the boorus so you can train a model on it.
that's it. that's the whole thing. Change tags in the config if you want something else

why? because i wanted a dataset of chubby/fat/obese anime porn sorted by score
and i wasn't about to click through 55k gelbooru posts by hand. nobody's got
time for that shit. We need more fat-fetish diffusers and LLMs

## what it does

grabs metadata from gelbooru, danbooru, and rule34, sorts by popularity,
filters out the garbage (videos, 3d, cosplay, tiny thumbnails), dedups by md5
across sources, downloads the survivors, downscales to 1024px longest side,
and writes an `index.jsonl` you can feed to whatever cursed trainer you're
running.

pipeline:

```
fetch metadata -> filter -> dedup -> download+resize -> index.jsonl
```

every step is its own module so you can run just the metadata part to preview
what you'd get before committing disk space to 10k images of drawn fat fucks.

## install

```powershell
pip install -r requirements.txt
```

needs python 3.10+ (uses `str | None` syntax, deal with it). pillow is in
there for the downscale. requests for the obvious reason.

## gelbooru auth

gelbooru's json api 401s on anonymous now. grab a free key:

1. register at gelbooru
2. account options -> api key
3. shove it in env vars:

```powershell
$env:GELBOORU_API_KEY="yourkey"
$env:GELBOORU_USER_ID="12345"
```

danbooru works anonymous (1 req/2s, 100/page). if you want faster, set
`DANBOORU_API_KEY` / `DANBOORU_LOGIN` too. i didn't bother.

## rule34 auth

rule34.xxx runs gelbooru software, so its json dapi is the same shape — but it
now 200s with a `"Missing authentication"` body instead of returning posts.
grab a free account:

1. register at rule34.xxx
2. account options -> api key
3. shove it in env vars:

```powershell
$env:RULE34_API_KEY="yourkey"
$env:RULE34_USER_ID="12345"
```

rule34 only exposes `score` (upvotes - downvotes) — no fav_count, no download
counter — so the pass sorts by `sort:score:desc` and stops at `score < 10`.

## usage

```powershell
cd dir with README

# dry run: just pull danbooru metadata, see what you'd get. no images.
python -m scraper.main --source danbooru --metadata-only -v

# full danbooru run
python -m scraper.main --source danbooru

# gelbooru (needs the env vars above)
python -m scraper.main --source gelbooru

# everything
python -m scraper.main

# just rule34 (needs the env vars above, same as gelbooru)
python -m scraper.main --source rule34

# already have metadata and just wanna re-download?
python -m scraper.main --skip-metadata
```

flags:
- `--source {gelbooru,danbooru,rule34,all}` — pick your poison
- `--metadata-only` — fetch + filter, no downloads. good for eyeballing counts
- `--skip-metadata` — reuse `raw_metadata/*.jsonl` from a prior run
- `--balance-ratio 0.4` — min male/mixed share. default 0.4 because high-score
  fat art is like 90% bbw and your model will forget men exist
- `-v` — verbose. shows every page fetch

## tuning

all the knobs live in `scraper/config.py`. the big ones:

| knob | default | what |
|---|---|---|
| `CORE_TAGS` | fat, fat_man, obese, bbw, bbm, ssbbw, gainer, weight_gain, chubby | tags we run separate score-sorted passes for |
| `THRESHOLDS.gelbooru_score` | 10 | stop paging when score drops below this |
| `THRESHOLDS.danbooru_fav_count` | 10 | same but danbooru uses fav_count (cleaner) |
| `THRESHOLDS.rule34_score` | 10 | same as gelbooru; rule34 has no fav_count |
| `MAX_SAVE_DIM` | 1024 | Rescaler longest side in px. 0 = keep originals |
| `MIN_IMAGE_DIM` | 512 | drop anything smaller (thumbnails) |
| `DOWNLOAD_WORKERS` | 8 | threads. bump if your connection isn't garbage |
| `TAG_BLACKLIST` | video, animated, 3d, comic, photo... | mandatory excludes |

if you're not getting enough images, lower the score thresholds. if you're
getting too much junk, raise them. groundbreaking stuff.

## storage

```
Scraped/
  gelbooru/      <md5>.jpg          # resized images
  danbooru/      <md5>.jpg
  rule34/        <md5>.jpg
  raw_metadata/  gelbooru.jsonl     # fetched post metadata (resumable)
                 danbooru.jsonl
                 rule34.jsonl
  index.jsonl                       # final: one line per image, caption=null
  seen_md5.txt                      # dedup set, persists across runs
```

`index.jsonl` schema:

```json
{"image":"gelbooru/abc123.jpg","source":"gelbooru","md5":"abc123","tags":["fat","1girl"],"rating":"explicit","gender":"female","score":420,"tag_query":"fat","caption":null}
```

`caption` is null because captioning is a separate step you do later with
whatever multimodal model you're into. I like Gemma 4 26B abliterated; will work on my own caption finetune later.

## the resize thing

images come down at whatever resolution the booru has them — could be 4000px.
we verify md5 on the **original** bytes first (so corrupt/poisoned files get
rejected), then downscale to 1024px longest side with lanczos, keeping aspect
ratio. the saved file's md5 won't match the post's md5 anymore — that's fine,
the md5 is just the filename and a provenance key, not a hash of what's on
disk. if that bothers you, set `MAX_SAVE_DIM=0` and waste your own storage.

## gender balance

fat art on the boorus skews female *hard*. like, embarrassingly hard. the
`balance()` function in filters.py caps the female share so male/mixed posts
don't get drowned out. default ratio is 0.4 (40% male-ish). it keeps all
male/mixed posts and trims females down to the highest-scoring ones. adjust
`--balance-ratio` if you want more or less of either.

## etiquette

- 1 req per ~2s per host, enforced in `http_client.py`. don't disable it, you'll
  get ip-banned and ruin it for the rest of us.
- retries with backoff on 429/5xx are built in.
- total runtime for ~10k is a few hours. go touch grass.


## license

the code is yours, do whatever. the images are not mine and not yours —
they're the artists'. don't be a dick about it. this is for personal
research, not redistribution.
