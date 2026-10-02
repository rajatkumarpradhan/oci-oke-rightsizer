"""Per-workload request recommendations.

Conservative rules:
- Fewer than MIN_SAMPLES samples: no recommendation (finding RS005 instead).
- CPU request = p95 usage * CPU_HEADROOM, rounded up to 10m.
- Memory request = max observed usage * MEM_HEADROOM, rounded up to 16MiB
  (memory is not compressible, so the max is used, not a percentile).
- A recommendation never goes below 10m CPU / 32MiB memory.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Workload
from .stats import percentile, round_up

MIN_SAMPLES = 48
CPU_HEADROOM = 1.20
MEM_HEADROOM = 1.15
CPU_FLOOR_M = 10
MEM_FLOOR_MIB = 32


@dataclass
class Rec:
    cpu_request_m: int | None
    mem_request_mib: int | None
    cpu_p95_m: float | None
    mem_max_mib: float | None
    enough_data: bool


def recommend(w: Workload) -> Rec:
    ok_cpu = len(w.cpu_usage_m) >= MIN_SAMPLES
    ok_mem = len(w.mem_usage_mib) >= MIN_SAMPLES
    cpu_p95 = percentile(w.cpu_usage_m, 95) if ok_cpu else None
    mem_max = max(w.mem_usage_mib) if ok_mem else None
    cpu = max(CPU_FLOOR_M, round_up(cpu_p95 * CPU_HEADROOM, 10)) if ok_cpu else None
    mem = max(MEM_FLOOR_MIB, round_up(mem_max * MEM_HEADROOM, 16)) if ok_mem else None
    return Rec(cpu, mem, cpu_p95, mem_max, ok_cpu and ok_mem)
