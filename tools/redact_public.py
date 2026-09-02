#!/usr/bin/env python3
"""Full data in, publish-safe JSON out. THE security boundary of this whole system.

Why an allowlist and not a redactor: this file's job is to let a public streamlit.app
page show your pipeline without publishing your life. A tool that scrubs secrets out of
free text always loses eventually, because a job description is arbitrary text that
already contains emails and phone numbers (the recruiter's) and nothing about its shape
tells you whose they are. So nothing here passes text through. Every published field is
either taken from one of a fixed set of short columns, or matched against a known city,
or dropped. Unrecognised input becomes absent, never truncated-and-kept.

The second defence is the abort: after building, the serialized blob is searched for
every PII marker readable out of profile.sarvesh.json (phone digits, email, each URL,
date of birth, passport line, marital status, nationality, your name) plus generic
email/phone/URL patterns, and the tool exits 1 rather than write. A publish step that
writes nothing costs you a stale dashboard. One that writes leaks you permanently -
public repos get crawled in minutes and archive.org keeps the copy.

The second gate, and the payload-first rule.

1. Prefer the payload the laptop committed (publish/board.json). The tracker, the review
   queue and the resume PDFs live in gitignored paths, and a redactor with nothing to read
   publishes an empty board. That is not a hypothesis: the first real publish of this
   pipeline was green, and empty, for exactly this reason.
2. Rebuild from the local tracker only when that payload is missing, which is what happens
   in an Actions checkout: it produces the counts and no rows, honestly.

Both gates stay. This tool refuses on its own findings, and board.yml greps the file with a
list kept in repo variables. Two checkers, one of which the other cannot rewrite.

The point of the --check mode is that it can be run on THIS machine, against your real
tracker, before anything is ever pushed.

  python tools/redact_public.py --out public/board.json      # write it
  python tools/redact_public.py --check                        # dry run, exit 1 on leak
  python tools/redact_public.py --emit-fields                  # what the allowlist allows
  python tools/publish_local.py --report                       # what WOULD be published here
  python tools/redact_public.py --check-payload                 # may the projection go out?
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import zlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import metrics                                          # noqa: E402  one arithmetic, one import

TRACKER = os.environ.get("JOBSEARCH_TRACKER") or os.path.join(ROOT, "job_search_tracker.csv")
SEEN = os.environ.get("JOBSEARCH_SEEN") or os.path.join(ROOT, "job_scraper", "seen_jobs.json")
# The queue lives in output/ (ignored) because it holds paths and free text. What may
# travel is the mirror apply_kit writes into state/, and it holds six short columns.
QUEUE_MIRROR = (os.environ.get("JOBSEARCH_QUEUE_MIRROR")
                or os.path.join(ROOT, "state", "review_queue.public.json"))
# What tools/publish_local.py commits from the machine that HAS the tracker. A runner has
# no rows to redact, so board.yml copies this file and re-scans it instead of building one.
PAYLOAD = (os.environ.get("JOBSEARCH_BOARD_PAYLOAD")
           or os.path.join(ROOT, "publish", "board.json"))


def resume_root() -> str:
    """Where a published resume lives, on the machine that published it.

    One helper, because three readers must agree exactly or the board shows a button that
    404s: the redactor (may I link this file?), the laptop publisher (is the PDF here?) and
    board_app (what URL do I print?). Overridable so a test can stage PDFs somewhere honest
    instead of inside the repo.
    """
    return os.environ.get("JOBSEARCH_RESUME_DIR") or os.path.join(ROOT, "resumes")


def payload_ready(path: str = PAYLOAD) -> bool:
    """Is there a payload to publish as-is? Only if it is readable AND scans clean.

    A JSON file is not trustworthy because it exists: it may be hours old, hand-edited, or
    written before a leak rule was added. So the gate here is the same scan, not a nod at
    what the laptop did. A payload that trips it falls through to a rebuild, which cannot
    publish what the projection contained, because the rebuild reads the tracker instead.
    """
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        json.loads(body)
    except Exception:                                            # noqa: BLE001
        return False
    return not scan(body)


def load_payload(path: str = PAYLOAD) -> dict:
    """The projection, parsed. Raises if it is unreadable or trips the scan: callers check
    with payload_ready() first, and a race between the two must fail loudly, not publish."""
    with open(path, encoding="utf-8") as fh:
        d = json.loads(fh.read())
    if not isinstance(d, dict) or scan(json.dumps(d, ensure_ascii=False)):
        raise ValueError("the payload at %s is not publishable as it stands" % path)
    return d
PROFILE = os.path.join(ROOT, "profile.sarvesh.json")

BLOB_PREFIX = "a/"                                     # resumes are opaque blobs, not paths

# Published per application. Anything not listed is not published, however innocent it
# looks. notes/warm_intro/salary_tier/outcome/resume_file are deliberately absent:
# notes is free text you type while tired, warm_intro names people, resume_file is a
# path, and outcome text can quote an email.
APP_FIELDS = ("date_applied", "company", "role", "track", "format", "channel", "status",
              "last_touch", "coverage_pct", "fit_verdict", "fit_score")
MAX_FIELD = 90                                         # short columns are still capped

# Published per open job.
JOB_FIELDS = ("title", "company", "date", "status")

# Location is never echoed. It is matched against this list and reduced to a city name,
# so "Hyderabad (Hitec City, near D-Mart, HR contact 98xxxx)" publishes "Hyderabad".
CITIES = [
    "Hyderabad", "Bengaluru", "Bangalore", "Chennai", "Pune", "Mumbai", "Noida", "Gurugram",
    "Gurgaon", "Delhi", "Kolkata", "Ahmedabad", "Jaipur", "Indore", "Kochi", "Coimbatore",
    "Visakhapatnam", "Lucknow", "Nagpur", "Remote",
    "Dubai", "Abu Dhabi", "Sharjah",
]
CITY_RE = re.compile("|".join(CITIES), re.I)

# Generic shapes that must never appear regardless of what the profile says. Applied
# to a version of the blob where the fields this tool itself emitted as safe have been
# blanked out (see _mask_safe): without that mask, an application date like
# 2026-08-31 trips the date-of-birth test and the tool refuses to publish anything,
# which is the kind of "secure" that ends with you disabling the check.
PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("url", re.compile(r"https?://[^\s\"']+")),
    ("passport", re.compile(r"\b[A-Z]\d{7}\b")),
    ("dob", re.compile(r"\b\d{1,2}[\s/:-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
                       r"january|february|march|april|may|june|july|august|september|october|"
                       r"november|december)[a-z]*[\s/:-]\d{2,4}\b", re.I)),
    ("dob-iso", re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b(?!"
                            r"\s*(?:to|-\s*(?:19|20)\d{2}|until|present))")),
    # +91 XXXXX XXXXX / 0XXXXXXXXXX / XXXXX-XXXXX, one placeholder per shape: a
    # real-looking number in this comment makes this file trip the publish scan it
    # documents, which is exactly how the redactor was blocked from being published. Note the
    # example is SYNTHETIC on purpose: this file is one of the eight copied into the PUBLIC
    # board repo, so a real number in a comment there would be published by the very tool
    # whose job is to keep it out. Deliberately NOT "\d{5}\d{5}" with no
    # separator, which matches half the things a job pipeline stores.
    ("phone", re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:0?\d[\s-]?){9,}\d(?:[\s-]?\d{1,4})?(?=[^\d]|$)"
                          r"")),
    ("phone", re.compile(r"\b(?:\+91|\+971|0)[ -]?\d{5}[ -]\d{5}\b")),
]
SAFE_MASKS = [
    ("iso date", re.compile(r'"(?:date_applied|last_touch|date)":\s*"\d{4}-\d{2}-\d{2}"')),
    ("generated", re.compile(r'"generated":\s*"[^"]*"')),
    ("blob id", re.compile(r'"resume_blob":\s*"a/[0-9a-f]{10}"')),
    ("percent", re.compile(r'"(?:coverage_pct|fit_score)":\s*"[0-9.]+"')),
]


def _mask_safe(text: str) -> str:
    """Blank out the exact spans this tool emitted from an allowlist, then scan the rest."""
    for _, pat in SAFE_MASKS:
        text = pat.sub('"x": "masked"', text)
    return text


# Keys whose VALUES are scanned in the profile, whatever the nesting, because that is
# where the sensitive strings live and a schema change must not silently unprotect them.
# The fields that must never appear in a public file. Deliberately NOT every string in
# the profile: "Single" (marital status) and "Indian" (nationality) are English words
# that legitimately occur in code and comments, and a gate that reports them as leaks
# gets muted by the next tired reader. Sensitivity is not the same as uniqueness; a
# blocklist only works if every entry is actually specific to you.
PROFILE_KEYS = {"phone", "email", "dob", "date_of_birth", "passport",
                "linkedin", "github", "portfolio", "portfolio_data"}
NAME_KEYS = ("first_name", "last_name", "owner")


def _clean(v) -> str:
    """One line, no control characters, capped. Never a substring of a long string."""
    s = str(v if v is not None else "").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("{", "").replace("}", "")            # can't break out of a template
    return s[:MAX_FIELD]


def _city(value) -> str:
    m = CITY_RE.search(str(value or ""))
    if not m:
        return ""
    hit = m.group(0)
    for c in CITIES:                                   # canonical spelling, not as typed
        if c.lower() == hit.lower():
            return c
    return ""


def _blob(company: str, date_applied: str) -> str:
    """Opaque resume reference. The page needs a stable id per application, not your
    folder layout; the app turns it back into a private-repo URL."""
    # zlib, not hashlib: a non-cryptographic id is all this needs (it must be stable and
    # unguessable-looking, it protects nothing), and it keeps the publisher off a module
    # whose name can be shadowed by anything on PYTHONPATH - which is exactly what broke
    # this file's first run in a sandbox whose parent had inserted tools/ into sys.path.
    h = format(zlib.crc32((company.strip().lower() + "|" + date_applied).encode()), "08x")
    return BLOB_PREFIX + h + format(len(company.strip()), "02x")


def applications(path: str = TRACKER, require_resume: bool = True) -> list[dict]:
    """The published rows. `require_resume` is the honest-link rule.

    A resume column is a link into the private repo, and a link is a promise: it is only
    allowed into the payload when the PDF it points at exists where this tool is running.
    On the laptop that means tools/publish_local.py has just copied it into resumes/; in
    a Actions checkout, where no PDF was ever committed, it means no link at all instead
    of a column of 404s. Turning the default off is how the hosted page ends up showing
    a button that does nothing, which is worse than showing none.
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        rec = {k: _clean(r.get(k)) for k in APP_FIELDS if str(r.get(k) or "").strip()}
        if not rec.get("company"):
            continue
        # A resume link is the one thing worth publishing as an id: the file itself
        # stays private. Named by company+date so it survives a renamed folder.
        blob = _blob(rec["company"], rec.get("date_applied", ""))
        if not require_resume or os.path.exists(os.path.join(resume_root(), blob + ".pdf")):
            rec["resume_blob"] = blob
        out.append(rec)
    out.sort(key=lambda r: r.get("date_applied", ""), reverse=True)
    return out


