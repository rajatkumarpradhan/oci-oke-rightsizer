"""Load and validate the simplified workload/metrics export."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


class ModelError(ValueError):
    pass


@dataclass
class Workload:
    name: str
    namespace: str
    replicas: int
    cpu_request_m: int | None
    cpu_limit_m: int | None
    mem_request_mib: int | None
    mem_limit_mib: int | None
    cpu_usage_m: list[float]
    mem_usage_mib: list[float]
    anti_affinity: bool = False


@dataclass
class NodeShape:
    name: str
    cpu_m: int
    mem_mib: int
    cost_units: float  # relative synthetic cost per node, not a price


@dataclass
class Cluster:
    shape: NodeShape
    node_count: int
    workloads: list[Workload] = field(default_factory=list)
    reserve_fraction: float = 0.10  # kept free for kubelet/system pods


def _nonneg_int(v, what):
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ModelError(f"{what} must be a non-negative integer, got {v!r}")
    return v


def _samples(v, what):
    if not isinstance(v, list) or any(isinstance(x, bool) or not isinstance(x, (int, float)) or x < 0 for x in v):
        raise ModelError(f"{what} must be a list of non-negative numbers")
    return [float(x) for x in v]


def load(data: dict) -> Cluster:
    try:
        s = data["node_shape"]
        shape = NodeShape(s["name"], _nonneg_int(s["cpu_m"], "cpu_m"), _nonneg_int(s["mem_mib"], "mem_mib"),
                          float(s.get("cost_units", 1.0)))
        if shape.cpu_m <= 0 or shape.mem_mib <= 0:
            raise ModelError("node shape must have positive cpu and memory")
        nodes = _nonneg_int(data["node_count"], "node_count")
        reserve = float(data.get("reserve_fraction", 0.10))
        if not 0 <= reserve < 0.9:
            raise ModelError("reserve_fraction must be in [0, 0.9)")
        wls = []
        seen = set()
        for w in data["workloads"]:
            key = (w["namespace"], w["name"])
            if key in seen:
                raise ModelError(f"duplicate workload {key}")
            seen.add(key)
            reps = _nonneg_int(w.get("replicas", 1), "replicas")
            if reps < 1:
                raise ModelError(f"{key}: replicas must be >= 1")
            wl = Workload(w["name"], w["namespace"], reps,
                          _nonneg_int(w.get("cpu_request_m"), "cpu_request_m"),
                          _nonneg_int(w.get("cpu_limit_m"), "cpu_limit_m"),
                          _nonneg_int(w.get("mem_request_mib"), "mem_request_mib"),
                          _nonneg_int(w.get("mem_limit_mib"), "mem_limit_mib"),
                          _samples(w.get("cpu_usage_m", []), "cpu_usage_m"),
                          _samples(w.get("mem_usage_mib", []), "mem_usage_mib"),
                          bool(w.get("anti_affinity", False)))
            wls.append(wl)
    except (KeyError, TypeError) as e:
        raise ModelError(f"invalid model: {e!r}") from e
    return Cluster(shape, nodes, wls, reserve)


def load_file(path: str) -> Cluster:
    with open(path, encoding="utf-8") as f:
        return load(json.load(f))
