# my-hacker-courses

Goal: find the online courses Hacker News keeps recommending.

## Source
Hugging Face dataset `open-index/hacker-news`, monthly parquet files at
`data/YYYY/YYYY-MM.parquet` (~70 MB/month in 2019). Columns used: `id`, `type`
(1 = story, 2 = comment), `by`, `time`, `text`, `parent`, `title`, `deleted`, `dead`.
Comment `text` is HN HTML: entities are escaped (`&#x2F;` for `/`, `&#x27;`, ...),
links are `<a href="...">`, paragraphs are `<p>`.

## Pipeline: `scripts/extract.py` (DuckDB + Python)
1. **Scan, one month at a time** (download -> scan -> delete, so disk use stays at
   one month of raw data, well under 30 GB). Downloads go to `raw/` and are deleted
   after the month is processed. Per month it writes:
   - `work/parents/YYYY-MM.parquet`: compact `id, parent, title` table for *every*
     item (title only for stories).
   - `work/matches/YYYY-MM.jsonl`: course-linking comments. Its existence marks the
     month as done; reruns skip it (resumable, written via `.tmp` + rename).
2. **Match rules** (`is_course_url`, `clean_comment`): non-dead, non-deleted
   comments; HTML-unescaped links (`<a href>` plus bare/scheme-less URLs) are tested
   against the course patterns: ocw.mit.edu/courses, coursera.org/learn|specializations,
   edx.org/course|learn, udacity.com/course, udemy.com/course, khanacademy.org,
   course.fast.ai, cs50.harvard.edu, missing.csail.mit.edu, pdos.csail.mit.edu,
   nand2tetris.org, online.stanford.edu, see.stanford.edu, web.stanford.edu/class,
   inst.eecs.berkeley.edu, cs.cmu.edu/~, and any `*.edu` URL whose path contains
   `/course`, `/class` or `/teaching`. The comment must also have >= 100 chars of its
   own text after removing links and collapsing whitespace. A cheap SQL regex
   prefilter runs first; Python does the exact check.
3. **Build** (runs at the end of every invocation, or `--build-only`): loads all
   `work/parents/*.parquet` and walks up from each match with a recursive CTE to
   its root story to get the title. Stories in months not yet scanned give
   `story_title: null`; rerun build after scanning more months.
4. **Output** `data/mentions_raw.jsonl` (committed), one JSON per comment:
   `hn_comment_id, author, month, story_id, story_title, url, course_urls, text`.
   `url` is the first course link, `course_urls` all of them. `text` is HTML-stripped,
   course links replaced by `[COURSE]`, other links by `[LINK]`, cut at 1500 chars.

## Canonicalize: `scripts/canonicalize.py`
Maps every course link in `data/mentions_raw.jsonl` to a course key (`course_key`):
- platforms by slug: `coursera.org/learn/<slug>` (old `/course/` -> `/learn/`),
  `udemy.com/course/<slug>`, `udacity.com/course/<code>` (`...--cs212` -> `cs212`),
  edX `edx.org/<org>+<code>` when the slug or `courses.edx.org` path carries it,
  else `edx.org/course/<slug>`; Stanford Lagunita / SEE / online.stanford.edu by course.
- MIT by course number across OCW (all years, old `OcwWeb` URLs), pdos, csail,
  web.mit.edu: `mit.edu/6.006`. `J` (cross-listed) and `SC` (OCW Scholar) are merged.
- university pages: `host/<code>`, ignoring term/year/archive/dept folders;
  Stanford class pages (`stanford.edu`, `web.stanford.edu`, `cs231n.stanford.edu`)
  -> `web.stanford.edu/class/<code>`; CMU `~15213`, `~410-s05`, `/afs/.../15213` -> `15-213`.
- single-course sites: `nand2tetris.org`, `course.fast.ai`, `missing.csail.mit.edu`.
- merged families: nand2tetris.org + Coursera `build-a-computer`/`nand2tetris2`
  -> `nand2tetris.org`; CS50 on any host (cs50.harvard.edu, edX, pll/online-learning
  .harvard.edu) -> `cs50.harvard.edu/<track>` (intro = `/x`, also root/college/
  extension); Stanford SEE recordings -> `web.stanford.edu/class/<code>`, with old
  `courseinfo.aspx?coll=<guid>` links mapped by hand in `SEE_COLL` (pages are gone;
  identified from comment text, ee364a/b order guessed).
- links with no course (papers, homepages, KA careers) get no key and are dropped.
A comment counts once per key. Keeps keys with >= `--min-authors` distinct authors
in >= `--min-months` distinct months (default 3/2) and writes
`data/courses_candidates.jsonl`: `key, n_mentions, n_authors, n_months, first_year,
last_year, urls, file_link_share, mentions` (<= 20, one per author first, spread over
time). `file_link_share` = share of links to pdf/slides/video; high values flag
course *material* shared as an article (e.g. large.stanford.edu ph240 reports),
not a course recommendation. Known unmerged look-alikes (left separate on purpose):
OCW vs edX MITx (`mit.edu/6.00` vs `edx.org/mitx+6.00x`), stanford-online ee380 vs
class ee380, Lagunita vs online.stanford.edu (compilers, statistical learning).