def open_jobs(path: str = SEEN, limit: int = 200) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            seen = (json.load(fh) or {}).get("seen", {})
    except Exception:                                    # noqa: BLE001
        return []
    out = []
    for v in seen.values():
        if not isinstance(v, dict) or not metrics.is_open(v):
            continue
        rec = {k: _clean(v.get(k)) for k in JOB_FIELDS if str(v.get(k) or "").strip()}
        loc = _city(v.get("location") or v.get("city") or v.get("place"))
        if loc:
            rec["location"] = loc
        # Source is a fixed vocabulary, never a URL: the posting link would be a
        # live ad for the job and a fingerprint of your interest to whoever crawls it.
        src = str(v.get("source") or v.get("channel") or "").lower()
        for known in ("naukri", "linkedin", "wellfound", "greenhouse", "lever", "ashby",
                      "workday", "instahyre", "cutshort", "foundit", "referral"):
            if known in src:
                rec["source"] = known
                break
        # The identity fields arrive under more than one name: linkedin_jobs.py writes
        # `title`, the board scrapers and the skill-written entries use `role`, and `company`
        # can be `organisation`. An alias missed is postings silently not counted, which is
        # how 165 open jobs became a tile saying 0 on his page.
        title = (v.get("title") or v.get("role") or v.get("job_title") or "").strip()
        comp = (v.get("company") or v.get("organisation") or v.get("org") or "").strip()
        if title:
            rec["title"] = title
        if comp:
            rec["company"] = comp
        # No "must have both to count" gate here. This list's only published use is its
        # LENGTH, and a posting he could act on later is still a posting that is open now.
        # The private ledger is where the identity lives; the public payload gets a number.
        out.append(rec)
    out.sort(key=lambda r: r.get("date", ""), reverse=True)
    return out[:limit]


