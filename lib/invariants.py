"""Runtime invariant checks that survive `python -O`.

The scoring pipeline leans hard on invariants -- market-blindness, sub-scores in
[0,1], probabilities summing to 1, p_win <= p_podium <= p_points, the Monte
Carlo/closed-form self-consistency check, and the leakage guards in snapshot.py.
Every one of those was originally a bare `assert`, which the interpreter strips
entirely under `python -O`. That is the wrong failure mode for checks whose whole
job is to stop a silently-wrong number reaching a snapshot or a Brier score, so
they go through require() instead and raise unconditionally.

Genuine programming-error checks (an unreachable branch, a type the caller
controls) can still use plain `assert`. The rule of thumb: if the check is
guarding *data* -- something an API, a snapshot file, or a calibration could
make false -- it belongs here.
"""


class InvariantError(AssertionError):
    """A pipeline invariant was violated.

    Subclasses AssertionError so anything that already catches AssertionError
    (tests written against the old bare asserts) keeps working unchanged.
    """


def require(condition, message):
    """Raise InvariantError(message) unless condition is truthy."""
    if not condition:
        raise InvariantError(message)