## Course pages: `scripts/fetch_pages.py`
Fetches each candidate's `urls[0]` (most-linked) at 1 req/s, caching HTML + fetch
info in `work/pages/<sha1>.{html,json}` (resumable; transient network errors are
not cached; `--offline` builds from cache only). Status: `live`, `dead` (404/5xx,
redirect to site root, platform redirect to a *different* course), or `unknown`
(bot wall / login page / 401-403-429 / TLS failure through the sandbox proxy, e.g.
web.stanford.edu, cs.cornell.edu). Non-live pages get the latest Wayback snapshot
(`archive.org/wayback/available` with no `timestamp` param: adding one gets 429s;
the API rate-limits hard). Writes `data/courses_meta.jsonl`: status, final_url,
wayback_url, title, description, provider (from host), instructors (JSON-LD or an
explicit "Instructor:" label), access free/paid/audit, has_video_lectures,
has_assignments. Null unless the page states it; booleans are true or null.

## Report and preview site
- `scripts/course_info.py`: shared helpers. `likely_title` (hand-curated `TITLES`
  first, then URL slugs for Coursera/edX/Udacity/Udemy/OCW/Khan Academy, then
  "<institution> <CODE>"); `pick_quote` (one short sentence, preferring ones that
  contain the course link and recommend it). "(readings)" / "(student reports)" in a
  title marks course pages HN links as articles; the site can hide those.
- `scripts/build_report.py` -> `data/REPORT.md`: matches per year, threshold table,
  top 100 courses (title, key, authors, months, years, quote + HN link).
- `scripts/embed_courses.py` -> `data/course_embeddings.npy` (float16 [n, 384]) +
  `data/course_embedding_keys.json` (committed). fastembed `BAAI/bge-small-en-v1.5`, no
  prefix, L2-normalized, over likely_title + page title + page description: the same
  model as my-hackertube's `embeddings.npy`, so course and video vectors compare
  directly. Needs `pip install fastembed`. Rerun after candidates or meta change
  (build_site.py asserts the keys match).
- `scripts/build_site.py` (needs numpy and a my-hackertube checkout, `--hackertube`,
  default `../my-hackertube`; reads its `data/hackertube-data/` or the zip in place)
  wipes and writes `docs/data/` (+ `docs/.nojekyll`):
  - `courses.json` `{meta, courses}` in rank order. Besides the candidate/meta fields:
    `id` (slug of key), `summary` (page description, else the page title if it adds
    words; no LLM text), `format` (Online course / OpenCourseWare / Class page / Reading
    list), `free` + `free_src` (page access, else inferred `free` for OCW, Khan Academy,
    class pages, nand2tetris/fast.ai/missing/CS50), `video` (page says so, or HackerTube
    has its lectures), `cats` (HackerTube category ids voted by the 20 nearest HackerTube
    videos, scored above each category's average so catch-alls don't win), `nb` (12
    nearest courses `[index, cosine]`), `n_lec`.
  - `c/<id>.json`: `mentions` (the <= 20 candidate excerpts, 700 chars, newest first),
    `similar`, `lectures`: HackerTube videos with content_type lecture/course matched by
    course number in the title, by a descriptive title (4+ words, or same institution),
    or cosine >= 0.85 with the same institution; pairs naming different institutions are
    rejected (embedding-only matches are mostly same topic, different course).
  - `taxonomy.json` (HackerTube areas/categories used, with counts), `timeline.json`
    (per year: comments, people, courses, top by distinct authors, debuts).
- `docs/` is a port of my-hackertube's site (plain HTML/CSS/JS, no build step, no
  external scripts; `assets/app.js` routes on `<body data-page>`; data text only via
  `textContent`): `index.html` (For You shelf, area chips, filters free / video /
  assignments / provider / category / reading lists / sort, kept in the query string),
  `course.html?c=<id>` (summary, why HN recommends it, HN comment excerpts with links,
  Lectures on HackerTube, similar courses; dead pages open their Wayback snapshot with a
  note), `category.html[?c=]`, `timeline.html`, `library.html` (likes, history, For You),
  `table.html` (the original sortable table). Likes and history are localStorage only;
  For You sums the `nb` neighbours of liked (1.0) and recently opened (0.5, decaying)
  courses, max 3 per provider. Shortcuts: `/` `j` `k` `l` `o` `g h|c|t|l` `?` `Esc`.
  Course cards are text only: no logos or provider artwork (lecture videos show
  YouTube thumbnails).
- **Every later session must rerun `python3 scripts/build_site.py` (and
  `build_report.py`) after changing anything in `data/`, and when it adds new
  fields to the data, add them to `build_site.py` and show them in `docs/`.**
  Keep the footer credits (HackerTube, open-index/hacker-news, ODC-By) on every page.
- Headless check: `scripts/site_check.js` (see its header). It loads every page in Playwright with console errors
  collected (ignore YouTube/archive.org hosts, which sandboxes may block); check phone
  width (390px) has no horizontal scroll.

## Running
```bash
nohup python3 scripts/extract.py --start 2019-01 --end 2019-03 >> work/extract.log 2>&1 &
python3 scripts/extract.py --build-only        # rebuild output from work/
python3 scripts/canonicalize.py --min-authors 3 --min-months 2
python3 scripts/embed_courses.py               # after candidates/meta change
python3 scripts/build_report.py && python3 scripts/build_site.py
python3 -m http.server -d docs 8000            # preview the site locally
```
Defaults: `--start 2006-10 --end <current month>`. Progress: `tail work/extract.log`.

## Conventions
- `work/` and `raw/` (and any `*.parquet`) are gitignored.
- Never print big files; use `head`, `wc -l`, or DuckDB aggregates.
