# 03 — Live Telemetry & Overtakes (Lane B)

Status: **§6's endpoint is WRONG and under revision — corrected 2026-08-26, same day, see the
banner below. Do not build against §6/§13 until it is rewritten.** The rest of the spec (§§4–5,
7–12) stands.

Previously: B0 source decided and specced 2026-08-26, implementation authorized for personal
research/development only and gated on the B1 delay check (§4.4). This document was a research
memo; it is now a build spec. §§1–3 are the original research, preserved unchanged — the four-way
source comparison, F1's own ToS text, and the IP-blocking precedent are the evidence the decision
rests on, not background to be deleted once the decision is made. §§4 onward are the spec.

Read `welcome.md`, `00-roadmap.md`, and `01-data-pipeline.md` §9.5 first. This document supersedes
`01` §9.5's flag.

---

> ## ⚠ CORRECTION 2026-08-26 — the unauthenticated endpoint is dead
>
> **§6.1 says B0 connects to the legacy `/signalr` endpoint without authentication. That endpoint
> has been returning HTTP 401 since approximately 2026-06-06 and is no longer usable.**
>
> **How the error was made.** §6.1's evidence was that `matteocelani/f1-telemetry` still had
> `F1_SERVER_URL = https://livetiming.formula1.com/signalr` on `main` and had been pushed
> 2026-08-07. That inference was wrong: the repo's `main` simply has not merged the fix. Its
> issue [#24](https://github.com/matteocelani/f1-telemetry/issues/24) ("SignalR endpoint returns
> 401 — F1 switched to SignalR Core with account auth", 2026-06-06) is open with two independent
> reporters confirming it, and the migration [PR #25](https://github.com/matteocelani/f1-telemetry/pull/25)
> is open and unmerged. The recent pushes were **replay-mode** work — which is what you build when
> you cannot connect live. Recency of commits was read as evidence of a working live path; it was
> not.
>
> **What is actually true.** F1 introduced `/signalrcore` in May 2025 (FastF1 issue #753) and
> appears to have shut the legacy endpoint off around June 2026 — roughly a year of overlap. The
> only live path now requires a **Bearer JWT tied to an F1 account**, sent both as an
> `Authorization` header and an `access_token` query parameter, over the SignalR **Core** text
> protocol (`\x1e` record separators, explicit handshake frame, type-1/type-3 messages,
> `negotiateVersion=1`) — a different wire protocol from §6.1's, not a URL swap.
>
> **The one thing that decides whether this stays zero-budget.** A commenter on #24 reports that a
> **free F1 account is sufficient — no F1 TV subscription needed**, with a working deployment.
> FastF1's own code says the opposite, printing "This feature requires an active F1TV
> Access/Pro/Premium subscription" and carrying `SubscriptionStatus` / `SubscribedProduct` claims
> in the token. Both cannot be right. **`UNVERIFIED` and unresolved** — it is a single community
> report against a maintainer's in-code assertion, and it is the difference between this lane
> costing nothing and costing a subscription.
>
> **Why this is not a quiet fix.** §16's open item 1 asked what to do *if* the unauthenticated
> endpoint closed, and called that a decision the owner had to take separately — because any
> authenticated route binds a named account holder to F1's terms directly and removes the
> "separate host, different agreement" caveat that §5's risk acceptance leans on. That item has
> now fired. §6 is not rewritten unilaterally; the route is the owner's call, and it is a
> different question from the one answered in §5.
>
> **Also unresolved:** #25's thread reports a reconnect loop against `/signalrcore` — WebSocket
> close code 1006 immediately after each subscribe snapshot, repeating every 5s — for one reporter
> while working for another. Unexplained. If it is general rather than environmental it is a
> second, independent problem with the authenticated path, and §9's backoff design would need to
> account for a connection that dies on a timer rather than after ~2h.

---

**Verification note — documentary, deliberately not live.** Every other spec in `/docs` opens by
saying its endpoints were called live against production. This one does not, and the difference is
intentional: connecting to `livetiming.formula1.com` is the exact act §2.3 identifies as legally
contested, and `welcome.md`'s hard constraint says no implementation before an approved spec. It
would be incoherent to establish that rule and then probe the endpoint while writing the spec that
governs it. So every claim in §§6–7 about the wire protocol, the channel set, and the payload
shapes is sourced from documentation and from working third-party clients read at HEAD, dated
below, and is marked **`UNVERIFIED`** in this project's own idiom until §13's acceptance run
executes it. §13 is the step that converts these to verified, and it is the first thing B0 does.

Sources read for §§6–12, all on **2026-08-26**:

| Source | Read at | What it establishes |
|---|---|---|
| `matteocelani/f1-telemetry` (`apps/backend/src/services/f1-client.ts`, `payload-parser.ts`, `core/src/constants.ts`, `core/src/live-timing.ts`, `docs/live-timing-types.md`) | HEAD, repo last pushed **2026-08-07** | The legacy handshake, headers, channel set, `.z` framing, payload schemas, reconnect behaviour, 2026 regulation impact |
| `theOehrly/Fast-F1` (`fastf1/livetiming/client.py`, `fastf1/internals/f1auth.py`) | HEAD | The *newer* authenticated endpoint, the topic list, the no-reconnect design |
| Fast-F1 issue [#753](https://github.com/theOehrly/Fast-F1/issues/753), "Live timing has changed to signalrcore" (opened 2025-05-27, closed) | — | When and how F1 introduced authentication, in the maintainer's own words |
| `docs.fastf1.dev` — `livetiming.html`, `api_reference/accounts_auth.html` | — | The real-time-parsing limit, the ~2h disconnect, the F1TV subscription requirement |
| FIA 2026 technical regulations coverage (Motor Sport, Motorsport.com, Raceteq) | — | DRS is abolished for 2026 (§7.3) |

The prior art in row 1 is load-bearing and worth stating plainly: **the endpoint this spec targets
is the same endpoint a third-party client that was actively maintained three weeks ago targets,
using the same protocol version, the same hub, the same channel names, and no authentication.**
That is why §6 is a spec and not a hypothesis.

---

# Part I — Research (2026-08-26, preserved unchanged)

## 1. What this phase needs, and why it's harder than Lane A

Lane A pulls a fixed set of data once and computes a prediction with no deadline. Lane B needs
to know a car's position, speed, and gap to the car ahead **while the session is still
happening**, with enough lead time before a braking zone that a probability is worth anything.
That means a genuinely live data feed — sub-minute latency at worst, ideally sub-second — which
is a different problem than anything Lane A has had to solve, and the zero-budget constraint
applies to it just as it does everywhere else in this project.

## 2. The four candidate sources, and what's actually true about each (verified 2026-08-26)

### 2.1 FastF1's live module — confirmed unusable for this, not a maybe

`00-roadmap.md` locks "FastF1's free live module" for live data, and `01` §9.5 already flagged
this as needing re-examination. Re-verified directly against FastF1's live-timing docs
(`docs.fastf1.dev/livetiming.html`, fetched 2026-08-26): **"It is not possible to do real-time
processing of the data."** The `SignalRClient` records the raw feed to a file as a session
happens; parsing only happens after, via `Session.load()`. The docs describe this as what the
tool is *for* (recording for later analysis), not a bug being worked toward a fix — `01` §9.5's
"does not and will never parse data in real time" phrasing is that document's own prior
characterization, consistent with what's quoted here, and is treated as confirmed rather than
re-derived independently this session. There is no code path in this library that produces a
probability *during* a session — this closes the question `01` §9.5 left open.

One more limit worth carrying forward even though it's now moot for this purpose: the docs also
state **"The SignalR Client seems to get disconnected after 2 hours of recording,"**
server-terminated, requiring a second recording file for anything longer (FastF1 merges them on
load). Irrelevant to *whether* FastF1 can go live — it can't, regardless — but relevant if a
future direct-connection client (§2.3) needs to survive a full race weekend session.

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

**It conflicts with F1's own legal terms, read directly rather than paraphrased.** Fetched
`formula1.com/en/information/legal-notices` (2026-08-26) rather than trusting a search summary,
since this is the clause the owner would actually be relying on. It states plainly: "The material
and content provided on the Site is for your personal, non-commercial use only," and separately,
"you agree not to reproduce, distribute, perform, display, modify, adapt, translate, prepare
derivative works from, decompile, reverse engineer, disassemble or otherwise attempt to derive
source code from the site." Crucially, it names the data type directly rather than leaving it to
inference: **"all materials on this Site, including, but not limited to live timing data,
historical race data..."** is the list of what's covered.

Two caveats on how far that reaches, stated rather than glossed over: these are formula1.com's
*website* terms, and `livetiming.formula1.com` is a separate host — whether the same terms
govern that endpoint, or it falls under a different agreement entirely, isn't settled by this
page alone. And "live timing data" as named here most directly describes the *website's own*
live timing display, not necessarily the raw SignalR stream a third-party client taps
independently, even though the underlying data is the same. Read most favorably to a builder,
there's an argument this specific endpoint isn't squarely what the clause contemplated. Read
plainly, F1 has named "live timing data" as protected, restricted use to personal/non-commercial,
and prohibited reverse-engineering "the site" in the same document — and this project's stated
endpoint is an **automated trading bot**, about as far from "personal, non-commercial use" as a
use case gets. That combination is enough to treat this as a real legal question needing an
actual answer, not a hobbyist grey area to route around by self-hosting quietly. It should be
weighed the same way Lane C's own open item about venue ToS already is (`00-roadmap.md`'s Lane C
open decisions) — except here it's the *data source's* terms, and it gates Lane B before Lane C
is even reachable.

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


---

# Part II — B0 build spec

## 4. The decision, and exactly what it authorizes

### 4.1 The decision

**Lane B's live data source is a direct, unofficial SignalR connection to
`livetiming.formula1.com`** — §2.4's fourth row. Not FastF1's live module (cannot parse live at
any budget, §2.1), not OpenF1's free tier (no live access at all, §2.2), not OpenF1's paid tier
(€9.90/mo, and unapproved).

This closes `00-roadmap.md`'s 2026-08-26 open decision on Lane B's data source. It was taken by
the owner with §2.3's findings in front of them — see §5.

### 4.2 What this spec authorizes: personal research and development only

This spec authorizes building a client that connects to the feed, parses it, writes it to local
disk, and computes tick state (§7) **for personal research and development on the owner's own
machine**. Nothing more. Concretely, in scope:

1. Connecting during live sessions to capture the raw feed to local disk.
2. Parsing that feed into the §7 tick-state contract, live and from recordings.
3. Running the B1 delay measurement (§4.4) against the live stream.
4. Developing and evaluating an overtake model offline, against recorded sessions.
5. Emitting predictions to a local log or console in **shadow mode** — computed, timestamped,
   recorded, scored after the fact, and read only by the owner.

Explicitly **not** authorized by this spec, each requiring its own separate approval:

1. **Feeding any live prediction into a trade.** No Lane C component may consume Lane B output.
   This is a hard interlock, not a sequencing preference (§4.3).
2. **Any hosted or public deployment** — no VPS, no cloud runner, no homelab service reachable
   from outside the LAN, no public dashboard, no shared WebSocket, no hosted demo. The only
   documented enforcement action in §2.3 was against a *hosted* deployment.
3. **Redistributing the data** in any form — no committing captures to git, no publishing derived
   per-tick series, no sharing recordings. See §11.
4. **Running the client outside a live F1 session window**, or from more than one process at a
   time (§6.5).

### 4.3 Why the scope is drawn there, and why it is stated rather than assumed

§2.3's central problem is that F1's terms restrict this material to "personal, non-commercial
use" while this project's stated endpoint is a commercial trading bot. That tension is real and
was accepted (§5) — but *when* it becomes concrete is a choice, and this spec makes it
deliberately.

A client capturing a feed to a laptop, in shadow mode, producing a number nobody acts on, is
squarely inside the ordinary meaning of personal, non-commercial research. The same client wired
to an order router is not, on any reading. Those are not the same activity and this spec does not
let them blur into each other by default. Deferring the commercial step until it is separately
approved keeps the defensible framing available for the entire build-and-test phase — which is
most of the calendar — and costs nothing, because the model has to be built and validated before
it could be traded on anyway.

This is the same shape as `welcome.md`'s existing rule that an approved trading spec authorizes
building the trader, not placing live trades. Here the interlock runs one layer earlier: an
approved Lane B spec authorizes building the feed, not wiring it to anything that trades.

**The interlock, stated as an implementation rule:** no module under Lane C may import from the
Lane B client, and the Lane B client must have no code path that writes to an order-placement
interface. Crossing that line needs a new decision recorded in `00-roadmap.md`, not a refactor.

### 4.4 Implementation is gated on the B1 delay check

§3, and the research memo's original open item 2, flagged a cheap observation that can kill B0 outright: if the gap between this
feed and the owner's actual broadcast is minutes rather than seconds, corner-level prediction
against a live market is dead regardless of which source feeds it. That observation has still not
been run.

**Gate:** the tick-state client (§§6–10) may be built and the B1 measurement run, because the
measurement needs a working client to measure against. **The prediction layer on top of it — the
overtake model, trigger conditions, scoring — is not authorized until B1 returns a workable gap.**
"Workable" is the owner's threshold to set, but the spec's own bar is: seconds, not minutes.

Building the client first is the cheapest ordering, not a way around the gate. §13's acceptance
run and B1's measurement are close to the same exercise.

## 5. Legal and ToS risk: identified, sourced, and knowingly accepted

**The owner has read §2.3's findings and decided to proceed on this route, accepting the risk.**
Recorded here so no future reader — agent or human — has to reconstruct whether this was noticed.

What was accepted, in summary, with the sourced text in §2.3 rather than restated here:

- F1's legal notices name **"live timing data"** explicitly in the list of protected material.
- The same document restricts the material to **"personal, non-commercial use only"** and
  prohibits reverse-engineering.
- §2.3's two caveats travel with those quotes and are not being quietly dropped: those are
  formula1.com's *website* terms and `livetiming.formula1.com` is a separate host, and "live
  timing data" most directly describes the site's own display. How far the clause reaches is not
  settled by that page alone.
- Enforcement is real, not theoretical: `matteocelani/f1-telemetry`'s hosted deployment was
  IP-blocked by F1. Their README still carries the notice — re-read 2026-08-26: *"Due to IP
  blocking measures introduced by Formula 1, the hosted version is currently down."*
- The project's end goal (Lane C, commercial automated trading) sits worst of all against
  "personal, non-commercial."

**Do not re-litigate this decision.** An agent reading this spec should not reopen it, argue
against it, or refuse work on the grounds that §2.3 exists. What an agent *should* do is hold the
scope boundary in §4.2 and the good-citizen behaviours in §6.5 and §9, because those are the terms
on which the risk was accepted: this is a small, local, personal-scale client that fails quietly
and gets out of the way when told to. It is not authorization to evade blocks, scale up, or go
commercial without a new decision.

Two consequences follow and are locked here:

1. **No evasion, ever.** If F1 blocks this client, the client stops (§9.3). It does not rotate
   IPs, route through a proxy or VPN chosen to defeat the block, vary its headers to look like a
   different client, or spread requests across hosts. A block is an answer, and the answer is
   taken.
2. **Nothing about this lane is published without a separate decision** — see §16's open item on
   the portfolio writeup. `welcome.md` says this project exists partly to be shown on LinkedIn,
   and Lane B is the one part where visibility carries its own risk.

## 6. The connection

All of §6 is `UNVERIFIED` per the verification note until §13 runs.

### 6.1 Endpoint and handshake — the legacy SignalR path

> **SUPERSEDED — see the correction banner at the top of this document. The endpoint described in
> this subsection returns 401 and the protocol described is no longer the one in use. Everything
> from here to the end of §6.5 is retained only to show what was specced and why it was wrong;
> none of it is buildable as written.**

There are **two** live-timing endpoints on this host, and the distinction matters more than
anything else in this section.

| | Legacy | Newer |
|---|---|---|
| Base | `https://livetiming.formula1.com/signalr` | `wss://livetiming.formula1.com/signalrcore` |
| Protocol | ASP.NET SignalR, `clientProtocol=1.5` | ASP.NET **Core** SignalR |
| Auth | **None** | F1TV subscription JWT (§6.4) |
| Used by | `matteocelani/f1-telemetry` (active 2026-08-07), the hobbyist clients in §2.3 | FastF1 ≥ 3.7.0 |

**B0 uses the legacy `/signalr` endpoint.** It is the one §2.3's proven prior art uses, the one
that needs no account, and the only one compatible with the zero-budget constraint.

This refines §2.3 rather than contradicting it. §2.3 said the feed "is not authenticated" — true
of this endpoint, and still true as of the 2026-08-07 prior art. What §2.3 did not know is that a
second, authenticated endpoint now exists alongside it. §6.4 carries that as a risk.

Handshake, four steps, in order:

1. `GET {base}/negotiate?clientProtocol=1.5&connectionData={connectionData}` → JSON containing
   `ConnectionToken`, plus a `Set-Cookie` that must be carried into every later step.
   `connectionData` is the URL-encoded `[{"name":"Streaming"}]`.
2. Open a WebSocket to
   `wss://livetiming.formula1.com/signalr/connect?clientProtocol=1.5&transport=webSockets&connectionToken={token}&connectionData={connectionData}`,
   sending the negotiate cookie.
3. `GET {base}/start?clientProtocol=1.5&transport=webSockets&connectionToken={token}&connectionData={connectionData}`
   — SignalR 1.x requires this explicit start after the socket opens; skipping it yields a socket
   that never delivers data.
4. Send the subscribe invocation over the socket:
   `{"H":"Streaming","M":"Subscribe","A":[[...channels...]],"I":1}`.

The subscribe **response** arrives as a frame with an `R` field carrying the full current state of
every subscribed channel — not a delta. This is the mechanism §9.4 relies on for reconnection.
Ordinary updates arrive in an `M` array of `{"H","M","A"}` messages, where `A` is
`[channelName, payload]`. A frame whose body is `{}` is the server keep-alive; discard it.

### 6.2 Request headers — required, fixed, and never varied

The legacy handshake requires specific headers. Per the 2026-08-07 prior art, negotiate sends
`User-Agent: BestHTTP`, `Accept-Encoding: gzip, deflate`, `Origin: https://www.formula1.com`,
`Referer: https://www.formula1.com/`; the WebSocket upgrade sends `User-Agent: BestHTTP`,
`Origin: https://www.formula1.com`, and the negotiate cookie.

This sits next to §5's no-evasion rule, and the two must not be confused, so the rule is stated
precisely:

> These header values are **what the protocol requires to complete a handshake at all**. They are
> set once, as constants, and are never varied. Changing headers, User-Agent, IP, or origin *in
> response to a failed or blocked connection* is evasion and is prohibited by §5. Setting the
> documented values that make the handshake work in the first place is not.

The test is mechanical: if the client contains any code path that picks a different header value
depending on what happened to a previous attempt, that path violates §5.

### 6.3 Subscribed channels

Subscribe to exactly this set, and no more. Every entry is either parsed into tick state (§7) or
required to interpret one that is.

| Channel | Why B0 needs it |
|---|---|
| `Heartbeat` | Liveness signal (§9.2). ~1 Hz. |
| `SessionInfo` | Session identity and `Path`; drives the session-change detection in §9.5. |
| `SessionData` | Session lifecycle events — start/stop, session part boundaries. |
| `SessionStatus` | Session state transitions (`Started`, `Finished`, `Finalised`). |
| `DriverList` | Racing number → FIA three-letter code. **Required** — it is the join to `01` §8.2's canonical driver key. |
| `TimingData` | Position, gap to leader, interval to car ahead, in-pit, laps. The spine of §7's tick. |
| `TimingDataF1` | Adds `Retired` / `Stopped` / `Status` and micro-sector segments — the degraded-mode positioning fallback in §8. |
| `CarData.z` | Speed, throttle, brake, gear, RPM, channel 45. ~3.7 Hz. |
| `Position.z` | Per-car X/Y track coordinates. ~3.7 Hz. |
| `TrackStatus` | Green/yellow/SC/VSC/red. A tick under SC is not a tick a corner model may reason about. |
| `LapCount` | Current and total laps. |
| `ExtrapolatedClock` | Session clock, for aligning against the broadcast in B1. |

Explicitly **not** subscribed, and the reason each is left out:

- `TeamRadio`, `AudioStreams`, `ContentStreams` — media, irrelevant, and the least defensible
  thing to be pulling.
- `WeatherData` — Lane A already owns weather via Open-Meteo (`01` §5). A second source would be
  a reconciliation problem for no gain at B0.
- `TimingStats`, `TopThree`, `ChampionshipPrediction`, `RcmSeries`, `TlaRcm` — derived or
  presentational; nothing in §7 consumes them.
- `RaceControlMessages` — genuinely useful later (flags, penalties), but `TrackStatus` covers
  what B0's tick needs and this spec scopes to what is needed now.
- `TimingAppData` (tyre compound/stint), `PitStopSeries`, `LapSeries`, `OvertakeSeries` — all
  plausible inputs to a *model*, none of them part of the *tick state* B0 is scoped to. In
  particular `OvertakeSeries` is an obvious label source for a future overtake model and is
  deliberately deferred rather than forgotten: its availability is documented as varying by
  session, and adding it is a B2-or-later decision made against a model that exists.

Subscribing to less is also cheaper to keep correct under §10's drift rules: every channel in the
set is a schema this project has to notice changing.

### 6.4 The authentication risk, and what to do if the legacy endpoint closes

FastF1 moved to the authenticated `signalrcore` endpoint in v3.7.0 after F1 changed the service in
May 2025 (issue #753). `fastf1/internals/f1auth.py`, read at HEAD, performs a browser-based F1
account sign-in, extracts a `subscriptionToken` JWT, and verifies it against
`api.formula1.com/static/jwks.json`; the token carries `SubscriptionStatus` and
`SubscribedProduct` claims. FastF1's own docs state it "can require an active F1TV
Access/Pro/Premium subscription to access certain data," and its client exposes `no_auth=True`
with the caveat that it "may only work for some sessions or may only return empty or partial
data."

The legacy endpoint has nonetheless kept working unauthenticated — the 2026-08-07 prior art is the
evidence — so this is a **risk to detect, not a blocker to design around**.

**Detection signature:** HTTP 401 or 403 from `/negotiate` or `/start`; or a negotiate that
succeeds while the subscribe response's `R` field comes back empty or missing every channel in
§6.3's required set.

**Required behaviour on that signature: stop, loudly.** Raise through `lib.invariants.require`
with a message naming this section, write the failure to the run log, exit. Specifically:

- **Do not** fall back to the `signalrcore` endpoint.
- **Do not** retry past §9.3's ceiling — a gate is not a transient fault.
- **Do not** attempt to obtain, borrow, or synthesize a subscription token.

The path forward from there is an owner decision, not an agent's — filed as §16's first open item,
because it is simultaneously a money decision (F1TV Access is paid, and `welcome.md` requires
explicit approval for any paid dependency) and a materially worse legal posture: an F1TV
subscription binds a named account holder to F1's subscription terms directly, which removes the
"separate host, different agreement" caveat §2.3 relies on. It is a genuinely different decision
from the one recorded in §5, and it must be taken separately.

### 6.5 Connection discipline

These are the behaviours that keep this a personal-scale client, and they are part of what §5's
risk acceptance assumed.

1. **One connection at a time.** A single process, one socket. No parallel clients, no second
   subscription for a different channel set. (Same reasoning as this project's Jolpica-throttle
   convention: a shared budget is shared whether or not the code knows it.)
2. **Only during actual session windows.** Connect no earlier than 60 minutes before the scheduled
   session start — FastF1's maintainer notes some data only appears about an hour ahead — and
   disconnect once `SessionStatus` reaches `Finished`/`Finalised`, or 30 minutes after the
   scheduled end, whichever is first. No idle connections, no "leave it running all weekend."
3. **Develop against recordings, not against F1.** §11's raw capture is replayable, and the
   replay path is the default development target. Every avoidable live connection is one the
   project does not need to make. Only §13's acceptance run and B1's measurement need a live one.
4. **A visible run log.** Every connect, disconnect, reconnect, backoff, and stop is logged with a
   timestamp. If this is ever questioned, the honest account of what the client did should exist.

## 7. What the client parses and exposes — the tick-state contract

B0's product is a **tick**: one immutable snapshot of per-car state at a point in session time.
Not a prediction, not an event, not a database. `04`/`05`'s relationship to `01`'s snapshot is the
model: the tick is the only interface between this document and any future overtake model, and the
model reads ticks and nothing else.

### 7.1 The tick record

```
Tick
  session_key        str    from SessionInfo.Path — identifies the session uniquely
  t_feed             str    feed timestamp of the newest message folded into this tick
  t_local            float  local monotonic receipt time, for the B1 delay measurement
  track_status       int    TrackStatus code (1 clear, 2 yellow, 4 SC, 5 red, 6 VSC, 7 VSC ending)
  lap_current        int?   LapCount.CurrentLap
  lap_total          int?   LapCount.TotalLaps
  degraded           set    which of {position, cardata} are unavailable this tick (§8)
  gap_after_reconnect bool  True on the first tick after a reconnect gap (§9.4)
  cars               dict   FIA three-letter code -> CarState
```

```
CarState
  code           str     FIA three-letter code — the canonical key (01 §8.2)
  racing_number  str     feed-native key, retained for provenance
  position       int?    TimingData.Lines[n].Position
  gap_leader     str?    GapToLeader.Value, verbatim string, unparsed
  gap_ahead      str?    IntervalToPositionAhead.Value, verbatim string, unparsed
  catching_ahead bool?   IntervalToPositionAhead.Catching
  in_pit         bool    TimingData InPit
  pit_out        bool    TimingData PitOut
  retired        bool    TimingDataF1 Retired — latched (§7.4)
  stopped        bool    TimingDataF1 Stopped — latched (§7.4)
  laps           int?    NumberOfLaps
  x, y           int?    Position.z coordinates; None in position-degraded mode (§8)
  speed          int?    CarData.z channel 2, km/h
  throttle       int?    CarData.z channel 4, 0-100
  brake          int?    CarData.z channel 5, 0 or 100
  gear           int?    CarData.z channel 3, 0-8
  rpm            int?    CarData.z channel 0
  aero_raw       int?    CarData.z channel 45, opaque — see 7.3
```

Keying on the FIA three-letter code rather than the racing number is not a preference; it is
`01` §8.2's locked canonical key, and it is what lets a tick ever be joined to a Lane A snapshot.
The racing number is kept alongside because it is what the feed actually sends and provenance
should survive the translation.

The gap fields are carried as **verbatim strings**, not parsed floats. The feed's gap format is
context-dependent (`"+1.234"`, `"1L"`, `""`, lap-down markers) and B0's job is to expose what the
feed said, not to guess a numeric interpretation a model has not been designed against yet.
Parsing them is the model's decision, made later, with the raw string still available to check.

### 7.2 Payload shapes the parser must handle

- **`CarData.z` / `Position.z`** are base64, then **raw DEFLATE with no zlib header** — Python
  `zlib.decompress(data, -zlib.MAX_WBITS)`, not plain `zlib.decompress`. Then JSON.
- `CarData.z` decompresses to `{"Entries":[{"Cars":{"<racingNumber>":{"Channels":{"0":…}}}}]}`.
- `Position.z` decompresses to `{"Position":[{"Timestamp":…,"Entries":{"<racingNumber>":{"X":…,"Y":…,"Z":…}}}]}`.
  `Z` is altitude and is broadcast as 0; B0 ignores it.
- `TimingData` arrives as `{"Lines":{"<racingNumber>":{…}}}` and is a **delta protocol** — only
  changed fields are sent. The parser merges into retained state; it never treats an update as a
  complete record.
- `TimingDataF1` extends `TimingData`. Field collections (`Stats`, `Segments`) arrive as an
  **array on snapshots and a keyed object on deltas**; both shapes must be accepted for the same
  field.

### 7.3 CarData channels, and the 2026 DRS problem

| Index | Field | Notes |
|---|---|---|
| 0 | `rpm` | |
| 2 | `speed` | km/h |
| 3 | `gear` | 0 = neutral, 1–8 |
| 4 | `throttle` | 0–100 |
| 5 | `brake` | 0 off, 100 full |
| 45 | `aero_raw` | **Not DRS any more.** See below. |

`00-roadmap.md`'s Phase B0 description asks for "position, speed, gap, brake/throttle/DRS". **DRS
does not exist in 2026.** The FIA's 2026 regulations abolish it outright and replace it with
active aerodynamics (wings that open on straights and close for corners, originally X-mode and
Z-mode) plus a separate battery-boost overtake mode for a car within one second of the car ahead.
Channel 45, which carried DRS state through 2025 (`0` closed, `8` detection zone, `10` open, `14`
closing), now carries active-aero state, and the prior art is explicit that **the numeric encoding
for the new system has not been confirmed from live capture**. The boost/overtake system has not
been observed as a distinct channel at all.

**Decision:** `aero_raw` is exposed as an **opaque integer**, passed through unmodified, with no
name, no enum, and no semantic meaning attached. B0 does not decode it and does not invest in
reverse-engineering it. Any downstream consumer that wants meaning from it must first establish
the mapping from captured data and record it here.

This is deliberately not filed as an open item for the owner: it is a scoping call, and the
scoping call is that a field whose meaning is unknown gets carried, not guessed. Guessing it would
produce exactly the failure `lib/invariants.py` exists to prevent — a plausible-looking number
that is wrong.

The roadmap's B0 line is corrected accordingly (§15).

### 7.4 State semantics

- **Merge, don't replace.** `TimingData` deltas fold into retained per-car state.
- **Latch terminal states.** Once `Retired` or `Stopped` is true for a car, it stays true for the
  rest of the session. The feed is lossy and has been observed to drop these flags; a car that
  un-retires is a parsing artifact, never a fact.
- **Replace state wholesale on a subscribe snapshot.** The `R` frame is the complete truth at that
  moment; merging it into pre-existing state lets stale cars from a previous session survive into
  the new one.
- **Never interpolate across a gap.** §9.4.
- **A tick is immutable once emitted.** Consumers get a value, not a live-updating handle.

### 7.5 What B0 does not do

No corner detection, no braking-zone geometry, no overtake probability, no trigger conditions, no
market interaction. Those are B2+ and, per §4.4, are not authorized until B1 clears. B0 ends at
"here is a correct, timestamped, per-car tick, and here is an honest statement of what was missing
from it."

## 8. Degraded modes — the feed does not always carry telemetry

The prior art's own documentation, re-read 2026-08-26, states: *"`CarData.z` and `Position.z` are
not guaranteed to be available in every session. Some sessions (notably in 2026) have been
observed to not broadcast GPS position data at all."*

This is the second-largest live threat to Lane B's premise after the broadcast-delay question, and
it gets its own section rather than a footnote, because corner-level prediction needs X/Y and the
feed may simply not send it. A spec that buried this would be a spec resting on an assumption.

Two degraded modes, both first-class and both explicitly reported in the tick's `degraded` field:

**`position` degraded — no `Position.z`.** Timing-derived state still flows: order, gaps,
interval, in-pit, retired, laps. What is unavailable is *where on the circuit* a car is, and
therefore every corner-proximity trigger. The client keeps running and keeps emitting ticks with
`x`/`y` as `None`. `TimingDataF1`'s micro-sector segments are the documented coarse fallback for
approximate track position — B0 captures them for that purpose but does not build a positioning
model on them; that is B2's problem if it ever needs solving.

**`cardata` degraded — no `CarData.z`.** No speed, throttle, brake, gear, aero. Order and gaps
still flow.

The rules that make this safe:

1. **Missing is never zero.** An absent channel yields `None`, never a default. A brake value of
   `0` means the driver is off the brakes; a brake value of `None` means the feed did not say.
   Collapsing those is exactly the silent-wrong-number failure §10 exists to prevent.
2. **Report, don't infer.** `degraded` is populated from what actually arrived in the window, and
   is part of the tick.
3. **A degraded tick may not feed a prediction that needs what is missing.** Any future consumer
   requiring `x`/`y` must check `degraded` and decline to predict, rather than predict on partial
   state. Enforced by §12.9.
4. **Degradation is logged once per transition**, not per tick — a channel going missing is an
   event worth seeing in the run log without drowning it.

## 9. Disconnects, reconnection, and backoff

### 9.1 The ~2-hour disconnect: whose property is it?

§2.1 carried FastF1's note forward — *"The SignalR Client seems to get disconnected after 2 hours
of recording. It looks like the connection is terminated by the server"* — and left open whether
that is FastF1's client or the feed itself. Resolved, from FastF1's source at HEAD and from the
prior art's behaviour against the same feed:

**The drop is server-side. Being fatal is client-side, and specific to FastF1.**

- The termination originates with F1's infrastructure. FastF1's own docs say so, and its client
  does nothing that would cause a timed disconnect: `_supervise()` only exits when *no data
  arrives* for `timeout` seconds. Corroborating detail: FastF1's handshake handles an `AWSALBCORS`
  cookie, i.e. there is an AWS Application Load Balancer in front of this service, and connection
  duration limits at a load balancer are a routine cause of exactly this kind of periodic,
  clean-looking close.
- What makes it *fatal* is FastF1's design. `client.py` builds its connection with a literal
  `# TODO: enable auto reconnect?` and has no reconnection path — when the socket closes, the
  recording ends, which is why the docs tell users to restart manually into a second file.
- The prior art does not have this problem against the same endpoint. `matteocelani/f1-telemetry`
  treats a close as routine, reconnects with exponential backoff, and re-runs the full handshake;
  its README describes multi-hour session use with no 2-hour ceiling mentioned anywhere.

**Design consequence:** a periodic server-initiated close is a *normal event in the lifecycle*,
not an error. B0 reconnects. The design work is not avoiding the drop — it is making the seam
across it honest, which is §9.4.

### 9.2 Liveness: heartbeat absence, not a data timeout

Do **not** inherit FastF1's `timeout=60` "exit if no data for 60s" heuristic. That is a *recording*
heuristic and it is wrong for a live client: red-flag stoppages, pit-lane-closed periods, and gaps
between sessions are all legitimately quiet on the data channels, and exiting through them loses
the session.

`Heartbeat` arrives at roughly 1 Hz and is the liveness signal. Rules:

- No `Heartbeat` for **30 seconds** → the connection is considered dead; close it and enter §9.3's
  reconnect path. (Quiet data channels alone never trigger this.)
- `Heartbeat` and `WeatherData` are broadcast between sessions and are therefore **not** evidence
  that a session is live. Session activity is judged from `SessionStatus`, `SessionInfo`,
  `TimingData`, `DriverList`, and `ExtrapolatedClock` — the same distinction the prior art draws.

### 9.3 Classifying a disconnect, and the response to each

The single most important behaviour in this spec is telling a *routine drop* apart from *being
refused*, because §5's accepted-risk terms turn on not escalating. Retrying into a block is
precisely the escalation this project promised not to do.

| Class | Signature | Response |
|---|---|---|
| **Routine close** | Socket closes cleanly; no heartbeat for 30s; the ~2h server close | Reconnect with backoff, up to the ceiling below |
| **Transient network** | DNS failure, connection refused, TLS error, 5xx from negotiate/start | Same as routine |
| **Refusal** | **401 / 403** on negotiate, connect, or start | **Stop. One attempt, no retry.** |
| **Rate limit** | **429**, or `Retry-After` present | **Stop.** Log the header verbatim. Do not retry within the session |
| **Gated** | Negotiate succeeds but subscribe returns empty/missing required channels (§6.4) | **Stop** |
| **Exhausted** | Backoff ceiling reached | **Stop** |

Backoff for the retryable classes: **exponential from 5s, doubling, capped at 60s, with ±20%
jitter, and a hard ceiling of 8 consecutive failed attempts** (≈5 minutes of trying) before the
client gives up and exits. The 5s/60s shape follows the prior art; the attempt ceiling and the
jitter are this spec's additions, and both exist for the same reason — an unattended client that
retries forever is how a personal-scale client turns into something that looks like an attack. A
successful connection resets the counter.

On any **Stop** class the client exits with a non-zero status and a message naming this section.
It does not retry later in the session, does not schedule itself, and does not fall back to
another endpoint. Restarting after a refusal is an owner decision taken with §5 in view — an agent
must not restart it automatically, and must not "just try once more."

Explicitly prohibited in every class, restating §5 where it is most likely to be violated under
pressure: changing IP, proxying, VPN-hopping, varying headers or User-Agent between attempts,
distributing attempts across machines, or reconnecting faster than the schedule above.

### 9.4 The reconnect seam: state resumes, ticks do not

On reconnect the client re-runs the full §6.1 handshake, and the subscribe response's `R` field
returns the **complete current state** of every channel. So state resumes cleanly: positions,
gaps, pit flags and lap counts are all correct immediately after reconnecting.

What does **not** resume is the tick stream. `CarData.z` and `Position.z` are ~3.7 Hz samples of a
moment; the samples during the gap are gone and are not recoverable from anywhere. A model that
smooths across the seam will be reading a fabricated trajectory.

Rules, and §12 asserts them:

1. **Never interpolate, extrapolate, or forward-fill telemetry across a gap.** Speed and position
   restart from the first post-reconnect sample.
2. **Emit an explicit gap marker.** The first tick after a reconnect carries
   `gap_after_reconnect = True`, and the run log records the gap's start, end, and duration.
3. **Replace, don't merge, on the `R` snapshot** (§7.4).
4. **Staleness guard.** If the newest data folded into a tick is older than **5 seconds**, the tick
   is marked stale and no consumer may produce a prediction from it. A five-second-old car
   position is not a live car position, and a corner-level model reading one is worse than one
   reading nothing.

### 9.5 Session changes

`SessionInfo.Path` identifies the session. If it changes on a live connection — F1's timing system
switching from Qualifying to Race, say — retained state belongs to the old session. Clear all
state, reconnect, and start a new capture file. Carrying state across a session boundary produces
ghost cars, which is the same class of bug as merging an `R` snapshot.

## 10. Schema drift — fail loud, never misparse

The feed is undocumented and reverse-engineered, and the prior art's own maintenance note says so
plainly: *"The F1 Live Timing feed has no public schema. Types are reverse-engineered by the
community and may change between seasons without notice."* 2026 already proves it — channel 45
changed meaning under the new regulations (§7.3), and some 2026 sessions stopped broadcasting
position data (§8).

This project has a settled answer to this class of problem and B0 inherits it unchanged: use
`lib.invariants.require`, not bare `assert` (stripped under `python -O`, which is the wrong
failure mode for a data check), and prefer a loud crash to a plausible-looking wrong number —
`fit.py` and `backfill.py` are the working examples.

**The rule:** a required field that is missing, or a value outside its declared domain, raises
`InvariantError` and halts the tick pipeline. It does not get a default, it does not get skipped
with a warning, and it does not produce a tick with a hole in it that looks like a real tick.

The line between "drift" and "degradation" is drawn deliberately, because conflating them makes
both useless:

- **An entire channel absent** is *degradation* (§8) — expected, handled, reported, keeps running.
- **A present channel with a shape that does not match §7.2** is *drift* — unexpected, halts.

Concretely:

| Condition | Behaviour |
|---|---|
| A channel arrives that is not in §6.3 | Log once, ignore. Not an error — F1 may add channels |
| A `.z` payload fails base64 or DEFLATE decode | Halt. This is a framing change, not noise |
| A decompressed payload is not the §7.2 shape | Halt |
| `TimingData.Lines` missing, or not a mapping | Halt |
| A racing number in `CarData`/`Position` with no `DriverList` entry | Halt — the canonical key join has broken |
| A `CarData` channel index in §7.3's table is missing | Field is `None` for that sample; log once per session |
| A `CarData` channel index **not** in §7.3's table appears | Log once, ignore |
| `speed`, `throttle`, `brake`, `gear`, `rpm` outside its declared range | Halt. An out-of-range value means the index map moved |
| `TrackStatus` code not in {1,2,4,5,6,7} | Halt |
| Two cars reporting the same `Position` in the same tick | Log, do not halt — documented feed lossiness, not drift |

**A season-boundary review is mandatory, not advisory.** Before the first session of a new season,
§7's channel map and payload shapes are re-checked against FastF1's and the prior art's changelogs
and this document updated. The 2026 DRS change is the precedent: a client written in 2025 would
have kept reporting "DRS open" all through 2026 and been wrong every single time, without ever
raising an error, because the *shape* never changed — only the meaning did. That is the failure
mode this section cannot catch on its own, and the review is what covers it.

## 11. Where the data lives, and who can reach it

Provenance drives this section. §2.3's terms restrict redistribution, and the one documented
enforcement action was against a *hosted* deployment. So storage is decided deliberately, not by
convenience.

### 11.1 Location

- **Raw capture:** `data/live/raw/{session_key}/{utc_timestamp}.jsonl` — append-only, one JSON
  frame per line, written as received, before any parsing. Written first, always, even when
  parsing fails: a capture that survives a parser bug is what makes §10's halts debuggable and
  what makes replay development possible without reconnecting to F1.
- **Parsed ticks:** `data/live/ticks/{session_key}.jsonl` — the §7 records.
- **Run log:** `data/live/logs/{session_key}.log` — §6.5's connect/disconnect/backoff/stop record.

A new file per reconnect segment, following FastF1's own convention for its 2-hour splits.

### 11.2 Git: `data/live/` is ignored, and this is a hard rule

**`data/live/` goes in `.gitignore`, with a comment explaining why, before any client code is
written.**

This is not tidiness. This repo *deliberately commits* `data/training/winner.csv` and
`data/snapshots/*.json`; it is a portfolio repo intended to be shown publicly; and this project's
standing workflow is to commit and push proactively, enforced by a hook that blocks on a dirty
tree. Every one of those is a mechanism that would push a live-timing capture to a public remote
by default. Pushing it is redistribution of F1 live timing data — precisely what §2.3 quotes the
terms as prohibiting — and it would happen mechanically, without anyone deciding to do it.

The rule, stated so it cannot be misread:

- **No raw capture, no parsed tick file, and no per-tick or per-car derived series is ever
  committed to git.** Not sampled, not truncated, not "just one file as an example," not in a
  test fixture, not in a notebook output cell.
- What **may** be committed from this lane: code, this spec, and **fitted model artifacts** —
  coefficients, calibration constants, aggregate summary statistics. A number learned from the
  data is not the data.
- Test fixtures that need feed-shaped input use **synthetic** payloads written by hand to match
  §7.2, not captured frames.

`data/cache/` is the existing precedent for ignored-because-it-is-reconstructible; this is ignored
for a different and stronger reason, and the `.gitignore` comment should say so.

### 11.3 Access

- **The owner's own machine only.** No VPS, no cloud runner, no scheduled cloud routine — which
  rules this lane out of the automation path `00-roadmap.md`'s backlog contemplates for Lane A's
  snapshots.
- **The homelab in the roadmap's backlog is not approved for this**, notwithstanding that it is
  already-owned hardware and zero-budget-compliant. A machine that runs services is one
  configuration change away from being reachable, and §4.2 rules out anything reachable. If the
  owner later wants Lane B on the homelab, that is a decision to record, with the network posture
  stated explicitly.
- **No network service.** The client does not expose a socket, port, or HTTP endpoint, not even on
  localhost. Consumers read files. The prior art's architecture — a backend rebroadcasting the feed
  over WebSocket to browser clients — is exactly the shape that got IP-blocked, and B0 does not
  reproduce it.
- **Retention: keep indefinitely, locally.** Recorded sessions are the replay corpus that keeps
  §6.5's "develop against recordings" rule affordable, and they are irreplaceable — a session not
  captured is gone. Deleting them would push development back onto live connections, which is the
  opposite of the good-citizen behaviour §5 assumed. If disk pressure ever forces a choice, drop
  parsed ticks (regenerable from raw) before raw captures.

## 12. Required assertions

Fail loudly rather than emit a plausible wrong number (`02` §8's principle, `05` §9's form). Use
`lib.invariants.require`, not bare `assert` — `lib/invariants.py` explains why. Every one of these
guards *data*, which is that module's stated test for what belongs there.

1. Every `CarState` key is a FIA three-letter code present in `DriverList` for this session
   (`01` §8.2). A racing number that cannot be resolved to a code halts.
2. `speed` ∈ [0, 400] km/h; `throttle` ∈ [0, 100]; `brake` ∈ {0, 100}; `gear` ∈ [0, 8];
   `rpm` ∈ [0, 20000] — or `None`. An out-of-range value halts (§10): it means the channel index
   map moved, not that a car did something unusual.
3. `track_status` ∈ {1, 2, 4, 5, 6, 7}.
4. No two ticks share a `(session_key, t_feed)` pair.
5. `t_feed` is non-decreasing within a session, except across a reconnect gap, where
   `gap_after_reconnect` must be `True`.
6. `Retired` and `Stopped` never transition true → false within a session (§7.4's latch).
7. No telemetry value in a tick was produced by interpolation, extrapolation, or forward-fill
   across a reconnect gap (§9.4). Structurally: the parser has no interpolation code path at all,
   and this assertion is the test that keeps it that way.
8. A tick whose newest input is older than 5 seconds is marked stale, and no prediction is emitted
   from a stale tick (§9.4).
9. A tick with `position` in `degraded` never reaches a consumer that requires `x`/`y` (§8).
10. `x`/`y` are `None` in position-degraded mode, never `0` and never a carried-over previous
    value (§8's missing-is-never-zero rule).
11. The subscribe response's `R` frame **replaces** state; a merge into pre-existing state is a
    defect (§7.4).
12. The client holds exactly one connection to `livetiming.formula1.com` at a time (§6.5).
13. No file under `data/live/` is tracked by git (§11.2). Worth a repo-level test, not just a
    `.gitignore` line — the `.gitignore` is the mechanism, the assertion is the check that the
    mechanism is still in place.
14. Request headers are constants and do not vary by attempt number or by the outcome of any
    previous attempt (§6.2 / §5's no-evasion rule).
15. On any Stop-class disconnect (§9.3), no further connection attempt is made in that process.

## 13. Acceptance — the first-connection validation run

This is the step that converts §§6–7's `UNVERIFIED` claims into verified ones, and it is B0's
first deliverable after the client exists. It is one session, and it should be a **practice
session, not a race** — lower stakes, and §2.1's own note is that some data only appears close to
a session.

The run: start the client no earlier than T-60min, capture one full practice session, disconnect
on `SessionStatus` finished. Then check, from the capture:

1. The §6.1 handshake completed unauthenticated, and the subscribe `R` frame carried every §6.3
   channel. (If not → §6.4's gate, stop, and file the open item.)
2. `CarData.z` and `Position.z` decoded as base64 + raw DEFLATE into §7.2's shapes.
3. `CarData` channel indices 0/2/3/4/5/45 are present, and 0/2/3/4/5 fall in §12.2's ranges.
   Record what channel 45 actually contains across the session — this is the observation that
   would eventually let §7.3's opaque field be decoded, if anyone ever decides that is worth doing.
4. Every racing number in `CarData`/`Position` resolved to a `DriverList` code.
5. Whether `Position.z` was broadcast at all (§8's 2026 concern), and whether either channel
   dropped mid-session.
6. Observed update rates for `CarData.z`, `Position.z`, and `Heartbeat`, against the ~3.7 Hz /
   ~3.7 Hz / ~1 Hz the prior art documents.
7. Whether a server-initiated disconnect occurred, at what elapsed time, and whether the
   reconnect path recovered state correctly (§9.4) — this is the measurement that either confirms
   or refutes §9.1's account of the ~2h drop, on this feed, from this client.
8. `t_local` was recorded on every tick, so the same capture can serve B1's delay measurement.

Update this document with the results and drop the `UNVERIFIED` markers on what the run confirms.
Anything the run contradicts is a spec bug to fix here first, before more code.

## 14. What is deliberately not in B0

Listed so a future reader can tell "not built yet" from "decided against":

- Corner geometry, braking-zone definitions, trigger conditions, overtake probability — B2+, and
  gated by §4.4.
- Broadcast delay *compensation*. B1 measures the delay; nothing here corrects for it. The prior
  art's three-minute manual buffer is a spoiler-avoidance feature for human viewers and is the
  opposite of what a prediction pipeline wants (§3).
- Any market or trading interaction (§4.2, §4.3).
- Tyre/stint, pit-stop, and overtake-event channels (§6.3) — deferred, not rejected.
- Decoding channel 45 (§7.3).
- A live positioning model built on micro-sector segments (§8).
- Any hosted, shared, or networked component (§11.3).

## 15. What this changes in other docs

- `00-roadmap.md` **Phase B0**: status changes from "premise researched, not resolved" to
  "source decided and specced, implementation gated on B1." Its description line must also lose
  **DRS** — §7.3: DRS does not exist in 2026.
- `00-roadmap.md` **Locked decisions**: add Lane B's live data source, with the §5 acceptance and
  the §4.2 scope limit stated as part of the decision, not as a footnote to it.
- `00-roadmap.md` **Open decisions**: the 2026-08-26 Lane B data-source entry is resolved and
  closed. The F1TV question (§6.4) is filed as a new one.
- `00-roadmap.md` **Phase C1**: stale twice over — it describes FastF1's live module as the only
  live source considered and repeats the ~2h cap as a property of the feed. Both are superseded by
  §§6 and 9.1.
- `00-roadmap.md` **backlog**: the "Lane B: FastF1's live module does not parse in real time — the
  B0 premise needs revisiting" line is resolved by this document.
- `welcome.md`: two corrections only. The zero-budget bullet's parenthetical ("we deliberately
  avoid it and use FastF1's free live module instead") is factually wrong, and the pointer to this
  file still calls it "not a build spec yet."
- `01-data-pipeline.md` §9.5: already points here; unchanged.
- `.gitignore`: add `data/live/` per §11.2.

## 16. Open items — genuinely the owner's call, not guessable

1. **If the legacy endpoint closes, what then?** (§6.4.) F1 already moved FastF1's path behind an
   F1TV Access/Pro/Premium subscription in 2025; the unauthenticated legacy endpoint still works
   as of the 2026-08-07 prior art, but it is one deprecation away from not working. The options
   are (a) buy an F1TV subscription — paid, so it needs explicit approval under `welcome.md`'s
   zero-budget constraint, *and* it binds a named account to F1's subscription terms directly,
   which is a materially worse legal posture than the anonymous access §5's acceptance was
   weighed against; (b) revisit OpenF1's €9.90/mo paid tier, which is at least ToS-clean; or
   (c) stop Lane B. Not decidable in advance by an agent: it trades money against legal exposure
   against abandoning the lane, and it is a different decision from the one already taken.
2. **Run the B1 manual delay observation.** Carried forward from §3 and the research memo's original open item 2, now
   with teeth: §4.4 makes B0's prediction layer *gated* on it, not merely informed by it. A
   multi-minute delay closes Lane B regardless of data source. Still not run; free; should be done
   at the first available session, alongside §13's acceptance run.
3. **Whether Lane B output may ever feed Lane C.** §4.3 makes this an explicit interlock rather
   than a sequencing detail. Turning it on is the moment the "personal, non-commercial" framing
   §2.3 examined stops being available, and it needs to be a decision taken on that basis with a
   date on it — not something that happens because two modules were both finished.
4. **Whether this lane appears in the portfolio / LinkedIn writeup at all.** `welcome.md` says the
   project is built to be shown. Lane B is the one part where being seen carries its own risk, and
   the enforcement precedent in §2.3 was against the most *visible* instance of this behaviour.
   Options run from "omit Lane B entirely," through "describe the architecture without naming the
   endpoint," to "write it up in full." The owner's call, and worth taking before anything about
   this lane is published rather than after.

## 17. Sources

- FastF1 live timing docs — `https://docs.fastf1.dev/livetiming.html` (real-time limit, ~2h drop)
- FastF1 auth docs — `https://docs.fastf1.dev/api_reference/accounts_auth.html` (F1TV requirement)
- `theOehrly/Fast-F1` — `fastf1/livetiming/client.py`, `fastf1/internals/f1auth.py` (HEAD)
- Fast-F1 issue #753 — `https://github.com/theOehrly/Fast-F1/issues/753` (the 2025 auth change)
- `matteocelani/f1-telemetry` — `https://github.com/matteocelani/f1-telemetry` (HEAD, pushed
  2026-08-07): `apps/backend/src/services/f1-client.ts`, `apps/backend/src/services/payload-parser.ts`,
  `core/src/constants.ts`, `core/src/live-timing.ts`, `docs/live-timing-types.md`, `README.md`
- `Troftu/F1-SignalR` — `https://github.com/Troftu/F1-SignalR` (§2.3)
- `OpenF1.Data` (.NET) — `https://www.nuget.org/packages/OpenF1.Data/` (§2.3)
- F1 legal notices — `https://www.formula1.com/en/information/legal-notices` (§2.3, fetched
  2026-08-26)
- OpenF1 API docs — `https://openf1.org/` (§2.2)
- 2026 regulations / DRS removal — Motor Sport Magazine, `https://www.motorsportmagazine.com/articles/single-seaters/f1/how-f1-2026s-new-active-aero-will-work-without-drs/`;
  Motorsport.com, `https://www.motorsport.com/f1/news/how-f1s-new-active-aero-will-work-in-2026/10620106/`;
  Raceteq, `https://www.raceteq.com/articles/2024/04/everything-you-need-to-know-about-the-2026-f1-regulations`
