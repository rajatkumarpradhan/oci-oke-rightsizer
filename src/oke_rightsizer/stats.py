import math


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; p in (0, 100]."""
    if not values:
        raise ValueError("empty sample")
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    s = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(s)))
    return s[rank - 1]


def round_up(x: float, step: int) -> int:
    return int(math.ceil(x / step) * step)
