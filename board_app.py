#!/usr/bin/env python3
"""app.py for the PUBLIC repo, developed here so it is tested with everything else.

What this page is: a read-only window onto the job agent, hosted on streamlit.app,
whose entire data source is one redacted JSON that GitHub Actions publishes after
tools/redact_public.py has refused-or-approved it. There is no secret here, no token,
no database, no private repo fetch. If this file is ever copied into the private repo
and starts reading profile.sarvesh.json, that is the bug.

Why the tiles are computed here instead of read from the blob: so a tile can only ever
show a number that the rows below it add up to. The published blob has no stats key.
metrics.py is IMPORTED (same file, same rules as the terminal and the laptop view), not
copied, so there is still exactly one definition of "applied this week" in the system.

  streamlit run app.py                      # the real thing
  python app.py --smoke                     # headless: loads the sample, prints the tiles
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import streamlit as st                                        # noqa: E402

from tools import metrics                                   # noqa: E402
from tools import agent_board                               # noqa: E402

# --- where the public page reads from. A path, or a URL. No credentials anywhere. ----
# A previous build baked one GitHub login into these two defaults. It is correct for exactly one
# person and silently wrong for everyone else: on streamlit.app the fetch 404s, load() falls back
# to the sample, and the page shows SAMPLE DATA forever while everything on GitHub looks healthy.
# So the slug comes from the config.json that is deployed INTO the public repo beside this app,
# which the installer writes, and an env var still wins for a local run.
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "public", "board.sample.json")
FETCH_SECONDS = 12


def local_config(here: str = "") -> dict:
    """The config.json sitting next to this app in the deployed repo. {} when absent."""
    p = os.path.join(here or HERE, "public", "config.json")
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.loads(fh.read())
    except Exception:                                        # noqa: BLE001
        return {}
    return d if isinstance(d, dict) else {}


CFG_LOCAL = local_config()
_SLUG = (os.environ.get("JOBSEARCH_BOARD_REPO") or CFG_LOCAL.get("board_repo") or "").strip("/")
_RAW = ("https://raw.githubusercontent.com/%s/main/public/" % _SLUG) if _SLUG else ""
BLOB_URL = os.environ.get("JOBSEARCH_BLOB") or (_RAW + "board.json" if _RAW else "")
CONFIG_URL = os.environ.get("JOBSEARCH_CONFIG") or (_RAW + "config.json" if _RAW else "")


def _fetch(url: str) -> str:
    """Plain read. A file path works too, which is how the tests use it."""
    if url and not url.startswith("http"):
        with open(url, encoding="utf-8") as fh:
            return fh.read()
    req = urllib.request.Request(url, headers={"User-Agent": "job-board/1.0"})
    with urllib.request.urlopen(req, timeout=FETCH_SECONDS) as r:   # noqa: S310 (https only)
        return r.read().decode("utf-8")


@st.cache_data(ttl=300, show_spinner=False)
def _read_url(url: str) -> str:
    """One read per source per session, not per widget. The Refresh button clears this.

    ttl=300 is bounded staleness, not a guess at a schedule: a laptop publish reaches the
    page in about a minute, and a five-minute cache means the page cannot show a payload it
    has already replaced, nor hammer raw.githubusercontent.com on every rerun. The URL is an
    argument, so the cache key is the source.
    """
    return _fetch(url)


def load():
    """(rows, open_jobs_count, generated, note, pending). Never raises: a public page
    whose blob is unreachable must say what it fell back to, not show a stack trace."""
    if not BLOB_URL:
        if not os.path.exists(SAMPLE):
            return [], 0, "", "no board.json on this host and no public/config.json board_repo " \
                              "to say where to look", []
        rows, open_jobs, generated, pend = _parse(open(SAMPLE, encoding="utf-8").read())
        return (rows, open_jobs, generated,
                "NOT LIVE: public/config.json in this repo has no board_repo, so this page has no "
                "address to read from. Run the installer again; step 5 writes it.", pend)
    try:
        raw = _read_url(BLOB_URL)
    except Exception as e:                                 # noqa: BLE001
        # There used to be a fallback here: read public/board.sample.json and label the page
        # SAMPLE DATA. On 2026-09-02 that fallback cost more than it saved. His installer run
        # force-pushed the board repo and deleted public/board.json, so the address the page was
        # configured to read returned 404, and the page answered by displaying nine companies he
        # never applied to, in a pipeline he never ran, with a banner he had to notice. A reader
        # who glances at that learns that Optum and TCS have answers pending, which is a lie with
        # a warning attached. So: an address that is configured and fails is reported as such,
        # with zero rows, and the sample is only ever shown when there is no address at all.
        return ([], 0, "", "the published board could not be read (%s: %s). This page shows no "
                           "sample data, because a fake row you might believe is worse than an "
                           "empty one. On the laptop run tools\\publish_local.py --commit; if the "
                           "payload exists in the private repo this heals itself in a minute."
                % (type(e).__name__, _short(BLOB_URL)), [])
    rows, open_jobs, generated, pend = _parse(raw)
    return rows, open_jobs, generated, "", pend


def _short(url: str) -> str:
    """The address without the part that reads like a credential-free but pointless tail."""
    u = str(url or "")
    return u if len(u) <= 96 else "..." + u[-93:]


def _parse(raw: str):
    d = json.loads(raw)
    return ((d.get("applications") or []),
            int(d.get("open_jobs_count") or 0),
            str(d.get("generated") or ""),
            # absent in a blob published before the queue travelled: an empty waiting
            # list, which is the honest reading, not a zero invented here
            d.get("pending") or [])


def load_config() -> dict:
    """The deployed copy first, the live one on top of it if it answers.

    They are the same file in normal operation, so a 404 here is not worth a warning: the
    copy beside the app is already what the installer wrote.
    """
    cfg = dict(CFG_LOCAL)
    cfg.setdefault("resume_path", "resumes")
    cfg.setdefault("private_repo", "")
    if CONFIG_URL:
        try:
            cfg.update(json.loads(_read_url(CONFIG_URL)))
        except Exception:                                    # noqa: BLE001
            pass
    return cfg


COLS = ["date_applied", "company", "role", "track", "format", "status",
        "last_touch", "coverage_pct", "fit_verdict", "fit_score"]


BLOB_RE = re.compile(r"^a/[0-9a-f]{10}$")
SEG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _path_ok(value: str, parts: int = 0) -> bool:
    """Every slash-separated bit is a real name, and no bit walks.

    A character class alone is not enough here and this is exactly where that showed: dots
    are legal in a repo name, so [A-Za-z0-9_.-]+ happily accepts "..", and a link built
    from "resumes/../private" is a link out of the folder the whole design lives in. So
    each segment is matched AND checked against the two that mean "up" and "here".
    parts, when given, pins the count (a repo slug is owner/name and nothing else).
    """
    segs = [s for s in str(value or "").split("/") if s]
    if not segs or (parts and len(segs) != parts):
        return False
    return all(SEG_RE.match(s) and s not in (".", "..") for s in segs)


def resume_url(a: dict, cfg: dict) -> str:
    """The one place a resume link is built. Blob id in, github.com out, nothing else.

    All three parts are matched, not sanitised, because the two that are not the blob come
    from public/config.json in a repo anyone with a fork button could propose a change to.
    An owner of "evil.example/#" would otherwise produce a working link to somewhere else
    with your file name on the end of it, which is the sort of mistake that only shows up
    in a link you clicked. Bad part, no link: losing a button is a small loss and
    following one you did not mean to publish is not.
    """
    repo = (cfg.get("private_repo") or "").strip("/")
    path = (cfg.get("resume_path") or "resumes").strip("/")
    blob = str(a.get("resume_blob") or "").strip("/")
    if not (_path_ok(repo, 2) and _path_ok(path) and BLOB_RE.match(blob)):
        return ""
    return "https://github.com/%s/blob/main/%s/%s.pdf" % (repo, path, blob)


def build_rows(shown: list, cfg: dict) -> list:
    """The ledger: published fields plus the exact PDF that went out, as its own column.

    Column set is pinned by COLS so the smoke check and the page cannot drift apart.
    """
    out = []
    for a in shown:
        r = {k: a.get(k, "") for k in COLS}
        r["resume sent"] = resume_url(a, cfg)
        out.append(r)
    return out


def table_config() -> dict:
    """Kept in a function so --smoke executes it: a wrong kwarg here would otherwise only
    ever surface as a blank page in the browser, which is how a UI bug hides."""
    return {"resume sent": st.column_config.LinkColumn(
        "resume sent",
        help="Links into the PRIVATE repo, so they open for you and 404 for anyone else. "
             "Blank means no PDF was published for that row.")}


def _age_hours(generated: str):
    """Hours between the payload's own stamp and now, in the publisher's clock.

    The stamp is IST wall clock (tools/redact_public.py writes `%Y-%m-%d %H:%M IST`) and the page
    runs in a container whose clock is UTC, so `datetime.now()` here would call a two-minute-old
    payload five and a half hours old, then two hours older than that every day. So the comparison
    is made in IST and the container's timezone never enters it. Unparseable stamp returns None,
    which the footer reads as "age unknown" instead of inventing an age.
    """
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    try:
        then = dt.datetime.strptime(generated.strip(), "%Y-%m-%d %H:%M IST")
    except (ValueError, AttributeError):
        return None
    return (dt.datetime.now(IST) - then.replace(tzinfo=IST)).total_seconds() / 3600.0


def tiles(apps, pending=None):
    """metrics.numbers() over exactly these rows, with an empty ledger: the open-jobs
    tile is filled in by the caller from the published count, and every applications
    metric is the shared function's answer. Same rules as tools/tracker.py status.

    `pending` is the published waiting list, passed through and never recounted here.
    """
    return metrics.numbers(apps, {}, pending=pending)


def main() -> None:
    st.set_page_config(page_title="Job pipeline", layout="wide", page_icon=None)
    apps, open_jobs, generated, note, pend = load()
    cfg = load_config()

    with st.sidebar:
        st.header("Board")
        motion = st.toggle("agent motion", value=True,
                           help="CSS only. Off stops the pulse and the slide-in; nothing else changes.")
        statuses = sorted({(a.get("status") or "queued") for a in apps})
        pick = st.multiselect("status", statuses, default=statuses)
        q = st.text_input("contains").lower().strip()
        st.divider()
        st.caption("Read-only, on purpose: this page has no login, so it cannot take an "
                   "approval from you. Each Needs You card carries the exact line to run "
                   "on the laptop. If the numbers below look wrong, hit Rerun first, then "
                   "read the age line.")
        if st.button("Refresh from the published blob", width="stretch"):
            st.cache_data.clear()
            st.rerun()

    st.title("Job pipeline")
    age = ""
    if generated:
        age = "published %s" % generated
    if note:
        st.warning(note)
    st.caption(age or "no publish timestamp in the blob (Actions has not run yet, or the blob "
                      "is from before the timestamp field existed)")

    shown = [a for a in apps
             if (not pick or (a.get("status") or "queued") in pick)
             and (not q or q in json.dumps(a).lower())]

    st.markdown(agent_board.render(shown, motion=motion, open_jobs=open_jobs, pending=pend,
                                   generated=generated, age_hours=_age_hours(generated)),
                unsafe_allow_html=True)

    sm = tiles(shown, pend)
    sm["open jobs found"] = open_jobs
    c = st.columns(7)
    for i, (label, key) in enumerate([("applications", "tracked applications"),
                                      ("this week", "applied this week"),
                                      ("awaiting reply 10d+", "awaiting reply 10d+"),
                                      ("needs your approval", "awaiting your approval"),
                                      ("open jobs found", "open jobs found"),
                                      ("replies received", "replies received"),
                                      ("response rate", "response rate")]):
        c[i].metric(label, sm[key])

    st.subheader("Applications (%d)" % len(shown))
    # Three different ways this list can be empty, and the page used to print ONE sentence for
    # all three. "Nothing published yet" on a payload published two minutes ago is a false
    # diagnosis on the only screen he looks at, which is the same class of bug as an installer
    # calling a `fetch first` rejection a read-only token. So each branch says only what it can
    # prove, and the one thing it cannot prove is handed back as a command to run.
    if not shown and apps:
        st.info("Your filters hid all %d published rows. The status list and the contains box "
                "are the only filters on this page; clear them and the list comes back. Nothing "
                "was unpublished." % len(apps))
    elif not shown and not note and generated:
        st.info("Published at %s, and that payload holds zero rows. The page is working, so the "
                "open question is what the tracker had to publish. On the laptop run "
                "tools\\publish_local.py --report. If it also says 0 rows, nothing is approved "
                "yet and that is the truth rather than a fault. If it says more, the last publish "
                "predates those rows: run the same line with --commit." % generated)
    elif not shown:
        st.info("Nothing published yet. This page only ever shows what the redactor let "
                "through: company, role, status, dates, coverage. Your resumes, notes, "
                "and contact details stay in the private repo.")
    else:
        st.dataframe(build_rows(shown, cfg), width="stretch", hide_index=True,
                     column_config=table_config())
        if not (cfg.get("private_repo") or "").strip("/"):
            st.info("No resume links because this repo's public/config.json has an empty "
                    "private_repo. The installer fills it in; nothing here guesses a repo name.")
        st.caption("Posting links stay on the desktop dashboard on purpose: this page has no "
                   "login, so it carries only what tools/redact_public.py let through - "
                   "company, role, status, dates, coverage, and a resume link that only opens "
                   "for you.")

    st.caption("Last rendered %s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        apps, open_jobs, generated, note, pend = load()
        sm = tiles(apps, pend)
        sm["open jobs found"] = open_jobs        # same fill main() does, so the two cannot drift
        print(json.dumps({"rows": len(apps), "open_jobs": open_jobs, "pending": len(pend),
                          "generated": generated, "note": note,
                          "tiles": sm}, indent=2))
        h = agent_board.render(apps, motion=True, open_jobs=open_jobs, pending=pend,
                               generated=generated, age_hours=_age_hours(generated))
        assert "<style>" in h and "class=\"ab" in h
        cfg = load_config()
        trows = build_rows(apps, cfg)
        urls = [r["resume sent"] for r in trows if r["resume sent"]]
        hosts = sorted({u.split("/")[2] for u in urls})
        # Pinned so the page and the test cannot disagree about the ledger's shape, and so a
        # resume link can never turn into a fetch of somebody else's host.
        for r in trows:
            assert list(r) == COLS + ["resume sent"], list(r)
        assert all(u.startswith("https://github.com/") for u in urls), urls[:2]
        print(json.dumps({"board_html": len(h), "slug_seen": bool(_SLUG),
                          "blob_url": BLOB_URL, "config_url": CONFIG_URL,
                          "columns": COLS + ["resume sent"],
                          "resume_links": len(urls), "resume_hosts": hosts}, indent=2))
        raise SystemExit(0)
    main()
