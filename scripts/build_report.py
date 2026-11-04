#!/usr/bin/env python3
"""Write data/REPORT.md from data/mentions_raw.jsonl and data/courses_candidates.jsonl.

Usage: python3 scripts/build_report.py [--top 100]
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonicalize import THRESHOLDS, group_all, survivors  # noqa: E402
from course_info import HN_ITEM, likely_title, pick_quote  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def md(s):
    return s.replace("|", "\\|").replace("\n", " ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args()

    per_year = collections.Counter()
    months = set()
    for line in open(DATA / "mentions_raw.jsonl"):
        r = json.loads(line)
        per_year[r["month"][:4]] += 1
        months.add(r["month"])
    total = sum(per_year.values())
    rows, _ = group_all()
    cands = [json.loads(l) for l in open(DATA / "courses_candidates.jsonl")]

    out = ["# Online courses Hacker News keeps recommending", "",
           f"Source: Hacker News comments {min(months)} to {max(months)} "
           "([open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news)). "
           "A *match* is a comment that links a course page and has at least 100 characters "
           "of its own text. Pipeline and rules: see `CLAUDE.md`.", "",
           f"**{total:,} matching comments**, **{len(rows):,} course keys**, "
           f"**{len(cands)} candidates** (>= 3 distinct authors in >= 2 distinct months).", "",
           "## Matches per year", "", "| Year | Matching comments |", "|---|---:|"]
    out += [f"| {y} | {n:,} |" for y, n in sorted(per_year.items())]
    out += ["", "## Candidates by threshold", "",
            "Courses kept when mentioned by at least *A* distinct authors in at least "
            "*M* distinct months.", "", "| Authors (A) | Months (M) | Courses |", "|---:|---:|---:|"]
    out += [f"| {a} | {m} | {survivors(rows, a, m)} |" for a, m in THRESHOLDS]
    out += ["", f"## Top {args.top} courses", "",
            "Ranked by distinct authors. Titles are inferred from URLs or set by hand "
            "(`scripts/course_info.py`). \"(readings)\" / \"(student reports)\" mark course "
            "pages that HN mostly links as articles rather than recommends as courses.", "",
            "| # | Course | Key | Authors | Months | Years | Quote |",
            "|---:|---|---|---:|---:|---|---|"]
    for i, r in enumerate(cands[:args.top], 1):
        q, m = pick_quote(r)
        link = r["urls"][0]
        out.append(
            f"| {i} | [{md(likely_title(r))}]({link}) | `{r['key']}` | {r['n_authors']} | "
            f"{r['n_months']} | {r['first_year']}–{r['last_year']} | "
            f"\"{md(q)}\" — [{md(m['author'])}, {m['month']}]({HN_ITEM.format(m['hn_comment_id'])}) |")
    out.append("")
    (DATA / "REPORT.md").write_text("\n".join(out))
    print(f"wrote data/REPORT.md ({args.top} courses)")


if __name__ == "__main__":
    main()
