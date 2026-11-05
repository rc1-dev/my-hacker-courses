#!/usr/bin/env python3
"""Regenerate docs/data/ (the GitHub Pages site) from data/.

Inputs: data/courses_candidates.jsonl, data/courses_meta.jsonl, data/mentions_raw.jsonl,
data/course_embeddings.npy + course_embedding_keys.json (scripts/embed_courses.py), and
my-hackertube's raw data (--hackertube, default ../my-hackertube; its
data/hackertube-data/ folder, or hackertube-data.zip, which is read in place).

Writes (docs/data/ is wiped first):
  courses.json   {"meta": {...}, "courses": [...]}: one row per candidate, rank order.
                 Fields from earlier versions (rank, key, title, url, n_authors, n_mentions,
                 n_months, first_year, last_year, file_link_share, quote, quote_author,
                 quote_month, quote_url, page_title, provider, description, instructors,
                 access, has_video_lectures, has_assignments, status, final_url,
                 wayback_url, dead_reason) plus:
                   id        URL-safe slug of key (course.html?c=<id>)
                   summary   page description, else page title if it adds words (lite route: no LLM text)
                   format    "Online course" | "OpenCourseWare" | "Class page" | "Reading list"
                   free      "free" | "audit" | null; access, else inferred from the provider
                   free_src  "page" | "provider" | null
                   video     true when the page says so or HackerTube has its lectures
                   cats      HackerTube category ids, voted by the 20 nearest HackerTube videos
                             (scored above each category's average vote)
                   nb        [[index into courses, cosine], ...] 12 nearest courses
                   n_lec     number of matched HackerTube lectures
  c/<id>.json    {"mentions": [{id, author, month, story, url, text}], "similar": [...],
                  "lectures": [{vid, title, channel, why, recs, match}]}
  taxonomy.json  HackerTube areas -> categories used by at least one course, with `count`
  timeline.json  {"years": [{y, mentions, people, courses, new, top: [[i, authors]], debuts: [i]}]}

Lectures on HackerTube: videos with content_type lecture or course whose title carries
this course's code (MIT 6.006, CS229, 15-213) or descriptive title, or with embedding
cosine >= LEC_STRONG and the same institution. A title match needs 4+ words, or the same
institution on both sides; pairs naming two different institutions
are rejected. Embedding-only matches are mostly same topic, different course, hence the
strict rule.

Later sessions: rerun this after changing data/, and when you add fields here, also
show them in docs/ (see CLAUDE.md).

Usage: python3 scripts/build_site.py [--hackertube ../my-hackertube]
"""
import argparse
import collections
import io
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonicalize import group_all  # noqa: E402
from course_info import HN_ITEM, likely_title, pick_quote  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "data"

N_NEIGHBORS = 12
N_CAT_VOTERS = 20          # nearest HackerTube videos voting on a course's categories
CAT_MIN_VOTE = 2.5         # summed cosine a category needs
CAT_MIN_EXCESS = 1.0       # ... and how far above its average over all courses
META_CATS = {"university-lectures", "education-learning"}   # say nothing about the topic
LEC_MIN_SIM = 0.70         # a code/title match still needs to be on topic
LEC_STRONG = 0.85          # embedding-only match, same institution required
MAX_LECTURES = 12
EXCERPT_CHARS = 700
N_TIMELINE = 24

PLATFORMS = {"Coursera", "edX", "Udacity", "Udemy", "Khan Academy"}
ONLINE_KEYS = ("nand2tetris.org", "course.fast.ai", "missing.csail.mit.edu", "cs50.harvard.edu",
               "lagunita.stanford.edu", "online.stanford.edu")
FREE_PROVIDERS = {"MIT OpenCourseWare", "Khan Academy"}
FREE_KEYS = ("nand2tetris.org", "course.fast.ai", "missing.csail.mit.edu", "cs50.harvard.edu")
# institution -> names that identify it in a video title or channel
INST = {
    "MIT": r"\bmit\b|massachusetts institute", "Stanford": r"stanford", "Berkeley": r"berkeley",
    "CMU": r"\bcmu\b|carnegie mellon", "Harvard": r"harvard", "Caltech": r"caltech",
    "Princeton": r"princeton", "Cornell": r"cornell", "Yale": r"\byale\b",
    "UW": r"university of washington", "Oxford": r"oxford", "Cambridge": r"cambridge",
    "Columbia": r"columbia", "Brown": r"\bbrown university", "UIUC": r"illinois|uiuc",
    "Georgia Tech": r"georgia tech", "NYU": r"\bnyu\b", "Waterloo": r"waterloo",
    "DeepMind": r"deepmind", "UCL": r"\bucl\b",
}
PROVIDER_INST = {"MIT OpenCourseWare": "MIT", "Carnegie Mellon": "CMU", "UC Berkeley": "Berkeley",
                 "University of Washington": "UW"}


