#!/usr/bin/env python3
"""Regenerate docs/data/ (the GitHub Pages preview) from data/.

Writes docs/data/courses.json, which docs/index.html loads:
  {"meta": {...}, "courses": [{rank, key, title, url, n_authors, n_mentions,
   n_months, first_year, last_year, file_link_share, quote, quote_author,
   quote_month, quote_url, page_title, provider, description, instructors,
   access, has_video_lectures, has_assignments, status, final_url,
   wayback_url}, ...]}
  page fields come from data/courses_meta.jsonl (scripts/fetch_pages.py); null
  when that file is missing or the page did not say.
Later sessions: rerun this after changing data/, and when you add fields here,
also add them to docs/index.html (see CLAUDE.md).

Usage: python3 scripts/build_site.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from course_info import HN_ITEM, likely_title, pick_quote  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "data"


def main():
    months, n_comments = set(), 0
    for line in open(DATA / "mentions_raw.jsonl"):
        months.add(json.loads(line)["month"])
        n_comments += 1
    meta_rows = {}
    if (DATA / "courses_meta.jsonl").exists():
        for line in open(DATA / "courses_meta.jsonl"):
            m = json.loads(line)
            meta_rows[m["key"]] = m
    courses = []
    for rank, line in enumerate(open(DATA / "courses_candidates.jsonl"), 1):
        r = json.loads(line)
        quote, m = pick_quote(r)
        courses.append({
            "rank": rank, "key": r["key"], "title": likely_title(r), "url": r["urls"][0],
            "n_authors": r["n_authors"], "n_mentions": r["n_mentions"],
            "n_months": r["n_months"], "first_year": r["first_year"],
            "last_year": r["last_year"], "file_link_share": r["file_link_share"],
            "quote": quote, "quote_author": m["author"], "quote_month": m["month"],
            "quote_url": HN_ITEM.format(m["hn_comment_id"]),
        })
        pm = meta_rows.get(r["key"], {})
        courses[-1].update({
            "page_title": pm.get("title"), "provider": pm.get("provider"),
            "description": pm.get("description"), "instructors": pm.get("instructors"),
            "access": pm.get("access"), "has_video_lectures": pm.get("has_video_lectures"),
            "has_assignments": pm.get("has_assignments"), "status": pm.get("status"),
            "final_url": pm.get("final_url"), "wayback_url": pm.get("wayback_url"),
        })
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"generated": time.strftime("%Y-%m-%d"), "first_month": min(months),
            "last_month": max(months), "n_comments": n_comments,
            "n_courses": len(courses), "min_authors": 3, "min_months": 2,
            "n_live": sum(c["status"] == "live" for c in courses),
            "n_dead": sum(c["status"] == "dead" for c in courses),
            "n_blocked": sum(c["status"] == "blocked" for c in courses)}
    with open(OUT / "courses.json", "w") as f:
        json.dump({"meta": meta, "courses": courses}, f, ensure_ascii=False, indent=0)
    (ROOT / "docs" / ".nojekyll").touch()
    print(f"wrote docs/data/courses.json ({len(courses)} courses)")


if __name__ == "__main__":
    main()
