#!/usr/bin/env python3
"""Group course mentions by canonical course key and keep the recurring ones.

Reads data/mentions_raw.jsonl, maps every course link to a key, and writes
data/courses_candidates.jsonl with courses mentioned by >= --min-authors
distinct people in >= --min-months distinct months.

Key scheme (see CLAUDE.md):
  platform + slug       coursera.org/learn/machine-learning, udemy.com/course/x
  MIT by course number  mit.edu/6.006   (OCW all years, pdos, csail, web.mit.edu)
  university pages      host/code, term/year folders ignored,
                        Stanford class pages as web.stanford.edu/class/cs231n
  single-course sites   nand2tetris.org, course.fast.ai, missing.csail.mit.edu
Links that don't map to a course (papers, homepages, KA careers...) get no key.

Usage:
  python3 scripts/canonicalize.py [--min-authors 3 --min-months 2] [--top 50]
"""
import argparse
import collections
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
IN = ROOT / "data" / "mentions_raw.jsonl"
OUT = ROOT / "data" / "courses_candidates.jsonl"
MAX_MENTIONS = 20

TERM_RE = re.compile(
    r"^(?:(?:19|20)\d\d|\d\d)?[-_]?"
    r"(?:fall|autumn|aut|au|fa|f|spring|spr|sp|s|summer|sum|su|winter|win|wi|w|iap|t\d)"
    r"[-_]?(?:(?:19|20)?\d\d)?$")
YEAR_RE = re.compile(r"^(?:19|20)\d\d(?:[-_]\d+)?$|^\d{2}$")
MIT_NUM_RE = re.compile(
    r"^(\d{1,2}[a-z]?|[a-z]{2,4})[.-](s?\d{2,4}(?:sc|j|x)?)"
    r"(?=$|[-_.]|fall|spring|summer|winter|iap|january)", re.I)
NAME_STOP = {"data", "notes", "papers", "slides", "readings", "handouts", "files",
             "lectures", "materials", "projects", "assets", "resources", "docs",
             "pubs", "content", "uploads", "images", "static", "student-papers"}
KA_SUBJECTS = {"math", "science", "computing", "humanities", "economics-finance-domain",
               "test-prep", "partner-content", "ela", "arts-humanities", "computer-programming"}
CODE_RE = re.compile(r"^([a-z]{1,6})[-_ ]?(\d{2,4}[a-z]{0,2})(?=$|[-_.])", re.I)
CMU_RE = re.compile(r"^(\d{2})-?(\d{3})(?=$|[-_.])")
MARKERS = {"courses", "course", "class", "classes", "teaching"}
SKIP = {"archive", "www", "public", "pub", "people", "home", "index.html"}
STANFORD_WEB = {"stanford.edu", "web.stanford.edu"}
FILE_RE = re.compile(r"\.(pdf|ps|ppt|pptx|doc|docx|txt|mp4|m4v|mp3|zip|gz|djvu)$", re.I)
KA_SKIP = {"careers", "jobs", "about", "profile", "login", "signup", "donate", "join",
           "contribute", "standards", "coach", "teacher", "district", "kids",
           "khan-labs", "college-careers-more", "blog", "talks-and-interviews"}
# see.stanford.edu/see/courseinfo.aspx?coll=<guid>: the live pages are gone, so
# these were identified from the comment text around each link. The two Boyd
# convex-optimization GUIDs are always linked together; a/b order is a guess.
SEE_COLL = {
    "348ca38a": "cs229", "2d712634": "cs107", "824a47e1": "cs106a",
    "11f4f422": "cs106b", "63480b48": "cs224n", "17005383": "ee263",
    "84d174c2": "ee261", "2db7ced4": "ee364a", "523bbab2": "ee364b",
    "86cc8662": "cs223a",
}
# CS50 family: every host (cs50.harvard.edu, edX, pll/online-learning.harvard.edu)
# collapses to cs50.harvard.edu/<track>; the intro course is /x.
CS50_TRACKS = [
    ("web", ("web-programming", "/web")), ("games", ("game-development", "/games")),
    ("python", ("programming-with-python", "/python")),
    ("ai", ("artificial-intelligence", "/ai")), ("sql", ("sql",)),
    ("business", ("business",)), ("law", ("law",)),
    ("technology", ("technology",)), ("scratch", ("scratch",)),
    ("r", ("programming-with-r", "/r/")), ("cybersecurity", ("cybersecurity",)),
    ("mobile", ("mobile",)),
]


