#!/usr/bin/env python3
"""Extract Hacker News comments that recommend online courses.

Pipeline (see CLAUDE.md):
  scan:  for each month YYYY-MM in [--start, --end]
           download data/YYYY/YYYY-MM.parquet from HF open-index/hacker-news
           -> work/parents/YYYY-MM.parquet  (id, parent, title) for every item
           -> work/matches/YYYY-MM.jsonl    course-linking comments
           delete the download
         Months whose work/matches/YYYY-MM.jsonl exists are skipped (resumable).
  build: walk up work/parents/* from each match to its story and write
         data/mentions_raw.jsonl.

Usage:
  python3 scripts/extract.py --start 2019-01 --end 2019-03
  python3 scripts/extract.py --build-only
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
RAW = ROOT / "raw"
PARENTS = WORK / "parents"
MATCHES = WORK / "matches"
OUT = ROOT / "data" / "mentions_raw.jsonl"
HF_URL = "https://huggingface.co/datasets/open-index/hacker-news/resolve/main/data/{y}/{y}-{m}.parquet"

MIN_OWN_CHARS = 100
MAX_TEXT_CHARS = 1500

# (host suffix, path prefix). Host matches exactly or as a subdomain.
COURSE_RULES = [
    ("ocw.mit.edu", "/courses"),
    ("coursera.org", "/learn"),
    ("coursera.org", "/specializations"),
    ("edx.org", "/course"),
    ("edx.org", "/learn"),
    ("udacity.com", "/course"),
    ("udemy.com", "/course"),
    ("khanacademy.org", ""),
    ("course.fast.ai", ""),
    ("cs50.harvard.edu", ""),
    ("missing.csail.mit.edu", ""),
    ("pdos.csail.mit.edu", ""),
    ("nand2tetris.org", ""),
    ("online.stanford.edu", ""),
    ("see.stanford.edu", ""),
    ("web.stanford.edu", "/class"),
    ("inst.eecs.berkeley.edu", ""),
    ("cs.cmu.edu", "/~"),
]
EDU_PATH_RE = re.compile(r"/(course|class|teaching)", re.I)

# Cheap SQL prefilter on the (still escaped) text; Python does the exact check.
PREFILTER = (r"(ocw\.mit\.edu|coursera\.org|edx\.org|udacity\.com|udemy\.com|"
             r"khanacademy\.org|fast\.ai|nand2tetris\.org|\.edu/)")

A_RE = re.compile(r'<a\s[^>]*?href="([^"]*)"[^>]*>.*?</a>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
# Bare URLs, with or without scheme (scheme-less only for dotted host + path).
BARE_URL_RE = re.compile(
    r"(?:https?://[^\s<>\"']+|(?<![\w@/.])(?:[a-z0-9-]+\.)+(?:edu|org|com|ai)/[^\s<>\"']*)",
    re.I)
TRAIL = ".,;:!?)]}'\"*"


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def is_course_url(url):
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or ""
    for suffix, prefix in COURSE_RULES:
        if (host == suffix or host.endswith("." + suffix)) and path.lower().startswith(prefix):
            return True
    return host.endswith(".edu") and bool(EDU_PATH_RE.search(path))


def clean_comment(raw):
    """HN HTML -> (plain text with [COURSE]/[LINK], course urls, own-text length)."""
    course_urls = []

    def sub_url(url):
        if is_course_url(url):
            course_urls.append(url)
            return " [COURSE] "
        return " [LINK] "

    def a_repl(m):
        return sub_url(html.unescape(m.group(1)))

    t = A_RE.sub(a_repl, raw)
    t = re.sub(r"<p>", "\n\n", t, flags=re.I)
    t = TAG_RE.sub("", t)
    t = html.unescape(t)

    def bare_repl(m):
        url = m.group(0)
        stripped = url.rstrip(TRAIL)
        return sub_url(stripped) + url[len(stripped):]

    t = BARE_URL_RE.sub(bare_repl, t)
    t = re.sub(r"(\[(?:COURSE|LINK)\]) +([.,;:!?)])", r"\1\2", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    own = re.sub(r"\s+", " ", re.sub(r"\[(COURSE|LINK)\]", " ", t)).strip()
    return t, course_urls, len(own)


def download(month, dest):
    y, m = month.split("-")
    url = HF_URL.format(y=y, m=m)
    tmp = dest.with_suffix(".part")
    for attempt in range(5):
        r = subprocess.run(["curl", "-sSfL", "--retry", "3", "-o", str(tmp), url])
        if r.returncode == 0:
            tmp.rename(dest)
            return True
        log(f"{month}: download failed (rc={r.returncode}), attempt {attempt + 1}")
        time.sleep(2 ** (attempt + 1))
    tmp.unlink(missing_ok=True)
    return False


def process_month(con, month):
    out = MATCHES / f"{month}.jsonl"
    if out.exists():
        log(f"{month}: already done, skipping")
        return
    RAW.mkdir(exist_ok=True)
    src = RAW / f"{month}.parquet"
    if not src.exists() and not download(month, src):
        log(f"{month}: giving up")
        return
    try:
        p_tmp = PARENTS / f"{month}.parquet.tmp"
        con.execute(f"""
            COPY (SELECT id, nullif(parent, 0) AS parent, CASE WHEN type = 1 THEN title END AS title
                  FROM read_parquet('{src}') WHERE id IS NOT NULL)
            TO '{p_tmp}' (FORMAT parquet)""")
        p_tmp.rename(PARENTS / f"{month}.parquet")

        rows = con.execute(f"""
            SELECT id, "by", strftime(time, '%Y-%m') AS month, parent, text FROM read_parquet('{src}')
            WHERE type = 2 AND text IS NOT NULL
              AND coalesce(deleted, 0) = 0 AND coalesce(dead, 0) = 0
              AND regexp_matches(lower(replace(text, '&#x2F;', '/')), ?)
            ORDER BY id""", [PREFILTER]).fetchall()

        n = 0
        m_tmp = out.with_suffix(".jsonl.tmp")
        with open(m_tmp, "w") as f:
            for cid, by, ts_month, parent, text in rows:
                clean, urls, own_len = clean_comment(text)
                if not urls or own_len < MIN_OWN_CHARS:
                    continue
                f.write(json.dumps({
                    "hn_comment_id": cid, "author": by,
                    "month": ts_month or month,
                    "parent": parent, "url": urls[0], "course_urls": urls,
                    "text": clean[:MAX_TEXT_CHARS],
                }) + "\n")
                n += 1
        m_tmp.rename(out)
        log(f"{month}: {len(rows)} prefiltered, {n} matches")
    finally:
        src.unlink(missing_ok=True)


def build(con):
    """Walk up the id->parent table from every match to its root story."""
    files = sorted(MATCHES.glob("*.jsonl"))
    if not files:
        log("build: no matches yet")
        return
    con.execute("CREATE OR REPLACE TEMP TABLE items AS "
                f"SELECT id, nullif(parent, 0) AS parent, title FROM read_parquet('{PARENTS}/*.parquet')")
    con.execute("CREATE OR REPLACE TEMP TABLE m AS SELECT * FROM read_json("
                f"{[str(f) for f in files]}, format='newline_delimited', "
                "columns={hn_comment_id:'UBIGINT', author:'VARCHAR', month:'VARCHAR', "
                "parent:'UBIGINT', url:'VARCHAR', course_urls:'VARCHAR[]', text:'VARCHAR'})")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE roots AS
        WITH RECURSIVE walk(cid, cur, depth) AS (
            SELECT hn_comment_id, nullif(parent, 0), 1 FROM m
            UNION ALL
            SELECT w.cid, i.parent, w.depth + 1
            FROM walk w JOIN items i ON i.id = w.cur
            WHERE i.parent IS NOT NULL AND w.depth < 500
        )
        SELECT cid, arg_max(cur, depth) AS story_id FROM walk GROUP BY cid""")
    rows = con.execute("""
        SELECT m.hn_comment_id, m.author, m.month, r.story_id, i.title,
               m.url, m.course_urls, m.text
        FROM m JOIN roots r ON r.cid = m.hn_comment_id
        LEFT JOIN items i ON i.id = r.story_id
        ORDER BY m.hn_comment_id""").fetchall()
    tmp = OUT.with_suffix(".jsonl.tmp")
    OUT.parent.mkdir(exist_ok=True)
    missing = 0
    with open(tmp, "w") as f:
        for cid, by, month, sid, title, url, urls, text in rows:
            missing += title is None
            f.write(json.dumps({
                "hn_comment_id": cid, "author": by, "month": month,
                "story_id": sid, "story_title": title, "url": url,
                "course_urls": urls, "text": text,
            }, ensure_ascii=False) + "\n")
    tmp.rename(OUT)
    log(f"build: wrote {len(rows)} mentions to {OUT.relative_to(ROOT)} "
        f"({missing} without a known story title)")


def months(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2006-10")
    ap.add_argument("--end", default=time.strftime("%Y-%m"))
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()
    PARENTS.mkdir(parents=True, exist_ok=True)
    MATCHES.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    if not args.build_only:
        for month in months(args.start, args.end):
            try:
                process_month(con, month)
            except Exception as e:  # keep going; month stays undone
                log(f"{month}: ERROR {e!r}")
    build(con)
    log("done")


if __name__ == "__main__":
    sys.exit(main())
