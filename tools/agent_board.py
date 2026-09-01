#!/usr/bin/env python3
"""The animated agent board, as self-contained HTML for a read-only page.

Why generated HTML and not streamlit widgets: Streamlit re-renders on interaction and
has no animation model, and a hosted page must not need a websocket tick to look
alive. So the motion is CSS keyframes, which run in the reader's browser forever
without any server involvement, and the DATA is re-read from the blob on every rerun.
Two consequences worth stating: the lanes and chips are real (derived from your
statuses), while the pulse and the slide-in are decoration on a timer. Nothing here
invents a job, a reply, or a submission - it moves the ones that exist.

Kept to no JS at all: the public page is an iframe-rich environment and every script
is another way for somebody else's content to run.
"""
from __future__ import annotations

import html
import re

LANES = [
    ("scout", "Scout", "SC", "reading public ATS endpoints + boards"),
    ("fit", "Fit Check", "FC", "scoring against your real experience"),
    ("tailor", "Tailor", "TL", "rewriting summary and skill lines per JD"),
    ("queue", "Needs You", "OK", "unapproved kits: run the line, on the laptop"),
    ("sent", "Submitted", "SN", "posted, receipt logged, PDF stored"),
]

# One place decides which lane a status sits in, so the board, the table and the tiles
# can never disagree about what "screening" means.
STATUS_LANE = {"applied": 4, "screening": 4, "interview": 4, "offer": 4, "hired": 4,
               "no-reply": 4, "rejected": 4, "closed": 4}

CSS = """
.ab{--bg:#0f1115;--panel:#171a21;--line:#262b36;--tx:#dfe4ee;--dim:#8b93a7;--blue:#4c8dff;
--green:#35d07f;--amber:#ffb547;--red:#ff5f6d;font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;
color:var(--tx);background:var(--bg);padding:14px;border-radius:12px}
.ab *{box-sizing:border-box}
.ab .grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
.ab .col{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:9px;
min-height:180px}
.ab .ch{display:flex;align-items:center;gap:7px;margin-bottom:3px}
.ab .av{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;font-size:11px;
font-weight:700;background:#1e2434;border:1px solid var(--line);color:var(--blue);flex:none;
animation:abpulse 1.9s ease-in-out infinite}
.ab .col:nth-child(2) .av{animation-delay:.25s}.ab .col:nth-child(3) .av{animation-delay:.5s}
.ab .col:nth-child(4) .av{animation-delay:.75s}.ab .col:nth-child(5) .av{animation-delay:1s}
@keyframes abpulse{0%,100%{box-shadow:0 0 0 0 rgba(76,141,255,0)}50%{box-shadow:0 0 0 4px rgba(76,141,255,.20)}}
.ab .nm{font-weight:650;font-size:12.5px}
.ab .ct{margin-left:auto;font-size:11px;color:var(--dim);background:#1e2330;padding:1px 7px;
border-radius:999px}
.ab .tk{color:var(--dim);font-size:10.5px;min-height:14px;margin-bottom:8px;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace;overflow:hidden;white-space:nowrap;
text-overflow:ellipsis}
.ab .card{background:#1c2130;border:1px solid #2b3140;border-radius:8px;padding:7px 8px;
margin-bottom:7px;animation:abslide 12s linear infinite;position:relative;overflow:hidden}
.ab .card:nth-child(odd){animation-delay:-3s}
.ab .card:nth-child(3n){animation-delay:-6s}
@keyframes abslide{0%{transform:translateY(6px);opacity:.35}6%,92%{transform:none;opacity:1}
100%{transform:translateY(-4px);opacity:.4}}
.ab h4{margin:0 0 2px;font-size:12px;font-weight:600;line-height:1.25}
.ab .m{font-size:10.5px;color:var(--dim)}
.ab .bar{height:3px;background:#2a3040;border-radius:3px;margin-top:6px}
.ab .bar i{display:block;height:100%;background:var(--blue);border-radius:3px}
.ab .tg{display:inline-block;font-size:10px;padding:1px 6px;border-radius:5px;margin-top:5px;
border:1px solid #2b3140;color:var(--dim)}
.ab .tg.g{color:var(--green);border-color:#1d3a2b;background:#132519}
.ab .tg.a{color:var(--amber);border-color:#3a3020;background:#241d13}
.ab .tg.r{color:var(--red);border-color:#3a1f24;background:#241316}
.ab .empty{font-size:11px;color:var(--dim);padding:14px 4px;text-align:center;opacity:.75}
.ab .cmd{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9.5px;color:var(--blue);
background:#131a2a;border:1px dashed #2b3a55;border-radius:6px;padding:3px 5px;margin-top:6px;
user-select:all;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.ab .cmd:hover{border-style:solid;border-color:var(--blue)}
.ab .foot{margin-top:11px;font-size:11px;color:var(--dim);display:flex;gap:14px;flex-wrap:wrap;
align-items:center}
.ab .live{display:inline-flex;gap:6px;align-items:center;color:var(--green);font-size:11px}
.ab .live b{width:7px;height:7px;border-radius:50%;background:var(--green);
animation:abdot 1.6s infinite}
@keyframes abdot{0%,100%{opacity:1}50%{opacity:.3}}
.ab .still .card,.ab .still .av,.ab .still .live b{animation:none!important}
.ab .still .card{opacity:1;transform:none}
@media(max-width:900px){.ab .grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.ab .grid{grid-template-columns:1fr}}
"""


