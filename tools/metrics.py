#!/usr/bin/env python3
"""tools/metrics.py -- the ONE definition of every number shown to you.

Three views print the same tiles: `tracker.py status` in the terminal, the HTML report
you email yourself, and dashboard.py on your phone. For a while they were built from
three separate hand-written rules and disagreed, which is worse than being wrong in one
place, because you cannot tell which page to trust:

  * the HTML page counted a reply as status in (screening, interview, offer, hired),
    while tracker.py counted anything that was not applied or no-reply. A row closed
    or rejected moved one page and not the other.
  * "awaiting reply" was 10 days in the label and `--days 14` in the flag, and the
    page skipped rows whose last_touch was blank while the dashboard did not.
  * the HTML page carried its own status vocabulary (no_response, offer_declined,
    withdrawn, skipped) that tracker.py has never been able to write, so `closed` and
    `no-reply` were reported as unknown statuses on a file the tool itself produced.

Import these functions; do not restate them. `tests/test_pipeline.py` runs all three
views over the same rows and fails if a single number differs.
"""
from __future__ import annotations

import datetime as dt
import json
import os

# A reply arrived, of any kind. This is deliberately NOT "still in a positive stage":
# a rejection is a response, and a page that hides rejections inflates your response
# rate and tells you your cover letter is working when it is not.
REPLY_STATUSES = frozenset({"screening", "interview", "offer", "hired",
                            "rejected", "closed"})
# The only statuses that still owe YOU a nudge. `offer` is excluded: the ball is
# already in your court and a follow-up is a negotiation, not a chase.
CHASE_STATUSES = frozenset({"applied", "screening"})
# The stale window the tile label promises ("awaiting reply 10d+"). One constant, so a
# label and its arithmetic can never drift apart again.
STALE_DAYS = 10
WEEK_DAYS = 7
# A scrape older than this makes "open jobs found" a historic count, not an offer.
STALE_SCRAPE_DAYS = 2

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")


def parse_date(value):
    """Return a date, or None. Blank, malformed and non-strings are all None, so a
    missing last_touch is treated as 'as old as it can get' by the caller, never as a
    crash and never as 'today'."""
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def days_since(value, today=None):
    d = parse_date(value)
    if d is None:
        return None
    return (today or dt.date.today()).toordinal() - d.toordinal()


def has_reply(row) -> bool:
    return (row.get("status") or "").strip() in REPLY_STATUSES


def needs_chase(row, today=None) -> bool:
    if (row.get("status") or "").strip() not in CHASE_STATUSES:
        return False
    last = parse_date(row.get("last_touch"))
    if last is None:
        last = parse_date(row.get("date_applied"))
    if last is None:
        return False                      # no dates at all: nothing to age against
    n = days_since(last, today)
    return n is not None and n >= STALE_DAYS


def applied_this_week(rows, today=None) -> int:
    # `or 999` here would be a bug, not a shortcut: an application filed TODAY is
    # days_since == 0, which is falsy, so `0 or 999` reads as 999 and today's own
    # row drops out of the tile. Check for None explicitly.
    out = 0
    for r in rows:
        n = days_since(r.get("date_applied"), today)
        if n is not None and 0 <= n <= WEEK_DAYS:
            out += 1
    return out


def stale_rows(rows, today=None) -> list:
    return [r for r in rows if needs_chase(r, today)]


def replies(rows) -> int:
    return sum(1 for r in rows if has_reply(r))


def response_rate(rows) -> float:
    """Percentage, 0.0 for an empty tracker (an empty tracker is not 100% or a
    division by zero)."""
    return (100.0 * replies(rows) / len(rows)) if rows else 0.0


