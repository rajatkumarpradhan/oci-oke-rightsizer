from __future__ import annotations

import argparse
import csv
import json
import sys

from . import findings, importer, model, packing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oke-rightsizer",
                                 description="Offline OKE right-sizing review from exported metrics (synthetic/exported data).")
    ap.add_argument("input", help="model JSON, or workload config JSON when --usage-csv is given")
    ap.add_argument("--usage-csv", help="CSV of per-pod samples: timestamp,namespace,workload,pod,cpu_m,mem_mib")
    ap.add_argument("--output", help="write JSON report")
    ap.add_argument("--fail-on-high", action="store_true")
    a = ap.parse_args(argv)
    try:
        c = importer.from_csv(a.usage_csv, a.input) if a.usage_csv else model.load_file(a.input)
    except (OSError, json.JSONDecodeError, csv.Error, model.ModelError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    fs, est = findings.analyze(c)
    cap = packing.allocatable(c)
    report = {"cluster": {"node_shape": c.shape.name, "nodes": c.node_count,
                          "allocatable_per_node": {"cpu_m": cap[0], "mem_mib": cap[1]},
                          "workloads": len(c.workloads)},
              "packing_estimate": est, "findings": [f.to_dict() for f in fs],
              "disclaimer": "Review priorities from sampled metrics; not scheduler guarantees."}
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    for f in fs:
        print(f"[{f.severity.upper():6}] {f.rule} {f.subject}: {f.message}")
    if est:
        print(f"packing estimate: {est['nodes_needed_current_requests']} -> {est['nodes_needed_suggested_requests']} nodes")
    print(f"{len(fs)} finding(s).")
    return 2 if a.fail_on_high and any(f.severity == "high" for f in fs) else 0


if __name__ == "__main__":
    sys.exit(main())
