"""Shared helpers for the report and the site: likely course titles and quotes.

likely_title(row): hand-curated TITLES first (university pages whose URLs carry
no name), else derived from the most informative URL slug (Coursera, edX,
Udacity, Udemy, MIT OCW, Khan Academy), else "<host> <CODE>".
pick_quote(row): one short recommending sentence from the row's mentions.
"""
import re
from urllib.parse import unquote, urlsplit

HN_ITEM = "https://news.ycombinator.com/item?id={}"

# Titles for keys whose URLs don't name the course. "(readings)" / "(student
# reports)" mark keys that HN mostly links as articles, not as courses.
TITLES = {
    "nand2tetris.org": "Nand2Tetris: Build a Modern Computer from First Principles",
    "khanacademy.org": "Khan Academy",
    "course.fast.ai": "fast.ai: Practical Deep Learning for Coders",
    "missing.csail.mit.edu": "MIT: The Missing Semester of Your CS Education",
    "cs50.harvard.edu/x": "Harvard CS50: Introduction to Computer Science",
    "cs50.harvard.edu/web": "Harvard CS50 Web Programming with Python and JavaScript",
    "cs50.harvard.edu/games": "Harvard CS50 Introduction to Game Development",
    "cs50.harvard.edu/python": "Harvard CS50 Introduction to Programming with Python",
    "cs50.harvard.edu/ai": "Harvard CS50 Introduction to AI with Python",
    "cs50.harvard.edu/business": "Harvard CS50 for Business Professionals",
    "cs50.harvard.edu/sql": "Harvard CS50 Introduction to Databases with SQL",
    "large.stanford.edu/ph241": "Stanford PH241 Introduction to Nuclear Energy (student reports)",
    "large.stanford.edu/ph240": "Stanford PH240 Physics of Energy (student reports)",
    "web.stanford.edu/class/ee380": "Stanford EE380 Computer Systems Colloquium",
    "web.stanford.edu/class/cs229": "Stanford CS229 Machine Learning",
    "web.stanford.edu/class/cs224n": "Stanford CS224N Natural Language Processing",
    "web.stanford.edu/class/cs193p": "Stanford CS193P Developing iOS Apps",
    "web.stanford.edu/class/cs140": "Stanford CS140 Operating Systems (Pintos)",
    "web.stanford.edu/class/cs240": "Stanford CS240 Advanced Topics in Operating Systems (readings)",
    "web.stanford.edu/class/cs107": "Stanford CS107 Programming Paradigms",
    "web.stanford.edu/class/cs106a": "Stanford CS106A Programming Methodology",
    "web.stanford.edu/class/cs106b": "Stanford CS106B Programming Abstractions",
    "web.stanford.edu/class/cs106x": "Stanford CS106X Programming Abstractions (accelerated)",
    "web.stanford.edu/class/ee364a": "Stanford EE364A Convex Optimization I",
    "web.stanford.edu/class/ee364b": "Stanford EE364B Convex Optimization II",
    "web.stanford.edu/class/ee263": "Stanford EE263 Introduction to Linear Dynamical Systems",
    "web.stanford.edu/class/ee261": "Stanford EE261 The Fourier Transform and its Applications",
    "web.stanford.edu/class/cs81n": "Stanford CS81N (readings: In the Beginning... was the Command Line)",
    "web.stanford.edu/class/ee384m": "Stanford EE384M (readings: How to Read a Paper)",
    "web.stanford.edu/class/polisci120a": "Stanford POLISCI120A (readings)",
    "web.stanford.edu/class/cs343": "Stanford CS343 Advanced Topics in Compilers (readings)",
    "web.stanford.edu/class/cs231n": "Stanford CS231N Convolutional Neural Networks for Visual Recognition",
    "snap.stanford.edu/cs224w": "Stanford CS224W Machine Learning with Graphs",
    "cva.stanford.edu/cs99s": "Stanford CS99S (readings: Hamming, Sutherland)",
    "lagunita.stanford.edu/statlearning": "Stanford Statistical Learning (Hastie & Tibshirani)",
    "lagunita.stanford.edu/compilers": "Stanford Compilers (Alex Aiken)",
    "lagunita.stanford.edu/db": "Stanford Introduction to Databases (Jennifer Widom)",
    "mit.edu/6.s081": "MIT 6.S081 Operating System Engineering",
    "mit.edu/6.828": "MIT 6.828 Operating System Engineering",
    "mit.edu/6.824": "MIT 6.824 Distributed Systems",
    "mit.edu/6.001": "MIT 6.001 Structure and Interpretation of Computer Programs",
    "cs.brown.edu/cs173": "Brown CS173 Programming Languages (PLAI)",
    "jeffe.cs.illinois.edu/algorithms": "Jeff Erickson: Algorithms (UIUC)",
    "cs.cmu.edu/15-213": "CMU 15-213 Introduction to Computer Systems",
    "cs.cmu.edu/15-410": "CMU 15-410 Operating System Design and Implementation",
    "cs.cmu.edu/15-451": "CMU 15-451 Algorithm Design and Analysis",
    "cs.cmu.edu/15-122": "CMU 15-122 Principles of Imperative Computation",
    "cs.cmu.edu/15-251": "CMU 15-251 Great Ideas in Theoretical Computer Science",
    "cs.cmu.edu/15-411": "CMU 15-411 Compiler Design",
    "cs.cmu.edu/10-701": "CMU 10-701 Machine Learning",
    "cs.umd.edu/cmsc838p": "UMD CMSC838P (readings: Royce's waterfall paper)",
    "cs.princeton.edu/cos333": "Princeton COS333 Advanced Programming Techniques",
    "cs.princeton.edu/cos226": "Princeton COS226 Algorithms and Data Structures",
    "cs.cornell.edu/cs6120": "Cornell CS6120 Advanced Compilers",
    "cs.cornell.edu/cs3110": "Cornell CS3110 Data Structures and Functional Programming",
    "cs.cornell.edu/cs312": "Cornell CS312 Data Structures and Functional Programming",
    "courses.cs.washington.edu/cse341": "UW CSE341 Programming Languages",
    "courses.cs.washington.edu/cse452": "UW CSE452 Distributed Systems",
    "inst.eecs.berkeley.edu/cs61a": "Berkeley CS61A Structure and Interpretation of Computer Programs",
    "dna.caltech.edu/cs191": "Caltech CS191 (readings: molecular biology classics)",
    "cs.ucdavis.edu/ecs188": "UC Davis ECS188 Ethics in an Age of Technology (readings)",
    "cis.rit.edu/simg716": "RIT SIMG716 (readings: Gauss and the history of the FFT)",
    "cs.utexas.edu/cs345": "UT Austin CS345 Programming Languages (readings)",
    "eecs.harvard.edu/cs286r": "Harvard CS286r (readings)",
    "ics.uci.edu/ics104": "UC Irvine ICS104 Human-Computer Interaction (readings)",
    "edx.org/ubcx+htc1x": "UBC How to Code: Simple Data (edX)",
    "edx.org/ubcx+htc2x": "UBC How to Code: Complex Data (edX)",
}

