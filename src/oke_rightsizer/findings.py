"""Findings. These are review priorities from sampled data, not scheduler guarantees."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import packing, recommend
from .model import Cluster

SEV = {"high": 0, "medium": 1, "low": 2}
OVER_RATIO = 0.60      # recommended < 60% of current request => over-provisioned
LIMIT_RISK = 0.95      # max usage >= 95% of limit => risk
PEAK_RATIO = 1.10      # peak day p95 at least 10% above pooled p95 => RS009


@dataclass
class Finding:
    rule: str
    severity: str
    subject: str
    message: str
    evidence: dict

    def to_dict(self):
        return asdict(self)


def analyze(c: Cluster):
    out: list[Finding] = []
    new_req: dict[tuple[str, str], tuple[int, int]] = {}
    cur_req: dict[tuple[str, str], tuple[int, int]] = {}
    complete = True
    for w in c.workloads:
        sub = f"{w.namespace}/{w.name}"
        rec = recommend.recommend(w)
        if w.cpu_request_m is None or w.mem_request_mib is None:
            out.append(Finding("RS006", "medium", sub,
                               "Missing CPU or memory request: scheduling and packing estimates are unreliable.",
                               {"cpu_request_m": w.cpu_request_m, "mem_request_mib": w.mem_request_mib}))
            complete = False
        if not rec.enough_data:
            out.append(Finding("RS005", "low", sub,
                               f"Fewer than {recommend.MIN_SAMPLES} samples; no recommendation made.",
                               {"cpu_samples": len(w.cpu_usage_m), "mem_samples": len(w.mem_usage_mib)}))
            complete = False
        if w.cpu_request_m is not None and w.mem_request_mib is not None:
            cur_req[(w.namespace, w.name)] = (w.cpu_request_m, w.mem_request_mib)
        if rec.enough_data and w.cpu_request_m is not None and w.mem_request_mib is not None:
            if rec.cpu_request_m < OVER_RATIO * w.cpu_request_m:
                out.append(Finding("RS001", "medium", sub,
                                   f"CPU request {w.cpu_request_m}m looks high; p95 usage is {rec.cpu_p95_m:.0f}m. Suggested request {rec.cpu_request_m}m.",
                                   {"current_m": w.cpu_request_m, "p95_m": rec.cpu_p95_m, "suggested_m": rec.cpu_request_m}))
            elif rec.cpu_p95_m > w.cpu_request_m:
                out.append(Finding("RS003", "medium", sub,
                                   f"p95 CPU usage {rec.cpu_p95_m:.0f}m exceeds the {w.cpu_request_m}m request; node contention can throttle it.",
                                   {"current_m": w.cpu_request_m, "p95_m": rec.cpu_p95_m, "suggested_m": rec.cpu_request_m}))
            if rec.mem_request_mib < OVER_RATIO * w.mem_request_mib:
                out.append(Finding("RS002", "medium", sub,
                                   f"Memory request {w.mem_request_mib}MiB looks high; max usage is {rec.mem_max_mib:.0f}MiB. Suggested request {rec.mem_request_mib}MiB.",
                                   {"current_mib": w.mem_request_mib, "max_mib": rec.mem_max_mib, "suggested_mib": rec.mem_request_mib}))
            elif rec.mem_max_mib > w.mem_request_mib:
                out.append(Finding("RS008", "low", sub,
                                   f"Max memory {rec.mem_max_mib:.0f}MiB exceeds the {w.mem_request_mib}MiB request; the pod can be evicted first under node memory pressure.",
                                   {"current_mib": w.mem_request_mib, "max_mib": rec.mem_max_mib}))
            if rec.peak_aware and rec.peak_day_p95_m > PEAK_RATIO * rec.pooled_p95_m:
                out.append(Finding("RS009", "low", sub,
                                   f"Peak day {rec.peak_day} sets the CPU suggestion: its p95 is {rec.peak_day_p95_m:.0f}m against {rec.pooled_p95_m:.0f}m pooled over all days. Suggested request {rec.cpu_request_m}m is sized from the peak day, not the quiet days.",
                                   {"peak_day": rec.peak_day, "peak_day_p95_m": rec.peak_day_p95_m,
                                    "pooled_p95_m": rec.pooled_p95_m, "day_p95_m": rec.day_p95_m,
                                    "headroom": rec.headroom, "suggested_m": rec.cpu_request_m}))
            new_req[(w.namespace, w.name)] = (rec.cpu_request_m, rec.mem_request_mib)
        if w.mem_limit_mib and w.mem_usage_mib and max(w.mem_usage_mib) >= LIMIT_RISK * w.mem_limit_mib:
            if (w.namespace, w.name) in new_req:  # do not plan a shrink for a workload near its limit
                new_req[(w.namespace, w.name)] = (new_req[(w.namespace, w.name)][0], max(w.mem_request_mib or 0, new_req[(w.namespace, w.name)][1]))
            out.append(Finding("RS004", "high", sub,
                               f"Max memory {max(w.mem_usage_mib):.0f}MiB is within 5% of the {w.mem_limit_mib}MiB limit: OOM-kill risk. Do not shrink; consider raising the limit.",
                               {"max_mib": max(w.mem_usage_mib), "limit_mib": w.mem_limit_mib}))

    node_est = None
    if complete and len(cur_req) == len(c.workloads) and len(new_req) == len(c.workloads):
        cur = packing.pack(c, packing.expand(c, cur_req))
        new = packing.pack(c, packing.expand(c, new_req))
        # Never plan below the packed need of the current requests' shape if packing fails.
        if cur is not None and new is not None:
            node_est = {"nodes_now": c.node_count, "nodes_needed_current_requests": cur,
                        "nodes_needed_suggested_requests": new,
                        "nodes_saved_estimate": max(0, cur - new),
                        "cost_units_saved_estimate": round(max(0, cur - new) * c.shape.cost_units, 2)}
            if new < cur:
                out.append(Finding("RS007", "medium", "cluster",
                                   f"Packing estimate: {cur} nodes needed at current requests vs {new} at suggested requests (cluster has {c.node_count}).",
                                   node_est))
    out.sort(key=lambda f: (SEV[f.severity], f.rule, f.subject))
    return out, node_est
