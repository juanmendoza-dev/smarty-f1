# 10 — Live Viewer / Debug UI (Lane B tooling)

Status: **specced 2026-08-27, not approved, not built — and deliberately not buildable yet.** The
tick format this renderer draws is `UNVERIFIED` in this project's own idiom: every `03` §§6–7 claim
about the wire stays unverified until `03` §13's acceptance run, which needs a live session (Monza
FP1, ~2026-09-04). Building a renderer against an unverified format means building against a format
that may not exist. §2 states the gate and what specifically has to come back from that run before
this is worth a line of code.

Read `welcome.md`, `03-live-telemetry-overtakes.md` (§4.2 the authorized scope, §7 the tick
contract, §8 degraded modes, §11.3 the storage/access rules, §16 item 4 the open visibility
question), `08-overtake-model.md` §13, `00-roadmap.md`'s Lane B phases, and `lib/livetiming_tick.py`
(the `CarState` / `Tick` fields this document renders) first.

**On the number.** This is `10`, not `09`. `09` is left free for the **live win-probability layer**
— `08` §12 item 5 / §13.5 name it as the next spec to be written and it is the load-bearing one;
this is tooling. The gap is deliberate, not a missing file.

---

## 1. What this is

A **local debug and analysis viewer** for Lane B's captures. It renders a `data/live/ticks/<slug>.jsonl`
file: a track map from the per-car X/Y, a timing-tower list of every car, and — on clicking a car —
that car's telemetry channels. Two modes: **replay** a finished capture (the primary use), and
**live-tail** a capture in progress.

It exists for one reason: **`03` §13's acceptance items and `08`'s labelling work are currently
checked by reading numbers.** `livetiming_verify.py` reports whether position data arrived; it
cannot show that the cars are in the right places on the right circuit. `08` §13.6 correction 1 —
ranking cars by integrated distance matched the official order 44.7% of the time and invented 828
phantom overtakes in one race — is exactly the class of bug that is invisible in a summary
statistic and obvious the instant you watch two markers move. That is the value being bought here:
a way to *look* at a capture.

It is not a product, not a dashboard for anyone else, and not part of the prediction pipeline. It
reads files and draws pixels.

**What it is explicitly not, restated because Lane B's history is that scope creeps through
plausible next steps:** not a live-prediction display (`03` §4.4 — see §14), not a broadcast
companion, not a hosted anything (`03` §4.2 item 2 / §11.3), and not a second consumer of the feed
(it never connects; see §3).

---

## 2. The gate: this gets built *after* Monza FP1, not before

**`03` §13's acceptance run is a hard prerequisite.** The reason is specific, not general caution.

| `03` §13 item | What it settles | What it does to this spec if it fails |
|---|---|---|
| **5 — was `Position.z` broadcast at all** | Whether X/Y exists in a live session under 2026 regs | **Go/no-go for the headline panel.** §8 records that *some* 2026 sessions have been observed not to broadcast GPS position at all. No X/Y, no track map — the viewer's main feature has no input and this spec needs redesigning around the timing tower, not just deferring |
| **2 — `.z` payloads decode as base64 + raw DEFLATE into §7.2's shapes** | Whether `x`/`y`/`speed`/… are populated at all | The tick's *fields* change. Every panel is keyed to those fields |
| **3 — `CarData` indices 0/2/3/4/5/45 present and in range** | Which telemetry channels the car panel can show | Panels drawn for channels that turn out to be absent or to mean something else |
| **1b — multi-message frames handled** | Whether the tick stream is complete under load | A viewer over a lossy tick stream shows gaps that are the client's, not the feed's, and will be misread as feed problems |

The `03` §8 measurement that position data was present for the 2026 Dutch GP came from the
**historical archive**, not the live wire, and `03` §8 already says one positive observation cannot
refute a claim about session-to-session variability. So the track map's input is, today,
genuinely not known to exist.

**The rule:** run `03` §13's acceptance capture, update `03` with the results, then re-read this
document against what came back and correct it before building. Anything in here that the run
contradicts is a spec bug to fix here first — the same discipline `03` §13 sets for itself.

There is one thing worth doing *before* that run, and it is the only thing: §10's circuit-outline
frame check can be prepared against the FastF1 archive so the first capture has something to be
drawn on. That is analysis, not renderer code.

---

## 3. Scope — what the viewer is allowed to be

Every constraint below is inherited, not invented here. They are restated because a UI is the most
natural place in this project for them to get quietly violated.

**Local only.** `03` §4.2 item 2 prohibits any hosted or public deployment — no VPS, no cloud
runner, no homelab service, no public dashboard, no shared WebSocket, no hosted demo. `03` §11.3
adds the sharper form: **the client exposes no socket, port, or HTTP endpoint, not even on
localhost.** The viewer inherits that unchanged, and it inherits the reason: the prior art's
architecture — a backend rebroadcasting the feed over WebSocket to browser clients — is exactly the
shape that got IP-blocked (`03` §2.3), and B0 does not reproduce it. A local web UI served on
`127.0.0.1` is that shape with the audience turned down, so it is out even though it would be
convenient.

