"""Jolpica (jolpica-f1) client. See 01-data-pipeline.md sec4.

No auth, no third-party deps. Every call goes through httpcache so repeat runs
don't re-hit the network and every response has a URL+timestamp provenance record.

Gotchas handled here (01-data-pipeline.md sec4.4, sec4.5):
- An empty Races/StandingsLists list means "hasn't happened yet", not an error.
  Callers get that back as an empty list/None and must decide what to do with it.
- All numeric fields arrive as strings; cast at the point of use, not here, so the
  raw snapshot provenance keeps exactly what the API returned.
- MRData.total counts result ROWS, not races. We assert list length against total
  wherever total should describe a single race's full classification.
"""

from . import httpcache
from .invariants import require

BASE = "https://api.jolpi.ca/ergast/f1/"


def _get(path, cache_dir, force_refresh=False):
    url = BASE + path
    return httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)


def schedule(season, cache_dir):
    body, meta = _get(f"{season}.json?limit=40", cache_dir)
    return body["MRData"]["RaceTable"]["Races"], meta


def race_info(season, round_, cache_dir):
    """Session dates/times for one round (quali/sprint/race, circuit)."""
    body, meta = _get(f"{season}/{round_}.json", cache_dir)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        raise ValueError(f"no schedule entry for {season}/{round_}")
    return races[0], meta


def circuit(circuit_id, cache_dir):
    body, meta = _get(f"circuits/{circuit_id}.json", cache_dir)
    circuits = body["MRData"]["CircuitTable"]["Circuits"]
    if not circuits:
        raise ValueError(f"unknown circuit id {circuit_id}")
    return circuits[0], meta


def qualifying(season, round_, cache_dir):
    """Returns (results, meta). results == [] means quali hasn't happened yet."""
    body, meta = _get(f"{season}/{round_}/qualifying.json?limit=30", cache_dir)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        return [], meta
    results = races[0]["QualifyingResults"]
    total = int(body["MRData"]["total"])
    require(total == len(results), (
        f"qualifying total={total} but got {len(results)} rows for {season}/{round_}"
    ))
    return results, meta


def sprint(season, round_, cache_dir):
    """Returns (results, meta). results == [] means no sprint entry (or not yet run)."""
    body, meta = _get(f"{season}/{round_}/sprint.json?limit=30", cache_dir)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        return [], meta
    results = races[0]["SprintResults"]
    total = int(body["MRData"]["total"])
    require(total == len(results), (
        f"sprint total={total} but got {len(results)} rows for {season}/{round_}"
    ))
    return results, meta


def race_results(season, round_, cache_dir, force_refresh=False):
    """Returns (results, meta). results == [] means the race hasn't happened yet.

    force_refresh exists for the stale-empty case: a response fetched before a
    race finished caches "no result" forever, and for a race that has since run
    that cached answer is simply wrong. Callers that can tell the race is over
    use it to re-ask rather than believe the cache. See find_full_result.
    """
    body, meta = _get(f"{season}/{round_}/results.json?limit=30", cache_dir,
                      force_refresh=force_refresh)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        return [], meta
    results = races[0]["Results"]
    total = int(body["MRData"]["total"])
    require(total == len(results), (
        f"results total={total} but got {len(results)} rows for {season}/{round_}"
    ))
    return results, meta


