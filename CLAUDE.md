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

## Running
```bash
nohup python3 scripts/extract.py --start 2019-01 --end 2019-03 >> work/extract.log 2>&1 &
python3 scripts/extract.py --build-only        # rebuild output from work/
```
Defaults: `--start 2006-10 --end <current month>`. Progress: `tail work/extract.log`.

## Conventions
- `work/` and `raw/` (and any `*.parquet`) are gitignored.
- Never print big files; use `head`, `wc -l`, or DuckDB aggregates.