**It never connects to F1.** The viewer's only inputs are files already on disk. It does not
negotiate, does not subscribe, does not open a socket to anything, and contains no import of
`lib/signalr.py` or `lib/livetiming_client.py`'s live path. `03` §6.5 item 1 allows exactly one
connection at a time and this tool holds zero of them — running the viewer while a capture is
running must be safe by construction, not by care.

**It is read-only against the capture.** It opens tick files for reading. It never writes, moves,
truncates, or rotates anything under `data/live/`, because the process that owns those files may be
appending to them right now.

**Zero budget** (`welcome.md`). Reuse `.venv312`. §4 picks a stack that adds no dependency at all.

**Redistribution.** `03` §11.2 — no capture, no tick file, and no per-tick or per-car derived series
is ever committed. §13 works out what that means for screenshots, which is not obvious and is the
one genuinely new question this document raises.

---

## 4. Stack decision — matplotlib on the `macosx` backend

### 4.1 The decision

**A single matplotlib figure, `macosx` backend, animated with `FuncAnimation` and driven by
`matplotlib.widgets`.** One script, `livetiming_view.py`, sibling to `livetiming_capture.py` and
`livetiming_verify.py`.

### 4.2 Why, measured rather than assumed

The owner has no UI experience, so the selection criterion is *time to a working window*, not
ceiling. Checked directly in `.venv312` on 2026-08-27:

| Check | Result |
|---|---|
| `matplotlib` present in `.venv312` | **Yes — 3.11.1**, already there as a FastF1 dependency |
| Default backend | **`macosx`** — a real interactive window, not `Agg` |
| `matplotlib.use("macosx")` | OK |
| `tkinter` | **Absent** (`No module named '_tkinter'`) — irrelevant, since `macosx` needs it not |
| `figure.canvas.supports_blit` | **True** — partial redraw is available |
| `Slider` / `Button` / `RadioButtons` / `FuncAnimation` import | OK |

That last set matters more than it looks: matplotlib's widgets are drawn *on axes*, not by a native
toolkit, so the scrub bar, the speed selector and the play/pause button in §9 come with the library
and are backend-independent. **Naming the backend is part of the stack decision** — "use matplotlib"
is not a complete statement for an interactive tool, and a spec that said only that could send a
builder into a day of diagnosing a silent `Agg` figure that never appears.

The rest of the case:

- **Zero new dependencies.** Nothing to install, nothing to approve, nothing to break `.venv312`
  which is also where the `08` model and the B0 client live.
- **Familiar.** It is the plotting library the owner will meet everywhere else in this project's ML
  work. A debug tool should not also be a new framework.
- **The panels are plots.** A track map is a scatter over a line; telemetry channels are time
  series; the timing tower is the only non-plot element and is a text block. This is the shape
  matplotlib is actually good at.
- **`pick_event` gives click-a-car** directly, which is the one interaction §7.3 needs.

### 4.3 What was rejected, and why

| Option | Why not |
|---|---|
| **Swift / SwiftUI / Xcode** | Ruled out by the owner's brief and correctly so: multi-week learning curve for a debug tool, platform lock, and a native app is a strange vehicle for something that reads a JSONL file. Not proportionate |
| **`textual` / `rich` TUI** | The strongest runner-up and genuinely nicer for the timing tower. It loses on the headline panel: a track map in a terminal is braille-cell or half-block art, at roughly 2×4 subpixels per character. That is enough to see a circuit shape and not enough to see *whether two cars are actually side by side* — which is the specific thing `08`'s labelling work needs to look at. It also adds a dependency and a second UI paradigm to learn |
| **`pygame`** | Best pixel control and a real event loop, but adds a dependency, brings no widgets (the §9 scrub bar and speed selector would be hand-built), and means learning a game loop to draw a scatter plot |
| **A local web UI (Flask/FastAPI + browser)** | Out on `03` §11.3, not on ergonomics — no HTTP endpoint, not even on localhost. Also the exact architecture `03` §2.3 records as having been IP-blocked, which is a bad shape to reproduce even with the network side removed |

### 4.4 The honest limits of the choice

Stated so the builder is not surprised, and so a future reader can tell a matplotlib limitation from
a bug:

- matplotlib is a plotting library with an event loop bolted on, not a GUI toolkit. Layout is
  `gridspec`, not a constraint system; nothing resizes intelligently.
- The redraw budget is the real constraint. §11 sets it at **10 fps** with blitting, which is
  comfortable for ~22 markers and a few text rows and is *deliberately below* the capture's 1 Hz
  tick rate anyway — see §5.