# How long a published payload counts as current. The publisher runs four times a day
# (07:00, 13:00, 19:00, 01:00 IST), so one day is the longest gap that is still the
# schedule rather than a fault - and it is a threshold about DATA, not a claim that any
# agent is alive.
FRESH_HOURS = 24.0


def _e(s) -> str:
    """Everything here is your data (or a recruiter's), interpolated into HTML on a page
    that is public. Escaping at the one place that builds the markup, not in the caller."""
    return html.escape(str(s if s is not None else ""), quote=True)


def _pct(v) -> int:
    m = re.match(r"^\s*(\d{1,3})(?:\.\d+)?", str(v or ""))
    return max(0, min(100, int(m.group(1)))) if m else 0


RESUME_DIR_NAME = "resumes"


def approve_cmd(company: str) -> str:
    """The one line that clears a card. Built here so the board, the caption and the demo
    cannot each invent their own spelling of a command that submits nothing until you run
    it: a wrong flag, copied from a page, is a wasted evening.

    It runs publish_local, not apply_kit, because deciding on the page and publishing are
    one motion: the row must reach the tracker, the queue mirror and the opaque PDF copy in
    a single command, or the card is still sitting there tomorrow. --resume-dir is spelled
    out even though it is the default, so a reader who moved the folder sees the knob. The
    backslash is chr(92) because in an ordinary literal \a is a bell, and a page cannot
    show a bell: it prints as nothing at all.
    """
    return ('python tools' + chr(92) + 'publish_local.py --approve "%s" --resume-dir '
            + RESUME_DIR_NAME) % str(company).strip()