INSTITUTIONS = {
    "mit": "MIT", "stanford": "Stanford", "berkeley": "Berkeley", "cmu": "CMU",
    "princeton": "Princeton", "cornell": "Cornell", "washington": "UW", "harvard": "Harvard",
    "utexas": "UT Austin", "wisc": "Wisconsin", "caltech": "Caltech", "illinois": "UIUC",
    "uiuc": "UIUC", "brown": "Brown", "umd": "UMD", "uci": "UC Irvine", "ucdavis": "UC Davis",
    "yale": "Yale", "columbia": "Columbia", "gatech": "Georgia Tech", "nyu": "NYU",
    "umich": "Michigan", "ucsd": "UC San Diego", "ucla": "UCLA", "uchicago": "UChicago",
    "rit": "RIT", "asu": "Arizona State", "upenn": "Penn", "duke": "Duke", "jhu": "Johns Hopkins", "rice": "Rice",
    "purdue": "Purdue", "umass": "UMass", "utah": "Utah", "ucsb": "UC Santa Barbara",
    "ucsc": "UC Santa Cruz", "northwestern": "Northwestern", "virginia": "UVA",
    "umbc": "UMBC", "usc": "USC", "ufl": "Florida", "colorado": "Colorado",
    "unc": "UNC", "vt": "Virginia Tech", "psu": "Penn State", "osu": "Ohio State",
    "linuxfoundation": "Linux Foundation", "mitx": "MIT", "harvardx": "Harvard",
    "berkeleyx": "Berkeley", "ubc": "UBC", "louvain": "UCLouvain",
}
PLATFORM = {"coursera.org": "Coursera", "edx.org": "edX", "udacity.com": "Udacity",
            "udemy.com": "Udemy", "khanacademy.org": "Khan Academy"}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
         "the", "to", "with", "vs"}