- Click hit-testing via `pick_event` is coarse. Two cars nose-to-tail may be one pick target; §7.3
  specs the fallback (cycle on repeated clicks, plus keyboard selection by position).
- The `macosx` backend has historically been the finickiest for blitting. If blitting misbehaves,
  **fall back to a full redraw at a lower rate rather than chasing it** — this is a debug tool and 5
  fps of correct drawing beats 30 fps of artifacts.

---

## 5. What it reads, and the temporal resolution it actually has

### 5.1 Inputs

| Path | Role | Access |
|---|---|---|
| `data/live/ticks/<slug>.jsonl` | **The only data input.** One JSON object per line, the `03` §7.1 record as serialized by `Capture.tick` (`lib/livetiming_client.py:100`) | read-only |
| `data/live/logs/<slug>.log` | Optional context panel: connects, reconnects, backoff, degraded transitions, stop-class exits | read-only |
| `data/live/raw/<slug>/*.jsonl` | **Not read by the viewer.** The raw frames are the client's input, not this tool's. Anything the viewer wants from them is obtained by re-running `run_replay` to produce a richer tick file (§5.2) | — |

The on-disk tick shape, as actually written today — this is the contract the renderer binds to, and
it is `03` §7.1's record with `degraded` as a sorted list and `cars` as a plain object keyed by FIA
three-letter code:

```
{ session_key, t_feed, t_local, t_wall, track_status, lap_current, lap_total,
  degraded: [...], gap_after_reconnect, stale,
  cars: { "VER": { code, racing_number, position, gap_leader, gap_ahead,
                   catching_ahead, in_pit, pit_out, retired, stopped, laps,
                   x, y, speed, throttle, brake, gear, rpm, aero_raw }, ... } }
```

### 5.2 The tick rate is a capture parameter, not a rendering parameter

This is the single most misleading thing about the tick file and it needs stating before anyone
draws a frame.

`livetiming_capture.py --tick-interval` defaults to **1.0 s**. The feed's own sample rate is
**~4.15 Hz** (`03` §13 item 6, measured from the 2026 R12 archive: car data 4.17 Hz, position
4.15 Hz). So a default capture's tick file holds **one snapshot per second of a stream that arrived
four times faster.** The viewer renders ticks. Therefore the map moves at 1 Hz, and at ~300 km/h a
car covers ~83 m between consecutive markers.

The supported way to get finer resolution is **not** in the viewer:

```
.venv312/bin/python livetiming_capture.py --replay data/live/raw/<slug>/<file>.jsonl \
    --tick-interval 0.25
```

`LiveTimingClient.run_replay` re-folds the raw frames through the same `TickAssembler` at whatever
interval is asked for, producing a denser tick file the viewer then reads unchanged. Resolution is
bought at capture/replay time, by the component that owns the assembler.

**Interpolation is prohibited, and this is not a style preference.** `03` §9.4 rule 1 and `03` §12
assertion 7 forbid interpolating, extrapolating, or forward-filling telemetry across a gap; the
`lib/livetiming_tick.py` docstring makes it a *structural* claim — "there is no interpolation /
extrapolation / forward-fill code path anywhere in this file." A viewer that tweens marker positions
between ticks to make motion look smooth reintroduces exactly that, one layer up, in the component a
human is looking at while forming beliefs about the data. So:

- **No tweening, no easing, no smoothing, no trailing-average of any displayed value.** A marker
  jumps from tick to tick. That jump is the data.
- A **breadcrumb trail** of the last N *actual* tick positions is allowed and useful, because every
  point in it is a real sample. A drawn curve *through* those points is not, because the curve is
  invented.
- No displayed number is ever a value from a previous tick. See §8.

---

## 6. The two modes

One script, one flag. Replay is the primary mode and the one to build first.

```
.venv312/bin/python livetiming_view.py --session <slug>              # replay (default)
.venv312/bin/python livetiming_view.py --session <slug> --follow     # live-tail
.venv312/bin/python livetiming_view.py --ticks path/to/file.jsonl    # explicit path
```

`--session <slug>` derives the tick and log paths under `--root` (default `data/live`), matching
`livetiming_verify.py`'s existing `--session` convention so the two tools take the same argument.

### 6.1 Replay — the primary mode

Load the whole tick file into memory at start. A season of practice sessions at 1 Hz is tens of
thousands of ticks; even a 0.25 s replay of a two-hour race is ~29,000 ticks of ~22 cars, which is
small. Loading everything is what makes §9's scrub bar instant, and instant scrubbing is most of the
tool's debug value.

Ticks are indexed by file order. **File order, not `t_feed`**: `03` §12 assertion 5 allows `t_feed`
to go backwards across a reconnect gap, and the viewer must not reorder or deduplicate what the
client wrote. Its job is to show what is in the file.

