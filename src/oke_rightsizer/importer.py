"""Build a Cluster from a local CSV of per-pod usage samples (offline, no API calls).

CSV columns: timestamp,namespace,workload,pod,cpu_m,mem_mib
Workload settings (requests, limits, replicas) come from a small JSON "workloads" file
because usage CSVs do not carry them.

Per-pod samples are combined per timestamp: for each workload the sample series is the
mean across pods at that timestamp (one value per timestamp). Replicas default to the
number of distinct pods seen.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict

from .model import Cluster, ModelError, load

REQUIRED = ["timestamp", "namespace", "workload", "pod", "cpu_m", "mem_mib"]


def read_samples(path: str) -> dict:
    """Return {(namespace, workload): {"pods": set, "cpu": {ts: [..]}, "mem": {ts: [..]}}}."""
    out: dict = defaultdict(lambda: {"pods": set(), "cpu": defaultdict(list), "mem": defaultdict(list)})
    seen = set()
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (rd.fieldnames or [])]
        if missing:
            raise ModelError(f"CSV is missing columns: {', '.join(missing)}")
        for n, row in enumerate(rd, start=2):
            try:
                cpu = float(row["cpu_m"])
                mem = float(row["mem_mib"])
            except (TypeError, ValueError):
                raise ModelError(f"line {n}: cpu_m and mem_mib must be numbers") from None
            if cpu < 0 or mem < 0 or cpu != cpu or mem != mem:
                raise ModelError(f"line {n}: negative or NaN sample")
            for c in ("timestamp", "namespace", "workload", "pod"):
                if not (row[c] or "").strip():
                    raise ModelError(f"line {n}: empty {c}")
            key = (row["namespace"].strip(), row["workload"].strip())
            dup = (key, row["pod"].strip(), row["timestamp"].strip())
            if dup in seen:
                raise ModelError(f"line {n}: duplicate sample for {dup[1]} at {dup[2]}")
            seen.add(dup)
            d = out[key]
            d["pods"].add(row["pod"].strip())
            d["cpu"][row["timestamp"].strip()].append(cpu)
            d["mem"][row["timestamp"].strip()].append(mem)
    if not out:
        raise ModelError("CSV has no samples")
    return out


def from_csv(csv_path: str, config_path: str) -> Cluster:
    """config JSON: node_shape, node_count, optional reserve_fraction, and
    workloads: [{namespace,name,cpu_request_m,...}] (no usage lists)."""
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    samples = read_samples(csv_path)
    declared = {(w.get("namespace"), w.get("name")) for w in cfg.get("workloads", [])}
    unknown = sorted(set(samples) - declared)
    if unknown:
        raise ModelError("samples for workloads missing from config: " + ", ".join(f"{a}/{b}" for a, b in unknown))
    wls = []
    for w in cfg.get("workloads", []):
        key = (w.get("namespace"), w.get("name"))
        w = dict(w)
        s = samples.get(key)
        if s:
            ts = sorted(s["cpu"])
            w["cpu_usage_m"] = [sum(s["cpu"][t]) / len(s["cpu"][t]) for t in ts]
            w["mem_usage_mib"] = [sum(s["mem"][t]) / len(s["mem"][t]) for t in ts]
            w.setdefault("replicas", len(s["pods"]))
        else:
            w.setdefault("cpu_usage_m", [])
            w.setdefault("mem_usage_mib", [])
        wls.append(w)
    return load({**{k: v for k, v in cfg.items() if k != "workloads"}, "workloads": wls})