def slug(key):
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def trim(text, n):
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def load_hackertube(root):
    """(videos by vid, taxonomy, embedding ids, float32 embeddings) from a checkout."""
    src = root / "data" / "hackertube-data"
    if (src / "videos.jsonl").exists():
        read = lambda name: (src / name).read_bytes()  # noqa: E731
    else:
        z = zipfile.ZipFile(root / "hackertube-data.zip")
        read = lambda name: z.read("hackertube-data/" + name)  # noqa: E731
    videos = {}
    for line in read("videos.jsonl").decode().splitlines():
        if line.strip():
            v = json.loads(line)
            videos[v["vid"]] = v
    tax = json.loads(read("taxonomy.json"))
    ids = json.loads(read("embedding_ids.json"))
    emb = np.load(io.BytesIO(read("embeddings.npy"))).astype(np.float32)
    return videos, tax, ids, emb


def course_code(key):
    """Regex for the course number in a key, or None (platform slugs carry none)."""
    host, _, rest = key.partition("/")
    last = rest.split("/")[-1] if rest else ""
    if host == "mit.edu":
        return r"(?<![\d.])" + re.escape(last) + r"(?:j|sc)?(?![\d])"
    m = re.fullmatch(r"(\d{2})-(\d{3})", last)
    if m:
        return r"\b" + m.group(1) + r"[- ]?" + m.group(2) + r"\b"
    m = re.fullmatch(r"([a-z]{2,6})(\d{2,4}[a-z]?)", last)
    if m and host not in ("coursera.org", "udemy.com", "udacity.com", "edx.org", "khanacademy.org"):
        return r"\b" + m.group(1) + r"\s?-?" + m.group(2) + r"\b"
    return None


def descriptive_title(title):
    """'MIT 6.001 Structure and ...' -> 'structure and ...'; None if too generic."""
    t = re.sub(r"\(.*?\)", "", title)
    t = re.sub(r"^[^:]{1,40}:\s*", "", t)                        # "Coursera: ", "fast.ai: "
    t = re.sub(r"^(?:\S+\s+)?[A-Z]*[\d.\-]+[A-Z]?\s+", "", t)    # "MIT 6.001 ", "Stanford CS229 "
    t = norm(t)
    return t if len(t.split()) >= 2 and len(t) >= 14 else None


def institutions(text):
    text = (text or "").lower()
    return {name for name, pat in INST.items() if re.search(pat, text)}


def course_institution(c):
    p = c["provider"] or ""
    if p in PROVIDER_INST:
        return {PROVIDER_INST[p]}
    return institutions(p + " " + c["title"])


def match_lectures(courses, C, videos, ids, emb):
    lec = [ids[i] for i in range(len(ids)) if videos[ids[i]]["content_type"] in ("lecture", "course")]
    rows = {v: i for i, v in enumerate(ids)}
    LE = emb[[rows[v] for v in lec]]
    S = C @ LE.T
    lec_text = [norm(videos[v]["title"]) for v in lec]
    lec_inst = [institutions(videos[v]["title"] + " " + videos[v]["channel"]) for v in lec]
    out = []
    for i, c in enumerate(courses):
        code = course_code(c["key"])
        code_re = re.compile(code, re.I) if code else None
        dt = descriptive_title(c["title"])
        ci = course_institution(c)
        hits = []
        for j in np.argsort(-S[i]):
            s = float(S[i, j])
            if s < LEC_MIN_SIM:
                break
            v = videos[lec[j]]
            vi = lec_inst[j]
            if ci and vi and not (ci & vi):
                continue
            how = None
            if code_re and code_re.search(v["title"] + " " + v["channel"]):
                how = "code"
            elif dt and dt in lec_text[j] and (len(dt.split()) >= 4 or ci & vi):
                how = "title"     # short titles ("Linear Algebra") are topics, not courses
            elif s >= LEC_STRONG and ci & vi:
                how = "similar"
            if how:
                hits.append({"vid": v["vid"], "title": v["title"], "channel": v["channel"],
                             "why": v.get("why_hn") or v.get("summary"),
                             "recs": (v.get("hn") or {}).get("recommenders"),
                             "match": how, "sim": round(s, 3)})
            if len(hits) >= MAX_LECTURES:
                break
        out.append(hits)
    return out