TERM_TAIL = re.compile(r"-(?:fall|spring|summer|winter|january|iap)(?:-.*)?$")
GOOD = re.compile(r"\b(recommend|highly|excellent|great|best|amazing|fantastic|love[ds]?|"
                  r"favou?rite|must|worth|changed|wonderful|brilliant|helped|enjoy)", re.I)


def titlecase(slug):
    words = [w for w in re.split(r"[-_+ ]+", slug) if w]
    out = []
    for i, w in enumerate(words):
        if re.fullmatch(r"[a-z]{1,4}\d+[a-z]?|\d.*", w) or w in ("ai", "ml", "ios", "sql", "sat", "act", "gre", "gmat", "mcat", "lsat"):
            out.append(w.upper())
        elif i and w in SMALL:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _ocw_title(row, num):
    for u in row["urls"]:
        p = urlsplit(u if "://" in u else "http://" + u)
        if "ocw.mit.edu" not in (p.hostname or ""):
            continue
        for seg in unquote(p.path).lower().split("/"):
            m = re.match(r"(?:\d{1,2}[a-z]?|[a-z]{2,4})-s?\d{2,4}(?:sc|j|x)?-(.+)", seg)
            if m and "-" + num.split(".")[1] in "-" + seg.split("-", 1)[1]:
                return f"MIT {num.upper()} " + titlecase(TERM_TAIL.sub("", m.group(1)))
    return None


def likely_title(row):
    key = row["key"]
    if key in TITLES:
        return TITLES[key]
    host, _, rest = key.partition("/")
    if key.startswith("mit.edu/"):
        return _ocw_title(row, rest) or f"MIT {rest.upper()}"
    if host in PLATFORM and rest:
        slug = rest.split("/")[-1]
        if host == "udacity.com":
            # prefer the descriptive slug: design-of-computer-programs--cs212
            for u in row["urls"]:
                m = re.search(r"/course/([a-z0-9-]+)--" + re.escape(slug), u.lower())
                if m:
                    return f"Udacity: {titlecase(m.group(1))} ({slug.upper()})"
            return f"Udacity {slug.upper()}"
        if host == "edx.org" and "+" in slug:
            org, code = slug.split("+", 1)
            org = org[:-1] if org.endswith("x") else org
            return f"edX: {INSTITUTIONS.get(org, org.capitalize())} {code.upper()}"
        if host == "khanacademy.org":
            return "Khan Academy: " + titlecase(rest.split("/")[-1].replace(
                "economics-finance-domain", "economics"))
        slug = re.sub(r"-\d+$", "", slug)
        return f"{PLATFORM[host]}: {titlecase(slug)}"
    if rest:
        return f"{institution(host)} {rest.upper()}"
    return host


def institution(host):
    for part in reversed(host.split(".")[:-1]):
        if part in INSTITUTIONS:
            return INSTITUTIONS[part]
    return host


def pick_quote(row, max_len=160):
    """(quote, mention) — the most recommendation-like sentence among mentions."""
    best = None
    for m in row["mentions"]:
        text = re.sub(r"\s+", " ", m["text"])
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])", text):
            sent = sent.strip()
            bare = re.sub(r"\[(COURSE|LINK)\]", "", sent).strip(" :-")
            if len(bare) < 40:
                continue
            links = sent.count("[COURSE]") + sent.count("[LINK]")
            score = 3 * bool(GOOD.search(sent)) + 4 * ("[COURSE]" in sent) \
                - 2 * max(0, links - 1) - (len(sent) > max_len) \
                - 2 * bool(re.match(r"^(\[\d+\]|>)", sent))
            if best is None or score > best[0]:
                best = (score, sent, m)
    if best is None:
        m = row["mentions"][0]
        best = (0, m["text"], m)
    _, sent, m = best
    sent = sent.replace("[COURSE]", "[course]").replace("[LINK]", "[link]")
    if len(sent) > max_len:
        sent = sent[:max_len - 1].rsplit(" ", 1)[0] + "…"
    return sent, m
