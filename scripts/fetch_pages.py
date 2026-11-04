#!/usr/bin/env python3
"""Fetch each candidate course's most-linked URL and extract page metadata.

For every row of data/courses_candidates.jsonl, fetches urls[0] (the most-linked
URL) at <= 1 request/second, caching raw HTML + fetch info in work/pages/
(<sha1>.html / <sha1>.json; reruns reuse the cache, so it is resumable).
A page is dead on a network error, HTTP >= 400, or a redirect from a deep link
to the site root (course page removed). For dead pages it asks the Wayback
Machine (archive.org/wayback/available) for the latest snapshot and, if one
exists, fetches it (also cached) to read metadata from.

Writes data/courses_meta.jsonl, one row per candidate:
  key, url, status (live|dead|blocked), http_status, final_url, dead_reason,
  wayback_url, meta_source (live|wayback|null), page_title, description,
  title, provider, instructors, access (free|paid|audit|null),
  has_video_lectures, has_assignments
Fields the page does not state are null; booleans are only ever true or null
(absence of a mention is not evidence of absence).

Usage: python3 scripts/fetch_pages.py [--limit N] [--offline]
"""
import argparse
import hashlib
import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "work" / "pages"
UA = "Mozilla/5.0 (X11; Linux x86_64) hn-course-picks/1.0 (research crawler)"
WAYBACK = "https://archive.org/wayback/available"
_last = [0.0]


def throttle():
    wait = 1.0 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


def cache_paths(url):
    h = hashlib.sha1(url.encode()).hexdigest()
    return CACHE / f"{h}.json", CACHE / f"{h}.html"


def fetch(url, offline=False):
    """Return info dict {url, http_status, final_url, error}; HTML cached on disk."""
    jp, hp = cache_paths(url)
    if jp.exists():
        return json.loads(jp.read_text())
    if offline:
        return None
    throttle()
    info = {"url": url, "http_status": None, "final_url": None, "error": None}
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
        info["http_status"], info["final_url"] = r.status_code, r.url
        ctype = r.headers.get("content-type", "")
        info["content_type"] = ctype
        if "html" in ctype or not ctype:
            hp.write_bytes(r.content[:5_000_000])
    except requests.RequestException as e:
        info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    jp.write_text(json.dumps(info))
    return info