def vote_categories(C, videos, ids, emb):
    """Up to 2 HackerTube categories per course, by its N_CAT_VOTERS nearest videos.

    Each neighbour votes its categories with its cosine. Categories whose videos sit
    near every course (SICP lectures -> functional programming) would win everywhere, so
    a category's score is its vote minus its average vote over all courses."""
    S = C @ emb.T
    votes = []
    for i in range(len(C)):
        v = collections.Counter()
        for j in np.argsort(-S[i])[:N_CAT_VOTERS]:
            for cat in videos[ids[j]]["categories"]:
                if cat["id"] not in META_CATS:
                    v[cat["id"]] += float(S[i, j])
        votes.append(v)
    base = collections.Counter()
    for v in votes:
        base.update(v)
    for k in base:
        base[k] /= len(votes)
    cats = []
    for v in votes:
        excess = sorted(((n - base[k], k) for k, n in v.items() if n >= CAT_MIN_VOTE), reverse=True)
        best = [k for e, k in excess[:2] if e >= CAT_MIN_EXCESS]
        # second category only if it is close to the first
        if len(best) == 2 and excess[1][0] < 0.6 * excess[0][0]:
            best = best[:1]
        cats.append(best)
    return cats


def page_summary(c):
    """Page description, else the page title when it says more than the course title."""
    if c["description"]:
        return c["description"]
    pt = c["page_title"] or ""
    if re.search(r"\b(home|login|log in|sign in|index)\b", pt, re.I):
        return None
    new = set(norm(pt).split()) - set(norm(c["title"]).split())
    return pt if len([w for w in new if not w.isdigit()]) >= 2 else None


def course_format(c):
    if re.search(r"\((?:readings|student reports)", c["title"]):
        return "Reading list"
    if c["provider"] == "MIT OpenCourseWare":
        return "OpenCourseWare"
    if c["provider"] in PLATFORMS or c["key"].startswith(ONLINE_KEYS):
        return "Online course"
    return "Class page"


def free_status(c, fmt):
    if c["access"]:
        return c["access"], "page"
    if c["provider"] in FREE_PROVIDERS or c["key"].startswith(FREE_KEYS) or fmt == "Class page":
        return "free", "provider"
    return None, None