def season_results(season, cache_dir):
    """All of a season's race results, grouped by round, via the season-level
    endpoint paginated at its 100-row page cap -- not a loop over rounds.

    01-data-pipeline.md sec4.3: "prefer one filtered query over N per-entity
    queries." snapshot.build_form used to call race_results() once per prior
    round, which is fine for one race but means a Phase A3 backfill re-walks
    the same season's rounds from scratch for every race in it. A season's
    pages are cached by (season, offset) and so are fetched from the network
    at most once per season for the life of the cache, then reused by every
    race in that season -- backfill or single-race snapshot alike.

    Returns (results_by_round: {round_int: [Result, ...]}, metas: [meta, ...]).
    A round with no key hasn't happened yet. Verified live 2026-08-23: a
    22-round season returns total=440 result rows (20 cars/round) paginated
    5 pages deep at limit=100, even when a larger limit is requested -- the
    server silently caps the page size rather than erroring or honoring it.

    Two failure modes this guards against, both verified live against the
    2016 season (22 cars/round, so 100 does not divide evenly):
    - Pagination is by result ROW, not by race, so a round can straddle a
      page boundary and arrive split across two responses (2016 round 5: 12
      rows on the offset=0 page, 10 on offset=100). Rows are appended per
      round rather than overwritten, so a split round is reassembled instead
      of losing its first fragment.
    - `total` lives in every page's body, including the offset=0 page, which
      is cached like any other URL. For a season still in progress, a warm
      cache would keep answering with whatever `total` was true the first
      time that URL was fetched, silently truncating every later round --
      most of the scorer's weight computed on a stale season. Worse, the
      *previously-last* page is the same problem one level down: it was
      cached as a short, partial page back when it was the tail, and revisiting
      that same offset from cache would still show that short page even
      after the season grew past it. Page 0 is always force-refreshed for an
      accurate total; every other page is fetched cache-first but its row
      count is checked against what that fresh total says it should hold --
      a short page is a stale tail and gets force-refreshed too. A page that
      was already fully populated (an interior page, or a genuinely finished
      season's true tail) never mismatches, so a finished historical season
      costs exactly one refreshed call (page 0) no matter how many times
      this runs against it.
    """
    results_by_round = {}
    metas = []
    limit = 100

    def merge(races):
        n = 0
        for race in races:
            results_by_round.setdefault(int(race["round"]), []).extend(race["Results"])
            n += len(race["Results"])
        return n

    body, meta = _get(f"{season}/results.json?limit={limit}&offset=0", cache_dir, force_refresh=True)
    metas.append(meta)
    total = int(body["MRData"]["total"])
    seen = merge(body["MRData"]["RaceTable"]["Races"])

    offset = limit
    while offset < total:
        expected = min(limit, total - offset)
        body, meta = _get(f"{season}/results.json?limit={limit}&offset={offset}", cache_dir)
        got = sum(len(r["Results"]) for r in body["MRData"]["RaceTable"]["Races"])
        if got != expected:
            body, meta = _get(
                f"{season}/results.json?limit={limit}&offset={offset}", cache_dir, force_refresh=True
            )
        metas.append(meta)
        seen += merge(body["MRData"]["RaceTable"]["Races"])
        offset += limit

    require(seen == total, (
        f"season_results({season}) collected {seen} rows across "
        f"{len(metas)} page(s) but total={total}"
    ))
    for rnd, rows in results_by_round.items():
        positions = sorted(int(r["position"]) for r in rows)
        require(
            positions == list(range(1, len(rows) + 1)),
            f"season_results({season}) round {rnd}: positions {positions} aren't a "
            f"contiguous 1..{len(rows)} classification -- a split or corrupted page merge",
        )
    return results_by_round, metas


def driver_standings(season, cache_dir, round_=None, verify_round=None, max_round=None):
    """Standings after `round_`, or the latest available if round_ is None.

    verify_round: assert the StandingsList came back stamped with exactly this
    round. Ergast-style APIs answer an out-of-range round with the nearest
    available standings rather than an error, so asking for round 12 and being
    handed round 22's table is a silent, plausible, completely wrong answer --
    exactly the leakage an A3 backfill must not have.

    max_round: the looser check for a round_=None ("latest") pull, which is
    legitimately allowed to be stamped with the round being predicted when only
    that round's sprint has run (see snapshot.build_form). Asserts the stamp is
    at most max_round, which still catches a finished season's final table.

    Either check also puts the stamped round on the returned meta as
    "standings_round", so a snapshot records which table it actually got.
    """
    path = f"{season}/{round_}/driverstandings.json?limit=40" if round_ else (
        f"{season}/driverstandings.json?limit=40"
    )
    body, meta = _get(path, cache_dir)
    lists = body["MRData"]["StandingsTable"]["StandingsLists"]
    if not lists:
        return [], meta
    got = int(lists[0]["round"])
    meta = dict(meta)
    meta["standings_round"] = got

    if verify_round is not None:
        require(
            got == verify_round,
            f"driver_standings({season}, round_={round_}) returned standings after "
            f"round {got}, not the requested {verify_round}",
        )
    if max_round is not None:
        require(
            got <= max_round,
            f"driver_standings({season}, latest) returned standings stamped round {got}, "
            f"which is past round {max_round} -- these include the race being predicted",
        )
    return lists[0]["DriverStandings"], meta


def constructor_standings(season, cache_dir, round_=None):
    path = f"{season}/{round_}/constructorstandings.json?limit=40" if round_ else (
        f"{season}/constructorstandings.json?limit=40"
    )
    body, meta = _get(path, cache_dir)
    lists = body["MRData"]["StandingsTable"]["StandingsLists"]
    if not lists:
        return [], meta
    return lists[0]["ConstructorStandings"], meta


def driver_track_history(circuit_id, driver_id, cache_dir):
    """All of one driver's prior results at one circuit, oldest-to-newest as
    returned by the API (Jolpica paginates oldest-first, sec4.4 gotcha #2 --
    limit=100 is comfortably above any driver's lifetime start count at one track).
    """
    body, meta = _get(
        f"circuits/{circuit_id}/drivers/{driver_id}/results.json?limit=100", cache_dir
    )
    races = body["MRData"]["RaceTable"]["Races"]
    total = int(body["MRData"]["total"])
    require(total == len(races), (
        f"track history total={total} but got {len(races)} races for {driver_id}@{circuit_id}"
    ))
    return races, meta
