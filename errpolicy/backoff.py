"""Deterministic, strictly increasing backoff sequences."""

DEFAULT_BASE = 1
DEFAULT_MULTIPLIER = 2


def wait_at(retry_index, base=DEFAULT_BASE, multiplier=DEFAULT_MULTIPLIER):
    """Wait seconds before retry number 'retry_index' (0-based).

    With the defaults this yields 1, 2, 4, 8, ... so waits before the
    first and second retries are 1s and 2s respectively.
    """
    if retry_index < 0:
        raise ValueError("retry_index must be >= 0")
    if base <= 0:
        raise ValueError("base must be positive")
    if multiplier <= 1:
        raise ValueError("multiplier must be > 1 so the sequence increases")
    return base * (multiplier ** retry_index)


def sequence(count, base=DEFAULT_BASE, multiplier=DEFAULT_MULTIPLIER):
    """Return the first 'count' waits, e.g. [1, 2, 4, 8]."""
    return [wait_at(i, base, multiplier) for i in range(count)]