def render(apps: list[dict], motion: bool = True, open_jobs: int | None = None,
           pending: list[dict] | None = None, generated: str = "",
           age_hours: float | None = None) -> str:
    """apps = the redacted application rows, pending = the redacted waiting list.

    Both come out of tools/redact_public.py, so every field here has already passed the
    allowlist and the blocklist scan. Nothing else is shown, and nothing here is
    clickable on purpose: a page with no login cannot accept a decision, only display one.
    """
    buckets: list[list[dict]] = [[], [], [], [], []]
    for r in apps:
        st = (r.get("status") or "").strip().lower()
        if st in ("", "queued", "ready"):
            buckets[3].append(r)
        elif st == "tailored":
            buckets[2].append(r)
        else:
            buckets[STATUS_LANE.get(st, 4)].append(r)

    # A kit waiting on you is a real pending item; a tracker row already in this lane is
    # the same decision seen from the other side. Show each company once.
    waiting = [dict(p, _pending=True) for p in (pending or []) if isinstance(p, dict)]
    in_lane = {str(r.get("company") or "").strip().lower() for r in buckets[3]}
    waiting = [p for p in waiting if str(p.get("company") or "").strip().lower() not in in_lane]

    cols = []
    for i, (key, name, short, task) in enumerate(LANES):
        mine = buckets[i] + waiting if i == 3 else buckets[i]
        cards = []
        for r in mine[:4]:
            cov = _pct(r.get("coverage_pct"))
            verdict = (r.get("fit_verdict") or "").strip().upper()
            tag = ""
            if verdict == "BULLSEYE":
                tag = '<span class="tg g">bullseye</span>'
            elif verdict in ("MIXED", "NOT SCORED"):
                tag = '<span class="tg a">%s</span>' % _e(verdict.lower())
            elif verdict in ("SKIP", "HARD SKIP"):
                tag = '<span class="tg r">below floor, skipped</span>'
            pct = ('<div class="bar"><i style="width:%d%%"></i></div>' % cov) if cov else ""
            cmd = ('<div class="cmd">%s</div>' % _e(approve_cmd(r.get("company") or ""))
                   if r.get("_pending") else "")
            cards.append('<div class="card"><h4>%s</h4><div class="m">%s</div>%s%s%s</div>'
                         % (_e(r.get("role") or "role"), _e(r.get("company") or ""), pct, tag, cmd))
        if len(mine) > 4:
            cards.append('<div class="m" style="padding:0 4px">+ %d more</div>' % (len(mine) - 4))
        if not mine:
            note = ("nothing pending: every kit built has been decided"
                    if i == 3 else "empty")
            cards = ['<div class="empty">%s</div>' % _e(note)]
        cols.append('<div class="col"><div class="ch"><div class="av">%s</div><div class="nm">%s</div>'
                    '<div class="ct">%d</div></div><div class="tk">%s</div>%s</div>'
                    % (_e(short), _e(name), len(mine), _e(task), "".join(cards)))

    total = len(apps)
    sent = len(buckets[4])
    # The pulse used to announce that the agents were running, unconditionally, on a page whose input is a
    # single JSON file. Nothing on this page can see a process, and at 03:00 with the schedule firing at
    # 07:00 it was a green light for a machine that was asleep. So the dot now reports the one
    # freshness fact the payload does prove - how long ago it was published - and is a dot only
    # while that is recent. The age is computed by the caller, because only the caller knows the
    # publisher's timezone; this function never compares clocks.
    if generated and age_hours is not None and age_hours <= FRESH_HOURS:
        lead = '<span class="live"><b></b>payload %s</span>' % _e(generated)
    elif generated and age_hours is not None:
        lead = '<span>payload %s (%d h ago: the publish is late, not the page)</span>' \
               % (_e(generated), int(age_hours))
    elif generated:
        lead = '<span>payload %s (age unknown)</span>' % _e(generated)
    else:
        lead = '<span>no payload timestamp</span>'
    foot = [lead,
            '<span>%d applications on record</span>' % total,
            '<span>%d submitted</span>' % sent]
    if open_jobs is not None:
        foot.append('<span>%d open jobs found (list stays private)</span>' % open_jobs)
    return ('<style>%s</style><div class="ab%s"><div class="grid">%s</div>'
            '<div class="foot">%s</div></div>'
            % (CSS, "" if motion else " still", "".join(cols), "".join(foot)))


if __name__ == "__main__":
    demo = [{"company": "Optum", "role": "Sr AI Engineer", "status": "applied",
             "coverage_pct": "91.0", "fit_verdict": "BULLSEYE"},
            {"company": "Tata 1mg", "role": "Data Analyst", "status": "",
             "coverage_pct": "78", "fit_verdict": "MIXED"}]
    wait = [{"company": "Infosys", "role": "GenAI Engineer", "coverage_pct": "64",
             "verdict": "MIXED", "status": "awaiting approval"}]
    print(render(demo, open_jobs=93, pending=wait))
