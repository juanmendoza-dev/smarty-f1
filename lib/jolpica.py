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


def _get(path, cache_dir):
    url = BASE + path
    return httpcache.cached_get_json(url, cache_dir)


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
    assert total == len(results), (
        f"qualifying total={total} but got {len(results)} rows for {season}/{round_}"
    )
    return results, meta


def sprint(season, round_, cache_dir):
    """Returns (results, meta). results == [] means no sprint entry (or not yet run)."""
    body, meta = _get(f"{season}/{round_}/sprint.json?limit=30", cache_dir)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        return [], meta
    results = races[0]["SprintResults"]
    total = int(body["MRData"]["total"])
    assert total == len(results), (
        f"sprint total={total} but got {len(results)} rows for {season}/{round_}"
    )
    return results, meta


def race_results(season, round_, cache_dir):
    """Returns (results, meta). results == [] means the race hasn't happened yet."""
    body, meta = _get(f"{season}/{round_}/results.json?limit=30", cache_dir)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        return [], meta
    results = races[0]["Results"]
    total = int(body["MRData"]["total"])
    assert total == len(results), (
        f"results total={total} but got {len(results)} rows for {season}/{round_}"
    )
    return results, meta


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
    assert total == len(races), (
        f"track history total={total} but got {len(races)} races for {driver_id}@{circuit_id}"
    )
    return races, meta