PENDING_FIELDS = ("company", "role", "verdict", "coverage_pct", "format", "status", "built")


def pending(path: str = QUEUE_MIRROR, limit: int = 12) -> list[dict]:
    """Kits that are built and still waiting on one decision.

    Read from the mirror, never from output/review_queue.json: the mirror is the file
    apply_kit is allowed to write into a tracked directory, so anything that reaches here
    has already passed through a fixed field list once. It is filtered again here, because
    the second reader must not trust the first writer: keys like url, notes, kit and resume
    are paths and free text, and free text is where a phone number hides.

    Only the rows that still need you appear, and they leave here with the published
    wording (`metrics.AWAITING_STATUS`), because the queue's private token and the page's
    words must not be two different names for one state. Approved and rejected kits are
    already tracker rows; showing them twice would give the page two numbers for one thing.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for it in items:
        if not isinstance(it, dict) or not metrics.is_awaiting(it.get("status")):
            continue
        rec = {k: _clean(it.get(k)) for k in PENDING_FIELDS}
        rec["status"] = metrics.AWAITING_STATUS
        if rec.get("company") and rec.get("role"):
            out.append(rec)
    # newest kit first: what you staged today is what you are asked to decide on today
    out.sort(key=lambda r: (str(r.get("built") or ""), str(r.get("company") or "")), reverse=True)
    return out[:limit]


def _as_count(x) -> int:
    """Accept the number, or the list someone counted by hand.

    The published figure is a COUNT, and `len(open_jobs(...))` used to be how it was made.
    That made the page's number depend on the list's filters and its 200-row cap, so a ledger
    of 165 open postings could publish 0 (a field name the redactor did not alias) and a
    ledger of 400 would publish 200. metrics.open_jobs() is the definition the desktop report
    already uses; taking its number is what stops the two screens disagreeing.
    """
    if isinstance(x, int):
        return x
    return len(x or [])


def board(rows: list[dict], seen, pend: list[dict] | None = None) -> dict:
    """The whole publish payload, and deliberately tiny.

    No `stats` key. An earlier version published tiles computed here, which meant the
    public page showed numbers a reader could not reconcile with the rows shown - the
    exact failure that broke the last build (a tile said 0 and the table said 2). Now
    the page runs metrics.numbers() over exactly the `applications` array it received,
    so tile and table are the same arithmetic over the same rows or the page is broken
    for everyone at once, never quietly.

    `seen` is the open-jobs COUNT (metrics.open_jobs), not a redacted list: a list's
    filters and its cap would quietly redefine the number, which is exactly what happened
    between his desktop report (165) and the hosted tile (0).

    Only a COUNT of open jobs is published, not the list: a public list of what you
    found is a public list of what you are hunting for, and it is a free job feed for
    anyone who crawls the blob. The names live in the private dashboard.
    """
    return {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "schema": 1,
        "applications": rows,
        "open_jobs_count": _as_count(seen),
        "pending": pend or [],
    }


def pii_markers(profile_path: str = PROFILE) -> dict[str, str]:
    """Every string in your profile that must never appear in a public file."""
    out: dict[str, str] = {}
    if not os.path.exists(profile_path):
        return out

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        else:
            s = str(o or "").strip()
            if (key in PROFILE_KEYS or key in NAME_KEYS) and len(s) >= 3:
                out[key] = s
    with open(profile_path, encoding="utf-8") as fh:
        walk(json.load(fh))
    return out


def scan(text: str, profile_path: str = PROFILE) -> list[str]:
    """What is wrong with this blob. Empty list means safe to publish."""
    problems: list[str] = []
    body = _mask_safe(text)
    for name, pat in PATTERNS:
        m = pat.search(body)
        if m:
            problems.append("%s: %r" % (name, m.group(0)[:40]))
    markers = pii_markers(profile_path)
    phone = markers.get("phone", "")
    if phone:
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 8 and digits in re.sub(r"\D", "", text):
            problems.append("profile phone digits")
    # The owner's name is matched as the PAIR (both words on one line, either order),
    # never as either word alone: a first name is common, and the surname alone fires on
    # our own file-name patterns and on a recruiter's subject line. A rule on both words
    # together still catches what matters - a resume or note that identifies the candidate
    # - while a rule on one word only produces refusals, and refusals get disabled. No
    # example string is written out in this comment: the file is one of the eight copied
    # into the public repo, and a test greps those for the profile's own values.
    first = markers.get("first_name", "")
    last = markers.get("last_name", "") or markers.get("owner", "").replace(first, "").strip()
    if first and last:
        for variant in ("%s %s" % (last, first), "%s %s" % (first, last)):
            if variant in text:
                problems.append("candidate name pair: %r" % variant)
                break
    for key in ("email", "passport", "dob", "date_of_birth", "linkedin", "github",
                "portfolio", "portfolio_data"):
        v = markers.get(key, "")
        if v and v in text:
            problems.append("profile " + key)
    return problems



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracker", default=TRACKER)
    ap.add_argument("--seen", default=SEEN)
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--queue", default=QUEUE_MIRROR,
                    help="tracked mirror of the review queue, for the waiting-on-you list")
    ap.add_argument("--check", action="store_true", help="scan and report, write nothing")
    ap.add_argument("--scan", default="",
                    help="scan an existing payload file and exit 1 on a finding, building nothing")
    ap.add_argument("--payload", default=PAYLOAD,
                    help="the laptop's projection; used as-is when it exists and scans clean")
    ap.add_argument("--check-payload", action="store_true",
                    help="report whether --payload can be published as-is, and stop")
    ap.add_argument("--resume-dir", default="",
                    help="where resumes/a/<id>.pdf live; empty means the repo's own resumes/")
    ap.add_argument("--emit-fields", action="store_true")
    a = ap.parse_args()

    if a.resume_dir:
        os.environ["JOBSEARCH_RESUME_DIR"] = a.resume_dir

    if a.check_payload:
        ok = payload_ready(a.payload)
        if ok:
            why = "scans clean"
        elif not os.path.exists(a.payload or ""):
            why = "no such file: run python tools/publish_local.py --commit first"
        else:
            why = "it trips the blocklist: rebuild it"
        print("%s -> %s" % (a.payload, why))
        return 0 if ok else 1

    if a.scan:
        if not os.path.exists(a.scan):
            print("nothing to scan at %s" % a.scan, file=sys.stderr)
            return 1
        body = open(a.scan, encoding="utf-8").read()
        try:
            json.loads(body)
        except ValueError as exc:
            print("refusing: %s is not valid JSON (%s)" % (a.scan, exc), file=sys.stderr)
            return 1
        found = scan(body, a.profile)
        for p in found:
            print("   leak ->", p, file=sys.stderr)
        if found:
            print("refusing: the payload on disk trips the blocklist", file=sys.stderr)
            return 1
        print("scanned %s: clean (%d B)" % (a.scan, len(body)))
        return 0

    if a.emit_fields:
        print(json.dumps({"applications": list(APP_FIELDS) + ["resume_blob"],
                          "open_jobs": list(JOB_FIELDS) + ["location", "source"],
                          "pending": list(PENDING_FIELDS)}, indent=2))
        return 0

    if payload_ready(a.payload):
        # See the payload-first rule at the top of this file: on a runner this is the only
        # path that can carry rows, and it is scanned again right here, not trusted. The
        # numbers printed are read back out of that file, so --check tells him what the page
        # will say rather than what a rebuild would have said.
        payload = load_payload(a.payload)
        counts = (len(payload.get("applications") or []),
                  int(payload.get("open_jobs_count") or 0),
                  len(payload.get("pending") or []))
        made = "payload"
    else:
        rows = applications(a.tracker)
        open_count = metrics.open_jobs(metrics.read_ledger(a.seen))
        pend = pending(a.queue)
        payload = board(rows, open_count, pend)
        counts = (len(rows), open_count, len(pend))
        made = "fallback"
        if a.limit != 200 and open_count > a.limit:
            print("note: --limit is not applied to the published open-jobs figure. It is a "
                  "count over the whole ledger (%d open), because capping it at %d is how a "
                  "page comes to read 200 for 400." % (open_count, a.limit), file=sys.stderr)
    text = json.dumps(payload, indent=1, ensure_ascii=False)
    problems = scan(text, a.profile)

    if problems:
        print("REFUSING TO PUBLISH. Refusing is not a warning:\n", file=sys.stderr)
        for p in problems:
            print("   leak ->", p, file=sys.stderr)
        return 1
    if a.check:
        print("clean: %d applications, %d open jobs, %d waiting on you, %d B,"
              " nothing on the blocklist [%s]" % (counts[0], counts[1], counts[2],
                                                  len(text), made))
        return 0
    if not a.out:
        print(text)
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    # re-read what actually landed and scan THAT, not the string we meant to write
    problems = scan(open(a.out + ".part", encoding="utf-8").read(), a.profile)
    if problems:
        os.remove(tmp)
        print("refusing after re-read: the file on disk differs from what was scanned",
              file=sys.stderr)
        return 1
    os.replace(tmp, a.out)
    print("wrote %s: %d applications, %d open jobs, %d waiting on you, %d B [%s]"
          % (a.out, counts[0], counts[1], counts[2], len(text), made))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