def is_term(seg):
    return bool(TERM_RE.match(seg) or YEAR_RE.match(seg))


def mit_number(seg):
    """'6-034-artificial-intelligence-fall-2010' / '6.828' -> '6.034' / '6.828'."""
    m = MIT_NUM_RE.match(seg)
    if not m or m.group(1).lower() in ("cs", "ee", "res"):
        return None
    dept, num = m.group(1).lower(), m.group(2).lower()
    # 6.042J (cross-listed) == 6.042; 18.06SC (OCW Scholar re-run) merged into 18.06
    num = re.sub(r"(j|sc)$", "", num)
    return f"{dept}.{num}"


def code_of(seg):
    """Course code at the start of a path segment, normalised, or None."""
    seg = seg.lower().lstrip("~")
    if not seg or is_term(seg):
        return None
    m = CMU_RE.match(seg)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = CODE_RE.match(seg)
    if m:
        return m.group(1) + m.group(2)
    return None


def split(url):
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    segs = [unquote(s) for s in p.path.split("/") if s]
    return host, segs, parse_qs(p.query)


def slug_after(segs, *heads):
    for i, s in enumerate(segs[:-1]):
        if s.lower() in heads:
            return segs[i + 1].lower()
    return None


def university_key(host, segs):
    """host/code for a university page, skipping term/year/dept folders."""
    low = [s.lower() for s in segs]
    # 1. right after a marker folder (courses/, class/, teaching/ ...)
    for i, s in enumerate(low):
        if s in MARKERS:
            for t in low[i + 1:i + 5]:
                if t in SKIP or is_term(t):
                    continue
                nxt = low[low.index(t) + 1] if low.index(t) + 1 < len(low) else ""
                if t.isalpha() and len(t) <= 4:     # dept folder: cs/, ee/, math/
                    if code_of(nxt):
                        continue
                    break
                mit = mit_number(t) if host.endswith("mit.edu") else None
                if mit:
                    return "mit:" + mit
                c = code_of(t)
                if c:
                    return c
                if re.fullmatch(r"[a-z][a-z0-9-]{4,40}", t) and t not in NAME_STOP:
                    return t                       # named course: teaching/algorithms
                break
            break
    # 2. code as the first segment (or right after ~user): /~cs61a, /6.828, /~15213
    for t in low[:2]:
        if host.endswith("mit.edu"):
            mit = mit_number(t)
            if mit:
                return "mit:" + mit
        if host == "cs.cmu.edu" and re.fullmatch(r"~\d{3,5}(-\w+)?", t):
            n = re.match(r"~(\d+)", t).group(1)
            n = n if len(n) == 5 else "15" + n[-3:]   # CMU dept 15 implied (~410)
            return f"{n[:2]}-{n[2:]}"
        # /~cs61a, /6.828-style first folder, or /~user/cs345
        if low.index(t) == 0 or low[0].startswith("~"):
            c = code_of(t)
            if c:
                return c
    return None


def cs50_key(url):
    u = url.lower()
    for track, needles in CS50_TRACKS:
        if any(n in u for n in needles):
            return "cs50.harvard.edu/" + track
    return "cs50.harvard.edu/x"


