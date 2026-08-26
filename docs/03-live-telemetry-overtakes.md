# 03 — Live Telemetry & Overtakes (Lane B)

Status: **B0's premise researched 2026-08-26; not approved, no implementation.** This is a
research memo toward B0/B1, not a build spec — nothing here authorizes writing code. Read
`welcome.md`, `00-roadmap.md`, and `01-data-pipeline.md` §9.5 first; this document supersedes
`01`'s §9.5 flag with a fuller investigation of the same question.

## 1. What this phase needs, and why it's harder than Lane A

Lane A pulls a fixed set of data once and computes a prediction with no deadline. Lane B needs
to know a car's position, speed, and gap to the car ahead **while the session is still
happening**, with enough lead time before a braking zone that a probability is worth anything.
That means a genuinely live data feed — sub-minute latency at worst, ideally sub-second — which
is a different problem than anything Lane A has had to solve, and the zero-budget constraint
applies to it just as it does everywhere else in this project.

## 2. The three candidate sources, and what's actually true about each (verified 2026-08-26)

### 2.1 FastF1's live module — confirmed unusable for this, not a maybe

`00-roadmap.md` locks "FastF1's free live module" for live data, and `01` §9.5 already flagged
this as needing re-examination. Re-verified live against FastF1's current docs: the
`SignalRClient` records the raw feed to a file as a session happens, but **parsing only happens
after the session ends**, via `Session.load()`. FastF1's own maintainers are explicit that this
is permanent, not a version limitation still being worked on. There is no code path in this
library that produces a probability *during* a session. This closes the question `01` §9.5 left
open: FastF1 does not satisfy Lane B's premise, full stop, not "not yet."

### 2.2 OpenF1 — free tier is historical-only, no partial live access

Checked directly against OpenF1's current docs (2026-08-26), because the natural next question
is whether *some* live access exists short of the full paid tier — e.g. plain REST polling
instead of a push subscription. It does not:

| | Free (Community) | Paid (Sponsor, €9.90/mo) |
|---|---|---|
| Access | REST, historical only | REST **and** MQTT **and** WebSocket, live |
| Rate limit | 3 req/s, 30 req/min | 6 req/s, 60 req/min, up to 10 concurrent streams |
| "Live" window | n/a — always historical | 30 min before session start to 30 min after end |

Outside the live window everything is free either way. Inside it, the free tier has **no**
access at all — not a slower version of live data, none. This is worse for a zero-budget
approach than `01`/`00-roadmap.md` currently imply: they treat the paid tier as "faster/more
concurrent," but the real gap is binary (any live access vs. none), which matters for how the
owner should weigh approving it.

### 2.3 The feed underneath both of the above: a direct, unofficial connection

Both FastF1 and OpenF1 ultimately source from the same place: F1's own live timing feed, served
over SignalR at `livetiming.formula1.com`. It is not authenticated and not access-controlled by
FastF1 or OpenF1 specifically — several independent hobbyist projects connect to it directly and
parse it live, which FastF1 deliberately chooses not to do:

- [`Troftu/F1-SignalR`](https://github.com/Troftu/F1-SignalR) — a minimal subscriber, logs the
  feed live to console.
- [`matteocelani/f1-telemetry`](https://github.com/matteocelani/f1-telemetry) — decodes the same
  feed into real-time WebSockets for a dashboard, and — directly relevant to §3 below — ships a
  **manual broadcast-delay buffer of up to three minutes**, so a viewer can hold the feed back to
  match their TV broadcast and avoid spoilers.
- [`OpenF1.Data`](https://www.nuget.org/packages/OpenF1.Data/) (.NET) — a standalone client for
  the same stream, unrelated to the OpenF1 API/website despite the name.

So the technical capability to get genuinely live F1 data at zero infrastructure cost exists and
is proven by working code, using the exact feed FastF1 already touches. It would mean writing a
small SignalR client rather than reusing FastF1 or paying OpenF1. Two real problems make this a
much worse option than "free and it works," though:

**It is very likely a Terms of Service violation, and the project's own end goal makes that
worse, not better.** F1's site terms restrict use of "materials on the site" — which live timing
data is — to personal, non-commercial use, and separately prohibit reverse-engineering or
deriving source code from the service. A hobbyist dashboard for personal viewing is already in a
grey area under those terms. This project's stated endpoint is an **automated trading bot** —
Lane C explicitly commercial, explicitly acting on the data for profit. That is not the "personal,
non-commercial use" the terms carve out; it is closer to the exact thing they prohibit. This
should be weighed the same way Lane C's own open item about venue ToS already is
(`00-roadmap.md`'s Lane C open decisions) — except here it's the *data source's* terms, and it
gates Lane B before Lane C is even reachable.

**It is being actively enforced, not just theoretically prohibited.** `matteocelani/f1-telemetry`
states plainly that F1 introduced IP-blocking measures against their hosted deployment, which is
currently down as a result — the maintainers' workaround is "self-host it yourself," which
doesn't remove the blocking risk, it just makes each instance small enough not to attract
attention yet. This isn't a documented, stable API with a rate limit to respect; it's an
adversarial relationship where the schema is explicitly reverse-engineered and "may change
between seasons" by the maintainers' own admission.

### 2.4 Where this leaves the three-way choice

| Source | Zero-budget? | Genuinely live? | ToS-clean? | Stable? |
|---|---|---|---|---|
| FastF1 live module | Yes | **No** — parses after the session only | Yes | Yes |
| OpenF1 free tier | Yes | **No** — free tier is historical-only | Yes | Yes |
| OpenF1 paid tier | **No** — €9.90/mo | Yes | Yes | Yes |
| Direct SignalR client | Yes | Yes | **Likely not**, especially for Lane C's commercial use | **No** — actively IP-blocked elsewhere |

No option is zero-budget, genuinely live, ToS-clean, and stable all at once. That is a harder
finding than `01` §9.5's original framing ("FastF1 doesn't satisfy this yet") — it says the
zero-budget path that *would* technically work is the one with the compliance and reliability
problems, and the compliant path costs money. This is the real decision B0 needs before B1's
delay measurement is even worth designing, because B1 needs a live source to measure delay
against.

## 3. B1 — delay/sync investigation, once a source is chosen

Kept here rather than dropped, since the investigation design doesn't depend on which source
wins §2.4, and one piece of prior art already answers part of it:

**`matteocelani/f1-telemetry`'s approach is manual, not measured**: the viewer holds the feed
back "by up to three minutes" — a number the user sets from their own experience of their
broadcast, not something the tool detects. That's evidence real F1 broadcast delay is commonly
in the low-minutes range on some setups, not sub-second — useful as a sanity check — but it
answers "how do I not get spoiled," not "how many seconds, precisely, for *this* broadcast setup
on *this* Apple TV app." The roadmap's original B1 plan (auto-record the broadcast, auto-log the
data feed, compare after the fact) is more rigorous than any prior art found here and should
still be the approach once a data source exists to log.

**Cheaper first step, in the project's own "prove it live, cheaply" spirit**: before building
auto-record/auto-log infrastructure, a single manual side-by-side observation — start a live
source and the Apple TV broadcast at the same time, watch for one clearly-timestamped event (e.g.
lights-out, or a purple sector flash) in both, and eyeball the gap — would tell the owner whether
the delay is roughly seconds (workable for corner-level prediction) or roughly minutes (Lane B's
whole premise is dead regardless of which data source gets chosen, since a multi-minute-old
"live" feed can't drive a real-time trade). That single observation is worth making before
investing in either a paid OpenF1 subscription or a legally-risky SignalR client, because it
could close B0 for a reason neither source choice fixes.

## 4. What this changes in other docs

- `01-data-pipeline.md` §9.5's flag ("FastF1's live module does not parse in real time") is
  correct but incomplete; this document is the fuller answer and `01` should point here rather
  than restate it.
- `00-roadmap.md`'s Lane B / Locked decisions entry for live data ("FastF1's free live module,
  not OpenF1's paid live tier... avoided per the zero-budget constraint") is now known to be
  moot regardless of budget: FastF1 doesn't provide live data at all, at any budget, so it was
  never really the zero-budget alternative to OpenF1's paid tier — it's not an alternative to it.

## 5. Open items — genuinely the owner's call, not guessable

1. **Which of §2.4's four rows to accept, if any.** Approve OpenF1's paid tier (€9.90/mo, needs
   explicit sign-off per `welcome.md`'s hard constraint); accept the ToS/stability risk of a
   direct SignalR client; or leave Lane B blocked until one of those changes (e.g. F1 opens an
   official live tier, or the owner decides the trading use case changes the risk calculus).
2. **Do the cheap manual delay observation first, regardless of §5.1's answer** — it's free,
   fast, and can independently kill B0 if the delay turns out to be minutes rather than seconds,
   which would make the source question moot.
3. **If a direct SignalR client is ever pursued, this needs its own explicit legal/risk
   conversation before any code is written** — not a default an agent should reach for even
   under time pressure, given both the ToS reading in §2.3 and the project's commercial end use.
