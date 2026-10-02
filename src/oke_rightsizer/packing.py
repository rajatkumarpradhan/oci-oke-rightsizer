"""First-fit-decreasing node packing of pod requests (an estimate, not the scheduler)."""
from __future__ import annotations

from .model import Cluster


def allocatable(c: Cluster) -> tuple[int, int]:
    keep = 1.0 - c.reserve_fraction
    return int(c.shape.cpu_m * keep), int(c.shape.mem_mib * keep)


def pack(c: Cluster, pods: list[tuple[str, int, int, bool]]) -> int | None:
    """pods: (workload, cpu_m, mem_mib, anti_affinity). Returns nodes needed, or None if a pod can't fit any node.

    Anti-affinity pods of one workload never share a node. Ignores taints, topology,
    DaemonSets, PodDisruptionBudgets and node-pool constraints.
    """
    cap_cpu, cap_mem = allocatable(c)
    bins: list[dict] = []
    for name, cpu, mem, anti in sorted(pods, key=lambda p: (-(p[1] / cap_cpu + p[2] / cap_mem), p[0])):
        if cpu > cap_cpu or mem > cap_mem:
            return None
        for b in bins:
            if b["cpu"] + cpu <= cap_cpu and b["mem"] + mem <= cap_mem and not (anti and name in b["anti"]):
                b["cpu"] += cpu
                b["mem"] += mem
                if anti:
                    b["anti"].add(name)
                break
        else:
            bins.append({"cpu": cpu, "mem": mem, "anti": {name} if anti else set()})
    return len(bins)


def expand(c: Cluster, requests: dict[tuple[str, str], tuple[int, int]]):
    pods = []
    for w in c.workloads:
        cpu, mem = requests[(w.namespace, w.name)]
        pods += [(f"{w.namespace}/{w.name}", cpu, mem, w.anti_affinity)] * w.replicas
    return pods