**`t_wall` is a literal string in replay-generated files.** `_replay_emit` (`lib/livetiming_client.py:389`)
writes `t_wall="(replay)"`, and the slug falls back to `"replay"` when the raw frames carry no
`SessionInfo`. Any wall-clock display, and any wall-clock delta arithmetic, must detect this and show
"replay — no wall clock" rather than attempting to parse it. This is a real trap: it is a string
where an ISO 8601 timestamp is expected, and it will only appear in replay, which is the mode
everything gets developed in.

### 6.2 Live-tail — following a capture in progress

Poll the tick file for appended lines (stat for size change, read forward from the last offset).
`Capture.tick` opens the file in append mode and flushes after every line
(`lib/livetiming_client.py:100–109`), so tailing is well-defined and no coordination with the
capture process is needed.

Rules:

1. **A partial trailing line is normal.** A read can land mid-write. Skip an unparseable trailing
   line, keep the offset before it, and retry on the next poll. Never halt, and never `require()` on
   it — this is the one place in Lane B where an incomplete record is expected rather than
   suspicious.
2. **Poll at the display rate, not faster.** 10 Hz of `stat` on a local file is free; there is no
   reason to go tighter and no benefit, since ticks arrive at 1 Hz.
3. **Silence has three different causes and the viewer must not conflate them.** This is `03` §8's
   report-don't-infer rule applied to the viewer's own situation:

   | Observation | Cause | Display |
   |---|---|---|
   | Tick file stops growing; a *different* slug's tick file has just appeared under `data/live/ticks/` | Session rotation — `_rotate_session` (`client.py:293`) starts a new `Capture` on a `SessionInfo.Path` change (`03` §9.5) | "session changed → `<new-slug>`", offer to follow it |
   | Tick file stops growing; the run log's last line is a stop-class exit or a clean disconnect | The capture process ended | "capture ended: `<last log line>`" |
   | Tick file stops growing; log still being written, or nothing conclusive | Genuinely unknown | "no new ticks for `<n>`s" — and nothing more. Do not guess |

4. **Never start, stop, restart or signal the capture process.** The viewer has no control surface
   over the client. A live capture is a `03` §6.5 connection under an accepted-risk regime and the
   only thing that manages it is the person who started it.

---

## 7. Layout and panels

Layout inspiration is taken from **`slowlydev/f1-dash`** — the same project `03` §6.4 already reads
as a primary source for the wire protocol, and the closest existing thing to this tool. What
transfers is *visual*: the track map with cars as coloured markers beside a vertical timing tower,
a selected-driver detail strip, session state along the top. What does **not** transfer is any of
its architecture — it is a hosted web app with a Rust backend rebroadcasting the feed over
WebSockets, which is the shape `03` §11.3 rules out and `03` §2.3 records as having been IP-blocked.
Take the layout, take nothing else.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ SESSION BAR  slug · lap 12/53 · GREEN · t_feed · t_wall · [STALE] [GAP]  │  §7.4
├───────────────────────────────────────┬──────────────────────────────────┤
│                                       │  TIMING TOWER              §7.2  │
│         TRACK MAP            §7.1     │  P  DRV   GAP     INT   FLAGS    │
│                                       │  1  VER   —       —              │
│      (circuit outline + 20 markers,   │  2  NOR   +1.204  +1.204  ▲      │
│       selected car highlighted)       │  3  LEC   +3.9    +2.7   PIT     │
│                                       │  …                               │
│                                       │  ── no position fix ─────────    │
│                                       │  ── retired ────────────────     │
├───────────────────────────────────────┴──────────────────────────────────┤
│ SELECTED CAR: VER  #1                                              §7.3  │
│ speed 312 km/h · throttle 100 · brake — · gear 8 · rpm 11,940 · ch45 0    │
│ [speed / throttle / brake traces over the last N ticks]                   │
├──────────────────────────────────────────────────────────────────────────┤
│ ◀◀  ▶/❚❚  ▶▶   [═══════════◆═════════════]  0.25× 1× 2× 4× max      §9  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 7.1 Track map

- **Input:** `CarState.x`, `CarState.y` (`03` §7.1 — `Position.z` coordinates, integers).
- One marker per car, at the tick's X/Y. Team colours are not available from the tick (`DriverList`
  carries them but `03` §7's tick does not retain them); use a stable per-code colour assignment and
  label markers with the three-letter code.
- **`Z` is ignored** — `03` §7.2 notes it is altitude and broadcast as 0, and the tick does not carry
  it.
- Optional breadcrumb: the last N real tick positions per car, as discrete points (§5.2).
- The selected car (§7.3) is drawn larger / ringed.
- Cars with no position fix are **not drawn at a fallback location**. They move to the tower's
  "no position fix" group (§7.2, §8).
- Circuit outline: §10.

### 7.2 Timing tower

One row per car, ordered by `CarState.position`, keyed to `03` §7.1's `CarState` fields:

| Column | Field | Rendering rule |
|---|---|---|
| P | `position` | `—` when `None` |
| DRV | `code` | The canonical key (`01` §8.2). `racing_number` shown in the detail panel only |
| GAP | `gap_leader` | **Verbatim string, printed as-is** |
| INT | `gap_ahead` | **Verbatim string, printed as-is** |
| ▲ | `catching_ahead` | Arrow when `True`, blank when `False`, `—` when `None` |
| FLAGS | `in_pit`, `pit_out`, `retired`, `stopped`, `laps` | Badges; `retired`/`stopped` are latched (`03` §7.4) and never clear |

**The gap fields stay strings and are never parsed.** `03` §7.1 carries them verbatim precisely
because the format is context-dependent (`"+1.234"`, `"1L"`, `""`, lap-down markers) and B0's job is
to expose what the feed said. `08` §13.6 correction 3 is the specific trap: **`LAP n` interval values
are not "lapped cars" — 72% of them occur at `Position == 1`, the leader, who has no car ahead** —
and the exact semantics remain `UNVERIFIED`. So the viewer must not coerce a gap to a float in order
to sort rows, colour a row by closeness, scale a bar, or decide that two cars are "in DRS range"
(which does not exist in 2026 anyway, `03` §7.3).

If a closeness cue is wanted, **`catching_ahead` is the one the feed actually provides** and it is a
boolean. Use it; do not reinvent it from a string whose meaning this project has already been wrong
about once.

Rows are grouped, in this order: running cars by position, then **no position fix**, then
**retired/stopped**. A latched-terminal car stays visible — disappearing cars make a session harder
to read, not easier.

### 7.3 Selected-car panel

Click a marker on the map (`pick_event`) or a tower row to select. Repeated clicks on overlapping
markers cycle through the candidates under the cursor; `↑`/`↓` select by tower position, so a car
is always reachable when hit-testing is ambiguous (§4.4).

Shows, from `03` §7.1's `CarState`:

- `code` and `racing_number` (provenance, `03` §7.1).
- **Instantaneous:** `speed` (km/h), `throttle` (0–110, see below), `brake` (0 or 100), `gear`
  (0–8), `rpm`.
- **Traces:** `speed`, `throttle`, `brake` over the last N ticks — discrete markers or a step plot,
  never a smoothed curve (§5.2). A gap in the trace is drawn as a gap.
- **`aero_raw`:** shown as a **raw integer with no name and no interpretation**, labelled `ch45`.
  `03` §7.3 is emphatic: it is not DRS (DRS does not exist in 2026), the encoding is not established,
  and it was **measured constant zero across 944,196 samples of the full 2026 Dutch GP**. The viewer
  displays it for the one purpose `03` §7.3 leaves it: as a drift tripwire, so that a non-zero value
  is visible to a human the first time it appears. Do not label it "DRS", do not map it to an enum,
  do not colour it.
- **`throttle` above 100 is normal and must not be clipped.** `03` §12.2: 97,218 samples — 10.3% of
  a race — exceed 100, observed max 104, which is why the asserted domain is [0, 110]. An axis
  clamped to 100 would silently hide a tenth of the channel's range.

### 7.4 Session bar

`session_key` (slug), `lap_current`/`lap_total`, `track_status` decoded to a label and a colour, and
both clocks: `t_feed` and `t_wall` (or "replay — no wall clock", §6.1).

`track_status` codes are `03` §7.1's set — 1 clear, 2 yellow, 4 SC, 5 red, 6 VSC, 7 VSC ending — and
`03` §12 assertion 3 halts on anything else. The viewer does not halt on an unexpected code; it
displays it **as the raw integer with a "unknown status code" label**, because a viewer that crashes
on a surprise is a worse debug tool than one that shows you the surprise. This is the one deliberate
divergence from `03` §10's fail-loud rule, and it is justified by the viewer being downstream of the
component that already halted: if an unknown code reached the tick file, the client's own assertion
is what should have fired, and the viewer's job is to help work out why it did not.

Two flags get their own prominent indicators because they change what the data *means*:

- **`stale`** (`03` §9.4 rule 4 — newest input older than 5 s): a persistent banner. `03` §12
  assertion 8 forbids any consumer predicting from a stale tick; a human reading the screen is a
  consumer, so they get told.
- **`gap_after_reconnect`** (`03` §9.4 rule 2): marked on the tick where it appears, and **marked
  permanently on the §9 scrub bar** so the seam is findable later. This is one of the most useful
  things the tool can show — it is the exact moment where a naive analysis would smooth across
  missing samples.

---

## 8. Degraded state, shown honestly

`03` §8's rules are the whole reason this section exists, and the failure they prevent is a viewer
that looks confident about a car it knows nothing about. The rule inherited verbatim: **missing is
never zero**, and **report, don't infer**.

**There are two different causes of a missing value and they must not be collapsed.** This is easy
to get wrong because they produce the identical field value:

| Cause | How to tell | What it means | Display |
|---|---|---|---|
| **Channel degraded** | `"position"` or `"cardata"` in the tick's `degraded` list | The channel produced no sample in this window at all — `_build_car` blanks it for **every** car (`lib/livetiming_tick.py:251–253`) | A session-level indicator: "POSITION DEGRADED" / "CAR DATA DEGRADED". The whole map or the whole telemetry panel is marked out |
| **Per-car absence** | Field is `None` while the channel is **not** in `degraded` | The channel is flowing but this car has no fix / no sample | Per-car only: that car moves to the tower's "no position fix" group, or its telemetry fields read `—` |

Collapsing them would report a feed-wide outage as twenty individual car problems, or vice versa —
which is precisely the inference `03` §8 rule 2 prohibits.

The display rules:

1. **A `None` renders as `—`, never as `0`, never as blank, and never as the previous tick's value.**
   `03` §8 rule 1: a brake value of `0` means off the brakes; a brake value of `None` means the feed
   did not say. If those look the same on screen the tool is lying. Give them visibly different
   glyphs, not just different numbers.
2. **A car with `x`/`y` of `None` is not drawn on the map.** Not at the origin, not greyed at its
   last-known position, not anywhere. `03` §12 assertion 10 is explicit that these are `None` and
   never `0` and never carried over; drawing a marker at a remembered location is that same
   carried-over value with the number hidden behind a pixel.
3. **Degraded transitions are visible in time, not just in the moment.** Mark them on the scrub bar
   alongside the reconnect gaps (§9), so "when did position data drop out" is answerable by looking
   rather than by grepping. `03` §8 rule 4 logs these once per transition; the viewer shows the same
   events on a timeline.
4. **The `stale` flag greys the entire data area**, not one field. A stale tick is not partially
   trustworthy.
5. **`gear` may be `None` on individual samples and that is expected, not a fault.** `03` §12.2:
   out-of-range gears are sparse feed corruption — 82 samples, 0.0087% of a race, roughly 1 in
   11,500 — and the specced disposition is to null the field, not to halt. A `—` in the gear readout
   once every few minutes is the system working correctly.

---

## 9. Replay controls

Replay is the primary use, so the controls are a first-class part of the spec rather than a
convenience.

| Control | Behaviour |
|---|---|
| **Play / pause** | Space bar and a button |
| **Speed** | `0.25× / 1× / 2× / 4× / max`, as `RadioButtons`. `1×` means one tick-interval of session time per real second — and since the tick interval is a capture parameter (§5.2), the bar shows the file's actual median tick spacing so "1×" is not a claim the tool cannot back up. `max` renders as fast as the frame budget allows |
| **Scrub** | A `Slider` over tick index, 0 to N−1. Dragging seeks. Because the whole file is in memory (§6.1), seeking is instant and that is the point |
| **Step** | `←` / `→` step one tick; `Shift` + arrow steps ten. Frame-stepping a pass is the core debug interaction — `08` §13.6's phantom-overtake class of bug is found this way |
| **Jump to event** | `[` / `]` jump to the previous/next **marked event**: a `gap_after_reconnect` tick, a `degraded` transition, a `track_status` change, or a `retired`/`stopped` latch. This is the highest-value control in the tool — it turns "find the reconnect seam in 29,000 ticks" into a keypress |
| **Follow selection** | Toggle: keep the map centred/zoomed on the selected car, or show the whole circuit |

**The scrub bar doubles as a timeline.** Above the slider, mark every event `[`/`]` can jump to,
using distinct glyphs for reconnect gaps, degraded windows, and non-green track status. A capture's
shape — where it dropped out, where the safety car was, where position data went missing — should be
readable from that strip alone before pressing play.

In `--follow` mode the controls collapse: play/pause and speed are meaningless against a live tail,
so the transport shows "LIVE" and only the scrub bar remains active, seeking backwards into what has
already been captured. Scrubbing back and then returning to the head is explicitly supported —
"what just happened" is exactly what a person watching a live session wants.

---

## 10. The circuit outline

The map needs something to draw cars on. Two sources, in this order.

### 10.1 v1 default — the swept envelope from the capture itself

Accumulate every `(x, y)` in the tick file and plot them as a faint point cloud, or their
alpha-shape/convex outline, underneath the live markers.

This is the default because it **cannot be wrong**: it is in the capture's own coordinate frame by
construction, needs no external data, needs no scaling, and requires no assumption about what the
numbers mean. A practice session's worth of samples across 20 cars traces the racing line, the pit
lane, and the run-off excursions — which is more informative for debugging than a clean outline
would be, because it shows where cars actually went.

It has one real weakness worth stating: in **live-tail** mode at the start of a session there is no
envelope yet, so the map starts empty and fills in. Acceptable. The alternative is drawing a
possibly-wrong outline immediately, which is worse.