def wayback_lookup(url, offline=False):
    jp, _ = cache_paths("wayback:" + url)
    if jp.exists():
        return json.loads(jp.read_text())
    if offline:
        return None
    res = {"wayback_url": None, "error": None}
    for attempt in range(5):  # archive.org rate-limits with non-JSON replies: back off
        throttle()
        try:
            # no timestamp param: latest snapshot (timestamp= triggers 429s)
            r = requests.get(WAYBACK, params={"url": url},
                             headers={"User-Agent": UA}, timeout=60)
            snap = r.json().get("archived_snapshots", {}).get("closest") or {}
            res["error"] = None
            if snap.get("available") and str(snap.get("status", "200")).startswith(("2", "3")):
                res["wayback_url"] = snap["url"].replace("http://web.archive.org", "https://web.archive.org")
            break
        except (requests.RequestException, ValueError) as e:
            res["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            time.sleep(5 * 2 ** attempt)
    if not res["error"]:  # only cache real answers, so reruns retry failures
        jp.write_text(json.dumps(res))
    return res


CHALLENGE = re.compile(rb"<title>\s*(Client Challenge|Just a moment|Attention Required|Access denied"
                       rb"|Are you a robot|Request Rejected|One moment, please|[^<]*Authentication Service|Choose Identity Provider"
                       rb"|[^<]*Single Sign[- ]On|Sign in|Log in|Login)", re.I)


def is_blocked(info, url):
    """Server refused the crawler (rate limit / bot wall): neither live nor dead."""
    if info is None or info.get("error"):
        return None
    if info["http_status"] in (401, 403, 429, 999):
        return f"HTTP {info['http_status']} (bot block)"
    _, hp = cache_paths(url)
    if hp.exists() and CHALLENGE.search(hp.read_bytes()[:20000]):
        return "bot challenge or login page"
    return None


def is_dead(info, url):
    if info is None:
        return "not fetched"
    if info.get("error"):
        return "network error"
    if info["http_status"] >= 400:
        return f"HTTP {info['http_status']}"
    a, b = urlparse(url), urlparse(info["final_url"] or url)
    if a.path.strip("/") and not b.path.strip("/") and not b.query:
        return "redirects to site root"
    # platforms identify a course by its slug/code: a redirect that drops it lands
    # on a different (replacement) course, so the original course is gone
    host = (a.hostname or "").lower()
    if any(host.endswith(d) for d in ("udacity.com", "coursera.org", "edx.org", "udemy.com")):
        seg = [x for x in a.path.lower().split("/") if x][-1:]
        code = seg[0].rsplit("--", 1)[-1] if seg else ""
        if code and code not in (info["final_url"] or "").lower():
            return "redirects to a different course"
    return None


# ---------- extraction ----------

PROVIDERS = [
    ("ocw.mit.edu", "MIT OpenCourseWare"), ("mit.edu", "MIT"),
    ("coursera.org", "Coursera"), ("edx.org", "edX"), ("udacity.com", "Udacity"),
    ("udemy.com", "Udemy"), ("khanacademy.org", "Khan Academy"),
    ("fast.ai", "fast.ai"), ("harvard.edu", "Harvard"), ("stanford.edu", "Stanford"),
    ("berkeley.edu", "UC Berkeley"), ("cmu.edu", "Carnegie Mellon"),
    ("nand2tetris.org", "Nand2Tetris"), ("youtube.com", "YouTube"),
    ("washington.edu", "University of Washington"), ("cornell.edu", "Cornell"),
    ("princeton.edu", "Princeton"), ("caltech.edu", "Caltech"),
    ("utexas.edu", "UT Austin"), ("uwaterloo.ca", "University of Waterloo"),
    ("gatech.edu", "Georgia Tech"), ("illinois.edu", "UIUC"), ("yale.edu", "Yale"),
    ("columbia.edu", "Columbia"), ("nyu.edu", "NYU"), ("ucsd.edu", "UC San Diego"),
    ("umich.edu", "University of Michigan"), ("wisc.edu", "UW-Madison"),
    ("brown.edu", "Brown"), ("ucla.edu", "UCLA"), ("utoronto.ca", "University of Toronto"),
]


def provider_for(url):
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("web.archive.org"):
        return None
    for dom, name in PROVIDERS:
        if host == dom or host.endswith("." + dom):
            return name
    m = re.search(r"([a-z0-9-]+)\.edu$", host)
    return f"{m.group(1).upper()} (.edu)" if m else host or None


def clean(s, n=400):
    if not s:
        return None
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return (s[:n].rstrip() + "…") if len(s) > n else (s or None)


def json_ld(soup):
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        stack = [d]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                out.append(x)
                stack.extend(v for v in x.values() if isinstance(v, (list, dict)))
    return out


def names(v):
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return names(v.get("name"))
    if isinstance(v, list):
        return [n for x in v for n in names(x)]
    return []


INSTR_RE = re.compile(
    r"\b(?:Instructors?|Taught by|Lecturers?|Professors?)\s*(?:\(s\))?\s*[:\-–]\s*"
    r"((?:(?:Prof\.|Dr\.)\s*)?[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){1,3}"
    r"(?:\s*(?:,|and|&)\s*(?:(?:Prof\.|Dr\.)\s*)?[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){1,3}){0,5})")
NOT_NAME = re.compile(r"\b(Office|Hours|Email|TBA|Course|Lecture|Section|Room|Hall|The|Staff)\b")
VIDEO_RE = re.compile(r"\b(video lectures?|lecture videos?|lecture recordings?|recorded lectures?"
                      r"|videos? of (?:the )?lectures?|watch (?:the )?lectures?)\b", re.I)
ASSIGN_RE = re.compile(r"\b(assignments?|problem sets?|homeworks?|programming exercises?"
                       r"|graded quizzes|projects? and quizzes)\b", re.I)
FREE_RE = re.compile(r"\b(free online course|free course|for free|free of charge|freely available"
                     r"|free (?:&|and) open|totally free|completely free|is free\b|100% free)", re.I)
AUDIT_RE = re.compile(r"\b(audit (?:this|the) course|audit for free|audit track|free to audit"
                      r"|enroll for free|enrol for free)\b", re.I)
PRICE_RE = re.compile(r"(?:US)?\$\s?\d{1,4}(?:\.\d\d)?\b")


def extract(raw, page_url):
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "noscript", "template"]):
        if t.get("type") != "application/ld+json":
            t.decompose()
    ld = json_ld(soup)

    def meta(*keys):
        for k in keys:
            t = soup.find("meta", attrs={"property": k}) or soup.find("meta", attrs={"name": k})
            if t and t.get("content", "").strip():
                return clean(t["content"])
        return None

    page_title = clean(soup.title.get_text()) if soup.title else None
    og_title = meta("og:title", "twitter:title")
    desc = meta("description", "og:description", "twitter:description")
    courses = [d for d in ld if str(d.get("@type", "")).lower() in ("course", "['course']")
               or "Course" in (d.get("@type") if isinstance(d.get("@type"), list) else [])]
    title = clean(courses[0].get("name"), 200) if courses and isinstance(courses[0].get("name"), str) \
        else (og_title or page_title)
    if title:
        title = re.sub(r"\s*[|\-–—]\s*(MIT OpenCourseWare|Coursera|edX|Udemy|Udacity|Khan Academy)\s*$",
                       "", title).strip() or title

    text = re.sub(r"\s+", " ", soup.get_text(" "))
    # instructors: JSON-LD first, then an explicit "Instructor(s):" label on the page
    instr = []
    for d in courses:
        for k in ("instructor", "creator", "author"):
            instr += [n for n in names(d.get(k)) if n and not re.search(r"\b(University|Institute|College|School|Inc|LLC)\b", n)]
    if not instr:
        for m in INSTR_RE.finditer(text):
            parts = re.split(r"\s*(?:,|\band\b|&)\s*", m.group(1))
            instr += [p.strip() for p in parts if p.strip() and not NOT_NAME.search(p)]
            if instr:
                break
    instr = [re.sub(r"\s+(Top Instructor|Enroll(?: for Free)?|Instructor)$", "", i).strip() for i in instr]
    instr = list(dict.fromkeys(i for i in instr if 3 <= len(i) <= 60))[:8] or None

    # access: only what the page says
    head = " ".join(filter(None, [title, desc, page_title]))
    offers = [o for d in ld for o in ([d.get("offers")] if isinstance(d.get("offers"), dict)
                                      else d.get("offers") or []) if isinstance(o, dict)]
    prices = []
    for o in offers:
        try:
            prices.append(float(str(o.get("price")).replace(",", "")))
        except (TypeError, ValueError):
            pass
    free_ld = any(d.get("isAccessibleForFree") in (True, "true", "True") for d in ld)
    if AUDIT_RE.search(text):
        access = "audit"
    elif free_ld or (prices and max(prices) == 0) or FREE_RE.search(head) or FREE_RE.search(text):
        access = "free"
    elif (prices and min(prices) > 0) or (PRICE_RE.search(text) and re.search(r"\b(buy now|add to cart|enroll now|subscribe)\b", text, re.I)):
        access = "paid"
    else:
        access = None

    has_video = True if (VIDEO_RE.search(text) or soup.find("video")
                         or soup.find("iframe", src=re.compile(r"youtube|vimeo|youtu\.be"))) else None
    has_assign = True if ASSIGN_RE.search(text) else None
    return {"page_title": page_title, "description": desc, "title": title,
            "instructors": instr, "access": access,
            "has_video_lectures": has_video, "has_assignments": has_assign}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--offline", action="store_true", help="only use the cache")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(ROOT / "data" / "courses_candidates.jsonl")][: args.limit]
    out, n_live, n_dead, n_snap, n_blocked = [], 0, 0, 0, 0
    for i, r in enumerate(rows, 1):
        url = r["urls"][0]
        info = fetch(url, args.offline)
        if info and info.get("http_status") == 429 and not args.offline:  # retry once, slowly
            time.sleep(30)
            for pth in cache_paths(url):
                pth.unlink(missing_ok=True)
            info = fetch(url)
        blocked = is_blocked(info, url)
        reason = None if blocked else is_dead(info, url)
        status = "blocked" if blocked else "dead" if reason else "live"
        rec = {"key": r["key"], "url": url, "status": status,
               "http_status": info and info.get("http_status"),
               "final_url": info and info.get("final_url"), "dead_reason": reason or blocked,
               "wayback_url": None, "meta_source": None}
        src_html, src_url = None, None
        if blocked:
            n_blocked += 1
        elif not reason:
            n_live += 1
            _, hp = cache_paths(url)
            if hp.exists():
                src_html, src_url, rec["meta_source"] = hp.read_bytes(), rec["final_url"], "live"
        if reason or blocked:
            n_dead += bool(reason)
            wb = wayback_lookup(url, args.offline)
            if wb and wb.get("wayback_url"):
                n_snap += 1
                rec["wayback_url"] = wb["wayback_url"]
                winfo = fetch(wb["wayback_url"], args.offline)
                _, hp = cache_paths(wb["wayback_url"])
                if winfo and not winfo.get("error") and winfo["http_status"] < 400 and hp.exists():
                    src_html, src_url, rec["meta_source"] = hp.read_bytes(), url, "wayback"
        fields = {"page_title": None, "description": None, "title": None, "instructors": None,
                  "access": None, "has_video_lectures": None, "has_assignments": None}
        if src_html:
            try:
                fields = extract(src_html, src_url)
            except Exception as e:  # malformed page: keep nulls
                print(f"extract failed {url}: {e}", file=sys.stderr)
        rec["provider"] = provider_for(rec["final_url"] if status == "live" and rec["final_url"] else url)
        rec.update(fields)
        out.append(rec)
        if i % 25 == 0:
            print(f"{i}/{len(rows)} live={n_live} dead={n_dead} blocked={n_blocked}", flush=True)
    with open(ROOT / "data" / "courses_meta.jsonl", "w") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote data/courses_meta.jsonl: {len(out)} courses, live={n_live}, dead={n_dead}, "
          f"blocked (bot wall, status unknown)={n_blocked}, wayback snapshots={n_snap}")


if __name__ == "__main__":
    main()