def course_key(url):
    host, segs, q = split(url)
    low = [s.lower() for s in segs]
    path = "/".join(low)
    # nand2tetris: the site and its two Coursera halves are one course
    if "nand2tetris" in host + path or (host.endswith("coursera.org")
                                        and "build-a-computer" in path):
        return "nand2tetris.org"
    if "cs50" in host + path and (host.endswith("harvard.edu") or "edx.org" in host):
        return cs50_key(url)
    if host.endswith("coursera.org"):
        for head in ("learn", "specializations", "course", "professional-certificates"):
            s = slug_after(low, head)
            if s:
                return f"coursera.org/{'learn' if head == 'course' else head}/{s}"
        return None
    if host == "courses.edx.org" or (host.endswith("edx.org") and low[:1] == ["courses"]):
        # /courses/course-v1:MITx+6.002x+1T2015/... or /courses/BerkeleyX/CS188.1x/2012_Fall
        if len(segs) >= 2 and segs[1].startswith("course-v1:"):
            parts = segs[1][len("course-v1:"):].split("+")
            return (f"edx.org/{parts[0]}+{parts[1].split('_')[0]}".lower()
                    if len(parts) >= 2 else None)
        if len(segs) >= 3:
            return f"edx.org/{segs[1]}+{segs[2]}".lower()
        return None
    if host.endswith("edx.org"):
        slug = None
        if low[:1] == ["course"] and len(low) >= 2:
            slug = low[2] if len(low) >= 3 and "x" in low[1][-1:] and low[2].startswith(low[1]) else low[1]
        elif low[:1] == ["learn"] and len(low) >= 3:
            slug = low[2]
        if not slug:
            return None
        # slugs that end in <org>x-<code>x (…-harvardx-cs50x, …-uc-berkeleyx-cs188-1x)
        # collapse onto the same org+code key that courses.edx.org links use
        m = re.search(r"(?:^|-)([a-z]+x)-([a-z]*\d+[a-z0-9]*?(?:-\d+)?x)(?:-\d+)?$", slug)
        if m:
            return f"edx.org/{m.group(1)}+{m.group(2).replace('-', '.')}"
        return f"edx.org/course/{slug}"
    if host.endswith("udacity.com"):
        s = slug_after(low, "course")
        if not s:
            return None
        m = re.search(r"--([a-z]+\d+[a-z]*)$", s)
        code = m.group(1) if m else s
        return f"udacity.com/course/{code}"
    if host.endswith("udemy.com"):
        s = slug_after(low, "course")
        return f"udemy.com/course/{s}" if s else None
    if host.endswith("khanacademy.org"):
        if not low:
            return "khanacademy.org"
        if low[0] in KA_SKIP or low[0].startswith(("profile", "computing/cs/")):
            return None
        if len(low) == 1 and low[0] not in KA_SUBJECTS:
            return "khanacademy.org"                   # exercisedashboard, library...
        return "khanacademy.org/" + "/".join(low[:2])
    if host.endswith("fast.ai"):
        return "course.fast.ai"
    if host.endswith("nand2tetris.org"):
        return "nand2tetris.org"
    if host == "missing.csail.mit.edu":
        return "missing.csail.mit.edu"
    if host == "ocw.mit.edu":
        # /courses/<dept>/<num-title-term>/ (old) or /courses/<num-title-term>/ (new)
        for t in low[1:3]:
            n = mit_number(t)
            if n:
                return "mit.edu/" + n
        return None
    if host == "online.stanford.edu":
        s = slug_after(low, "course", "courses")
        return f"online.stanford.edu/courses/{s}" if s else None
    if host == "see.stanford.edu":
        s = slug_after(low, "course")
        if not s and "coll" in q:                     # courseinfo.aspx?coll=<guid>
            g = q["coll"][0].lower()
            s = SEE_COLL.get(g[:8])
            if not s:
                return "see.stanford.edu/coll/" + g
        if not s:
            m = re.search(r"(cs|ee|cme|engr)\d{2,3}[a-z]?", "/".join(low))
            s = m.group(0) if m else None
        c = code_of(s) if s else None
        return f"web.stanford.edu/class/{c}" if c else None
    if host == "lagunita.stanford.edu":
        if low[:1] == ["courses"] and len(low) >= 3:
            if low[1].startswith("course-v1:"):
                p = low[1][len("course-v1:"):].split("+")
                return f"lagunita.stanford.edu/{p[1]}" if len(p) >= 2 else None
            # /courses/<Org>/<Course>/<Run>, but some runs use <Course>/<Year>/...
            parts = [t for t in low[1:3] if not is_term(t)]
            return "lagunita.stanford.edu/" + parts[-1] if parts else None
        return None
    # code-named subdomain: cs231n.stanford.edu, cs61a.org isn't .edu so not matched
    label = host.split(".")[0]
    c = code_of(label) if host.endswith(".edu") else None
    if c and c == label:
        k = c
    else:
        k = university_key(host, segs)
    if not k:
        return None
    if k.startswith("mit:"):
        return "mit.edu/" + k[4:]
    if host.endswith("stanford.edu") and (c == label or (
            host in STANFORD_WEB and low[:1] == ["class"])):
        return f"web.stanford.edu/class/{k}"
    if host.endswith("berkeley.edu") and host.startswith(("inst.", "www-inst.")):
        host = "inst.eecs.berkeley.edu"
    return f"{host}/{k}"