def build_timeline(courses, index):
    rows, _ = group_all()
    by_year = collections.defaultdict(lambda: collections.defaultdict(set))   # y -> i -> authors
    comments, people, first = collections.defaultdict(set), collections.defaultdict(set), {}
    for r in rows:
        i = index.get(r["key"])
        if i is None:
            continue
        for m in r["_ms"]:
            y = int(m["month"][:4])
            by_year[y][i].add(m["author"])
            comments[y].add(m["hn_comment_id"])
            people[y].add(m["author"])
            first[i] = min(first.get(i, y), y)
    years = []
    for y in sorted(by_year):
        cs = by_year[y]
        top = sorted(cs, key=lambda i: (-len(cs[i]), i))[:N_TIMELINE]
        debuts = sorted(i for i in cs if first[i] == y)[:N_TIMELINE]
        years.append({"y": y, "mentions": len(comments[y]), "people": len(people[y]),
                      "courses": len(cs), "new": sum(first[i] == y for i in cs),
                      "top": [[i, len(cs[i])] for i in top], "debuts": debuts})
    return years


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hackertube", default=str(ROOT.parent / "my-hackertube"))
    args = ap.parse_args()

    months, n_comments = set(), 0
    for line in open(DATA / "mentions_raw.jsonl"):
        months.add(json.loads(line)["month"])
        n_comments += 1
    meta_rows = {}
    if (DATA / "courses_meta.jsonl").exists():
        for line in open(DATA / "courses_meta.jsonl"):
            m = json.loads(line)
            meta_rows[m["key"]] = m
    cands = [json.loads(line) for line in open(DATA / "courses_candidates.jsonl")]
    courses = []
    for rank, r in enumerate(cands, 1):
        quote, m = pick_quote(r)
        pm = meta_rows.get(r["key"], {})
        courses.append({
            "rank": rank, "id": slug(r["key"]), "key": r["key"], "title": likely_title(r),
            "url": r["urls"][0], "n_authors": r["n_authors"], "n_mentions": r["n_mentions"],
            "n_months": r["n_months"], "first_year": r["first_year"],
            "last_year": r["last_year"], "file_link_share": r["file_link_share"],
            "quote": quote, "quote_author": m["author"], "quote_month": m["month"],
            "quote_url": HN_ITEM.format(m["hn_comment_id"]),
            "page_title": pm.get("title"), "provider": pm.get("provider"),
            "description": pm.get("description"), "instructors": pm.get("instructors"),
            "access": pm.get("access"), "has_video_lectures": pm.get("has_video_lectures"),
            "has_assignments": pm.get("has_assignments"), "status": pm.get("status"),
            "final_url": pm.get("final_url"), "wayback_url": pm.get("wayback_url"),
            "dead_reason": pm.get("dead_reason"),
        })
    assert len({c["id"] for c in courses}) == len(courses), "duplicate course slugs"

    keys = json.load(open(DATA / "course_embedding_keys.json"))
    assert keys == [c["key"] for c in courses], "embeddings are stale: run scripts/embed_courses.py"
    C = np.load(DATA / "course_embeddings.npy").astype(np.float32)
    videos, tax, ids, emb = load_hackertube(Path(args.hackertube))
    lectures = match_lectures(courses, C, videos, ids, emb)
    cats = vote_categories(C, videos, ids, emb)
    CC = C @ C.T
    np.fill_diagonal(CC, -1)

    for i, c in enumerate(courses):
        fmt = course_format(c)
        free, src = free_status(c, fmt)
        nb = np.argsort(-CC[i])[:N_NEIGHBORS]
        c.update({
            "summary": page_summary(c), "format": fmt,
            "free": free, "free_src": src,
            "video": True if c["has_video_lectures"] or lectures[i] else None,
            "cats": cats[i], "nb": [[int(j), round(float(CC[i, j]), 3)] for j in nb],
            "n_lec": len(lectures[i]),
        })

    if OUT.exists():
        shutil.rmtree(OUT)
    for i, (c, r) in enumerate(zip(courses, cands)):
        dump(OUT / "c" / (c["id"] + ".json"), {
            "mentions": [{"id": m["hn_comment_id"], "author": m["author"], "month": m["month"],
                          "story": m.get("story_title"), "url": m["url"],
                          "text": trim(m["text"].replace("[COURSE]", "[course]")
                                       .replace("[LINK]", "[link]"), EXCERPT_CHARS)}
                         for m in sorted(r["mentions"], key=lambda m: m["month"], reverse=True)],
            "similar": [j for j, _ in c["nb"]],
            "lectures": lectures[i],
        })

    used = collections.Counter(k for c in courses for k in c["cats"])
    areas = []
    for a in tax["areas"]:
        cs = [{"id": k["id"], "name": k["name"], "description": k["description"],
               "count": used[k["id"]]} for k in a["categories"] if used[k["id"]]]
        if cs:
            areas.append({"id": a["id"], "name": a["name"], "categories": cs})
    dump(OUT / "taxonomy.json", {"areas": areas})
    index = {c["key"]: i for i, c in enumerate(courses)}
    dump(OUT / "timeline.json", {"through": max(months), "years": build_timeline(courses, index)})

    meta = {"generated": time.strftime("%Y-%m-%d"), "first_month": min(months),
            "last_month": max(months), "n_comments": n_comments,
            "n_courses": len(courses), "min_authors": 3, "min_months": 2,
            "n_live": sum(c["status"] == "live" for c in courses),
            "n_dead": sum(c["status"] == "dead" for c in courses),
            "n_unknown": sum(c["status"] == "unknown" for c in courses),
            "n_with_lectures": sum(bool(x) for x in lectures),
            "n_lectures": len({h["vid"] for x in lectures for h in x})}
    with open(OUT / "courses.json", "w") as f:
        json.dump({"meta": meta, "courses": courses}, f, ensure_ascii=False, separators=(",", ":"))
    (ROOT / "docs" / ".nojekyll").touch()
    print(f"wrote docs/data/courses.json ({len(courses)} courses; {meta['n_with_lectures']} with "
          f"{meta['n_lectures']} HackerTube lectures; {sum(bool(c['cats']) for c in courses)} "
          f"categorized)")


if __name__ == "__main__":
    main()