### 10.2 Optional — a FastF1-derived outline, gated on a frame check

FastF1 is already in `.venv312` and the archive is already Lane B's offline corpus (`08` §13.4). A
circuit outline can be taken from a fast lap's X/Y trace, with `get_circuit_info()` supplying corner
numbers and marshal sectors — which would make the map substantially more readable, since "car is at
turn 4" is what a person actually wants to know.

**This is gated on a coordinate-frame check that has not been run, and the obvious version of that
check is circular.** `03` §8's measured X range for the 2026 Dutch GP — −1,015 to 8,703 — came from
the **archive**, via FastF1's own parser. Comparing an archive-derived outline against archive-derived
positions establishes nothing about the live wire.

**The check that settles it:** take the X/Y range and shape from `03` §13's *live acceptance capture*
and compare it against the archive envelope for the same circuit. Same frame, same scale, or not.
Until that comparison exists, §10.1 is the only outline drawn — and if the frames turn out to differ,
that is a finding worth recording in `03` §13, not a transform to quietly apply. Filed as §16 item 1.

---

## 11. Performance and interaction budget

- **Target 10 fps** with blitting; fall back to full redraw at 5 fps if the `macosx` backend
  misbehaves (§4.4). Both are far above the 1 Hz the default tick file actually carries.
- **Redraw only what changed.** Markers, tower text, and the traces are the animated artists; the
  circuit outline, axes and widgets are drawn once.
- **`max` replay speed must not be limited by rendering.** Render every k-th tick when the frame
  budget is exceeded, and **say so on screen** ("showing 1 of 4 ticks") rather than silently
  dropping frames. A viewer that quietly skips ticks in fast-forward is a viewer that can hide the
  event you are looking for.
- Memory: the whole tick file in memory (§6.1). A 4 Hz two-hour race is ~29,000 ticks × ~22 cars ×
  ~18 fields — tens of megabytes as plain Python objects, which is fine on the owner's machine and
  not worth optimising until it is not.

---

## 12. Required assertions

Same convention as the rest of the project: `lib.invariants.require`, not bare `assert`
(`03` §10). Fewer than `03` §12's fifteen, because the viewer guards *display*, not data — but the
ones that exist are the ones that would let it show something untrue.

1. The viewer opens **no network connection of any kind**. Structurally: no import of
   `lib/signalr.py`, and no import of `lib/livetiming_client.py`'s live path. (`03` §4.2, §11.3.)
2. The viewer **binds no socket or port**, on localhost or anywhere else. (`03` §11.3.)
3. The viewer **never writes to, moves, truncates, or rotates any file the capture owns** — anything
   under `data/live/raw/`, `data/live/ticks/`, or `data/live/logs/` — and opens all of them
   read-only. Any file the viewer itself ever produces (§13) goes under `data/live/viewer/`, which
   is covered by the same `.gitignore:16` line and is not a path the client touches. (§3.)
4. **No displayed value is interpolated, extrapolated, smoothed, or forward-filled** from another
   tick. Structurally, as in `lib/livetiming_tick.py`: there is no such code path. (`03` §9.4,
   `03` §12 assertion 7, §5.2.)
5. **A `None` field never renders as `0`, as blank, or as a previous tick's value.** (`03` §8 rule 1,
   `03` §12 assertion 10, §8.)
6. **A car with `x`/`y` of `None` is never plotted at any coordinate.** (§8 rule 2.)
7. **`gap_leader` and `gap_ahead` are never parsed to a number**, for any purpose including sorting,
   colouring, or scaling. (`03` §7.1, `08` §13.6 correction 3, §7.2.)
8. **A degraded channel is reported at session level and a per-car `None` at car level**, never
   interchanged. (`03` §8 rule 2, §8.)
9. **`aero_raw` is displayed as an unlabelled raw integer** — no enum, no "DRS", no derived state.
   (`03` §7.3.)
10. **No prediction of any kind is displayed in `--follow` mode.** (`03` §4.4; see §14.)
11. **No file the viewer produces is tracked by git.** (`03` §11.2, §13.)

---

## 13. Screenshots are data — and this lane's publication question is still open

This is the one genuinely new question this document raises, and it needs flagging rather than
deciding.

**`03` §16 item 4 — whether Lane B appears in the portfolio / LinkedIn writeup at all — is open.**
`welcome.md` says the project exists partly to be shown; `03` §5 consequence 2 says nothing about
this lane is published without a separate decision; and `03` §2.3 records that the one documented
enforcement action was against the most *visible* instance of this behaviour. The options `03` §16
lists run from "omit Lane B entirely" through "describe the architecture without naming the
endpoint" to "write it up in full."

**This spec builds a tool. It does not assume, enable, or pre-empt that decision** — and it is worth
being explicit that a viewer makes the decision *easier to violate by accident*, because a
screenshot is the most natural thing in the world to paste into a writeup.