def pick_mentions(ms):
    """<= MAX_MENTIONS mentions, one per author first (their longest), spread in time."""
    by_author = collections.defaultdict(list)
    for m in ms:
        by_author[m["author"]].append(m)
    for v in by_author.values():
        v.sort(key=lambda m: -len(m["text"]))
    firsts = sorted((v[0] for v in by_author.values()), key=lambda m: m["month"])
    if len(firsts) >= MAX_MENTIONS:
        step = len(firsts) / MAX_MENTIONS
        chosen = [firsts[int(i * step)] for i in range(MAX_MENTIONS)]
    else:
        chosen = list(firsts)
        rest = sorted((m for v in by_author.values() for m in v[1:]),
                      key=lambda m: -len(m["text"]))
        chosen += rest[:MAX_MENTIONS - len(chosen)]
    return sorted(chosen, key=lambda m: m["month"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-authors", type=int, default=3)
    ap.add_argument("--min-months", type=int, default=2)
    ap.add_argument("--top", type=int, default=50)
    args = ap.parse_args()

    groups = collections.defaultdict(lambda: {"urls": collections.Counter(), "mentions": {}})
    unkeyed = collections.Counter()
    for line in open(IN):
        r = json.loads(line)
        for url in r["course_urls"]:
            k = course_key(url)
            if not k:
                unkeyed[split(url)[0]] += 1
                continue
            g = groups[k]
            g["urls"][url] += 1
            g["mentions"][r["hn_comment_id"]] = r     # one mention per comment

    rows = []
    for k, g in groups.items():
        ms = list(g["mentions"].values())
        years = sorted(m["month"][:4] for m in ms)
        rows.append({
            "key": k,
            "n_mentions": len(ms),
            "n_authors": len({m["author"] for m in ms}),
            "n_months": len({m["month"] for m in ms}),
            "first_year": int(years[0]), "last_year": int(years[-1]),
            "urls": [u for u, _ in g["urls"].most_common()],
            # share of links to a file (pdf, slides, video): high = article-like
            # sharing of course material rather than recommending the course
            "file_link_share": round(sum(n for u, n in g["urls"].items()
                                         if FILE_RE.search(urlsplit(u).path))
                                     / sum(g["urls"].values()), 2),
            "_ms": ms,
        })
    rows.sort(key=lambda r: (-r["n_authors"], -r["n_mentions"], r["key"]))

    print(f"{len(groups)} keys; links without a key: {sum(unkeyed.values())} "
          f"(top hosts: {unkeyed.most_common(6)})")
    print(f"\nTop {args.top} keys (authors / mentions / months, years):")
    for r in rows[:args.top]:
        print(f"  {r['n_authors']:4d} {r['n_mentions']:4d} {r['n_months']:4d}  "
              f"{r['first_year']}-{r['last_year']}  {r['key']}")

    print("\nSurvivors (distinct authors / distinct months):")
    for a, mo in ((3, 2), (3, 3), (5, 3)):
        n = sum(r["n_authors"] >= a and r["n_months"] >= mo for r in rows)
        print(f"  {a}/{mo}: {n}")

    keep = [r for r in rows
            if r["n_authors"] >= args.min_authors and r["n_months"] >= args.min_months]
    with open(OUT, "w") as f:
        for r in keep:
            ms = pick_mentions(r.pop("_ms"))
            r["mentions"] = [{k: m[k] for k in ("hn_comment_id", "author", "month",
                                                "story_title", "url", "text")}
                             for m in ms]
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(keep)} courses to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