def read_ledger_checked(path):
    """(seen, problem) - the ledger, plus the reason it could not be read.

    `read_ledger` below has always swallowed a corrupt or locked file and returned {}, which
    is right for a view and deadly for a writer: for two days every screen said "0 open jobs"
    because the ledger was unreadable, and not one of them said that was why. This is the
    version that keeps the difference between an empty market and a broken file.
    """
    if not path:
        return {}, ""
    if not os.path.exists(path):
        return {}, ""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh) or {}
    except Exception as e:                                    # noqa: BLE001
        return {}, "%s: %s" % (type(e).__name__, e)
    if not isinstance(d, dict):
        return {}, "the ledger is a %s, not an object" % type(d).__name__
    seen = d.get("seen", {})
    if not isinstance(seen, dict):
        return {}, "`seen` is a %s, not an object" % type(seen).__name__
    return seen, ""


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def now_ist(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Now, in the one time zone this project prints, computed from an offset instead of from
    the machine's own clock.

    `dt.datetime.now().strftime("%H:%M IST")` is true on a laptop in Telangana and five and a
    half hours wrong on a GitHub runner, whose clock is UTC - and the label travels with the
    number, so the reader has no way to find out. That is the same defect as the page footer that
    printed container UTC next to a payload stamped IST and looked stale while being two minutes
    old. Anything that carries an `IST` suffix in this repo calls this instead.
    """
    return dt.datetime.now(IST).strftime(fmt)


def read_ledger(path):
    """The scrape ledger: {"seen": {key: {...}}}. Missing or corrupt reads as empty,
    never as a crash - a broken ledger means zero open jobs, not a dead dashboard.
    A writer must call read_ledger_checked() instead: for a write, "empty" is a lie that
    then gets saved to disk.
    """
    seen, _ = read_ledger_checked(path)
    return seen


def stamp_label(raw) -> str:
    """Render a scrape-run stamp with the zone it carries, or admit that it never said.

    A `runs/latest.json` can be written by a runner whose clock is UTC and read on a laptop in
    Telangana. Until -45 the value was written as `datetime.now().isoformat()` with no offset, so
    one laptop printed `last scrape: 2026-09-03T06:16` beside a ledger whose own mtime said 12:28:
    both correct, and the reader forced to guess which clock produced the first. Guessing is what
    turned a two-day-old archive into "no jobs out there" once already. So: new stamps carry an
    offset, and an old stamp is labelled as unknown instead of silently assumed to be local.
    """
    txt = str(raw or "").strip()
    if not txt:
        return ""
    try:
        d = dt.datetime.fromisoformat(txt)
    except ValueError:
        return txt[:16].replace("T", " ") + " (unreadable stamp)"
    if d.tzinfo is None:
        return d.strftime("%Y-%m-%d %H:%M") + " (no zone recorded when written)"
    return d.astimezone(IST).strftime("%Y-%m-%d %H:%M") + " IST"


def ledger_facts(path) -> dict:
    """The file behind the open-jobs number: where it is, how big, how fresh, how many,
    and what went wrong if it will not parse. Printed beside every count, so a zero can be
    checked against a byte count instead of argued about.
    """
    facts = {"path": os.path.abspath(path) if path else "", "exists": False, "bytes": 0,
             "mtime": "", "entries": 0, "open": 0, "parse_error": ""}
    if not path:
        facts["parse_error"] = "no ledger path given"
        return facts
    if not os.path.exists(path):
        facts["parse_error"] = "file absent"
        return facts
    st = os.stat(path)
    seen, problem = read_ledger_checked(path)
    facts.update({"exists": True, "bytes": st.st_size,
                  "mtime": dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                  "entries": len(seen), "open": open_jobs(seen), "parse_error": problem})
    return facts


def facts_line(path) -> str:
    """The one-line form of ledger_facts, for the tools that print sentences, not JSON.

    The path leads rather than trails, because the question it answers first is "which folder
    made this number" - two workspaces on one laptop, each with its own ledger, is what turned
    "0 open jobs" into a two-day investigation.
    """
    f = ledger_facts(path)
    where = f["path"] or "not set"
    if not f["exists"]:
        return "ledger: %s (%s)" % (where, f["parse_error"] or "absent")
    tail = ", UNPARSABLE: " + f["parse_error"] if f["parse_error"] else ""
    return "ledger: %s - %d entries, %d B, written %s%s" % (
        where, f["entries"], f["bytes"], f["mtime"], tail)


def is_open(entry) -> bool:
    """An entry still worth your time. Unknown shapes are treated as open so a ledger
    written by an older scraper cannot quietly zero the tile; anything explicitly
    expired, closed, filled or gone is not. The HTML page used to count only
    status == "new", which undercounted every job that got re-scraped and re-stamped."""
    status = (entry.get("status") or "").strip().lower()
    return status not in {"expired", "closed", "filled", "gone"}


def open_jobs(seen) -> int:
    return sum(1 for v in (seen or {}).values() if isinstance(v, dict) and is_open(v))


def closed_at_source(seen) -> int:
    return sum(1 for v in (seen or {}).values()
               if isinstance(v, dict) and not is_open(v))


# The one spelling of "the agent is done and you are not". The queue calls it REVIEW, the
# page and the tiles call it "awaiting approval", and the table shows a status column: two
# words for one state is how a tile ends up saying 0 over two visible rows, which is the
# exact failure this file exists to prevent. So the comparison is a function, and the
# published value is normalised on the way out.
AWAITING_STATUS = "awaiting approval"
_AWAITING = {AWAITING_STATUS.upper(), "REVIEW", "AWAITING-APPROVAL", "AWAITING_APPROVAL"}


def is_awaiting(value) -> bool:
    """Does this status still need you? Case, spacing and hyphen tolerant, nothing else."""
    return str(value or "").strip().upper().replace("-", " ").replace("_", " ") in _AWAITING


def awaiting_count(items) -> int:
    """How many of these kits are still waiting. Rows without a company or a role are not
    counted, because a card you cannot act on is not a task, it is noise."""
    return sum(1 for it in (items or []) if isinstance(it, dict) and is_awaiting(it.get("status"))
               and it.get("company") and it.get("role"))


def waiting(path=None) -> int:
    """Kits that are built and sitting unapproved. Counted from the mirror apply_kit
    writes into state/, never from output/review_queue.json, which is ignored because it
    holds free-text notes and posting URLs.

    This is the only place the number exists. The terminal, the HTML page and the hosted
    board all print it, and the hosted board prints the count the redactor published in
    board.json, so a stale blob cannot invent a task for you.
    """
    p = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "state", "review_queue.public.json")
    if not os.path.exists(p):
        return 0
    try:
        with open(p, encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
    except Exception:                                           # noqa: BLE001
        return 0
    return awaiting_count(items)


def numbers(rows, seen=None, latest_path=None, today=None, pending=None) -> dict:
    """The tiles, keyed exactly as the HTML page and the dashboard label them. Pass a
    `seen` dict; leave it None to have this read job_scraper/seen_jobs.json from the
    repo root, which is the same file the scraper writes."""
    if seen is None and latest_path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        seen = read_ledger(os.path.join(root, "job_scraper", "seen_jobs.json"))
    seen = seen or {}
    out = {
        "tracked applications": len(rows),
        "applied this week": applied_this_week(rows, today),
        f"awaiting reply {STALE_DAYS}d+": len(stale_rows(rows, today)),
        "open jobs found": open_jobs(seen),
        "closed at source": closed_at_source(seen),
        # None means "count it yourself from the queue mirror"; a list means "these are
        # the published pending rows, count them and do not go reading my disk".
        "awaiting your approval": (awaiting_count(pending) if pending is not None
                                    else waiting()),
        "replies received": replies(rows),
        "response rate": f"{response_rate(rows):.0f}%",
    }
    if latest_path and os.path.exists(latest_path):
        try:
            with open(latest_path, encoding="utf-8") as fh:
                out["last scrape"] = stamp_label(json.load(fh).get("generated", ""))
        except Exception:                                        # noqa: BLE001
            out["last scrape"] = ""
    return out