The consequence, which follows from rules already in force rather than from a new judgement:

- **A screenshot of this viewer is F1 live timing data in image form.** It shows per-car positions,
  gaps, and telemetry from a specific session. `03` §11.2 prohibits committing "no raw capture, no
  parsed tick file, and no per-tick or per-car derived series … not sampled, not truncated, not
  'just one file as an example.'" A screenshot is a sampled, truncated per-car series that has been
  rendered. It falls inside that rule.
- **So: no screenshot, recording, or exported frame from a real capture goes into the repo, the
  README, or any writeup, until `03` §16 item 4 is decided.** Not as an illustration, not in a
  docs folder, not in a commit message.
- **The viewer writes no files by default.** If a screenshot/export feature is ever added, it writes
  under `data/live/viewer/` and nowhere else — already gitignored by `.gitignore:16`, and a path
  the capture client does not own, so §12 assertion 3 is satisfied as well as §12 assertion 11.
- What *may* be committed from this lane is unchanged (`03` §11.2): code, specs, and fitted model
  artifacts. This document is one of those.

If `03` §16 item 4 is later decided in favour of publishing, the way to illustrate this tool without
reopening it is a screenshot driven by **synthetic ticks** — hand-written in the `03` §7.2 shape, the
same convention `03` §11.2 already sets for test fixtures and that `test_livetiming.py` and
`test_overtakes.py` follow. That is a note for then, not permission now.

---

## 14. Deliberately not in v1

Listed so a future reader can tell "not built yet" from "decided against":

- **Any prediction overlay.** The reasoning is worth recording, not just the conclusion, because the
  two modes fall on opposite sides of a real line. Rendering `08`'s overtake probability over a
  **replay** of a captured session is squarely `03` §4.2 item 4 — "developing and evaluating an
  overtake model offline, against recorded sessions" — and would be genuinely useful for
  validating the labeller. Rendering it in **`--follow`** mode is running the model against a live
  feed, which `03` §4.4 gates on B1. So the rule when this is eventually built is: **replay yes,
  live no**, enforced as §12 assertion 10 rather than as care. Deferred from v1 because `08` §13's
  model consumes archive-derived features, not `03` §7 ticks, and wiring that up is its own piece of
  work.
- **Corner geometry, braking zones, sector overlays.** `03` §14 defers these; the viewer does not
  get to introduce them through the back door by drawing them. Corner *numbers* from FastF1's
  `get_circuit_info()` are labels, not geometry, and are allowed once §10.2's frame check passes.
- **Any market, odds, or trading display.** `03` §4.2 item 1 / §4.3's interlock.
- **Team colours, driver photos, tyre compounds.** `03` §6.3 does not subscribe `TimingAppData`, so
  compound is not in the tick; the rest is presentation the tick does not carry.
- **Video sync against the broadcast.** That is B1's measurement (`03` §13 item 8), and B1 is a
  measurement, not a UI feature.
- **Multi-session comparison**, diffing two captures side by side. Plausible later; not v1.
- **Reading `data/live/raw/`.** §5.1 — resolution is bought via `run_replay`, not by the viewer
  growing a second parser. Two implementations of the `03` §7.2 decode path is exactly how they
  drift apart.

---

## 15. What this changes in other docs

- `00-roadmap.md` **Lane B**: add the viewer as tooling under B0/B2 — specced, gated on `03` §13's
  acceptance run, not part of the B0 client. It is not a new phase; it does not gate anything and
  nothing gates on it except `03` §13.
- `welcome.md`: add a pointer line for this document in the "where to go next" list, in the same
  form as the others, noting it is tooling and gated.
- `03-live-telemetry-overtakes.md`: no change required. This document is downstream of it and
  restates rather than amends. If §10.2's frame check runs, its **result** belongs in `03` §13's
  results, not here.
- `.gitignore`: no change. `data/live/` (`.gitignore:16`) already covers everything §13 needs it to.

---

## 16. Open items

1. **Do the live X/Y coordinates share a frame with FastF1's archive positions?** (§10.2.) Not
   answerable from the archive alone — the obvious comparison is circular. Settled by comparing
   `03` §13's acceptance capture against the archive envelope for the same circuit. Blocks the
   corner-labelled outline; blocks nothing else.
2. **What tick interval should the standard replay use for viewing?** (§5.2.) The capture default is
   1.0 s and the feed is ~4.15 Hz. 0.25 s is the natural choice, but it quadruples the tick file
   and nobody has yet looked at whether 1 Hz is actually too coarse to see an overtake. Answerable
   the first time a real capture is watched; not worth deciding in advance.
3. **`03` §16 item 4 — does Lane B appear in the writeup at all?** Unchanged and still the owner's.
   Restated here only because §13 shows this tool makes it easier to violate by accident. This
   document does not move it.
