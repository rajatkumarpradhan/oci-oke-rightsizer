"""Per-workload request recommendations.

Conservative rules:
- Fewer than MIN_SAMPLES samples: no recommendation (finding RS005 instead).
- CPU request = sizing p95 * headroom, rounded up to 10m. Headroom comes from the workload's
  class (steady 1.20, bursty 1.40, critical 1.50) or an explicit cpu_headroom (1.0 to 3.0).
- Peak-aware sizing: when samples carry days and at least MIN_DAYS days each have at least
  MIN_DAY_SAMPLES samples, the sizing p95 is the largest per-day p95, not the pooled p95, so a
  rare daily peak is not diluted by quiet days. The day that set it is reported.
  Without day information, or with too few usable days, the pooled p95 is used (unchanged).
- Memory request = max observed usage * MEM_HEADROOM, rounded up to 16MiB
  (memory is not compressible, so the max is used, not a percentile).
- A recommendation never goes below 10m CPU / 32MiB memory.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .model import HEADROOM_CLASSES, Workload
from .stats import percentile, round_up

MIN_SAMPLES = 48
MIN_DAYS = 3
MIN_DAY_SAMPLES = 12
CPU_HEADROOM = HEADROOM_CLASSES["steady"]
MEM_HEADROOM = 1.15
CPU_FLOOR_M = 10
MEM_FLOOR_MIB = 32


@dataclass
class Rec:
    cpu_request_m: int | None
    mem_request_mib: int | None
    cpu_p95_m: float | None      # the p95 the request was sized from (pooled, or the peak day's)
    mem_max_mib: float | None
    enough_data: bool
    pooled_p95_m: float | None = None
    peak_day: str | None = None
    peak_day_p95_m: float | None = None
    day_p95_m: dict | None = None
    peak_aware: bool = False
    headroom: float = CPU_HEADROOM


def daily_p95(w: Workload) -> dict[str, float]:
    """Per-day p95 for days with at least MIN_DAY_SAMPLES samples; {} if days are unknown."""
    if not w.sample_days or len(w.sample_days) != len(w.cpu_usage_m):
        return {}
    by_day: dict[str, list[float]] = defaultdict(list)
    for d, v in zip(w.sample_days, w.cpu_usage_m):
        by_day[d].append(v)
    return {d: percentile(v, 95) for d, v in sorted(by_day.items()) if len(v) >= MIN_DAY_SAMPLES}


def headroom_for(w: Workload) -> float:
    return w.cpu_headroom if w.cpu_headroom is not None else HEADROOM_CLASSES[w.headroom_class]


def recommend(w: Workload) -> Rec:
    ok_cpu = len(w.cpu_usage_m) >= MIN_SAMPLES
    ok_mem = len(w.mem_usage_mib) >= MIN_SAMPLES
    pooled = percentile(w.cpu_usage_m, 95) if ok_cpu else None
    mem_max = max(w.mem_usage_mib) if ok_mem else None
    hr = headroom_for(w)
    sizing, peak_day, peak_p95, days, aware = pooled, None, None, None, False
    if ok_cpu:
        days = daily_p95(w)
        if len(days) >= MIN_DAYS:
            aware = True
            top = max(days.values())
            peak_day = next(d for d in days if days[d] == top)
            peak_p95 = top
            sizing = max(pooled, top)
    cpu = max(CPU_FLOOR_M, round_up(sizing * hr, 10)) if ok_cpu else None
    mem = max(MEM_FLOOR_MIB, round_up(mem_max * MEM_HEADROOM, 16)) if ok_mem else None
    return Rec(cpu, mem, sizing, mem_max, ok_cpu and ok_mem, pooled, peak_day, peak_p95,
               days if aware else None, aware, hr)
