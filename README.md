# oci-oke-rightsizer

Offline right-sizing review for Kubernetes (OKE-style) workloads: compare what pods request with what they actually used, suggest conservative requests, and estimate how many nodes the suggested requests would need.

**Scope, stated plainly.** Portfolio project. It reads a simplified JSON export of workloads and usage samples. The bundled data is synthetic. It has not been run against a real OKE cluster, calls no OCI or Kubernetes API, and spends nothing. Suggestions are review priorities, not tuned production values. Node cost is a relative synthetic unit, not an OCI price.

## Rules

| Rule | Meaning |
|---|---|
| RS001 | CPU request is much higher than p95 usage (suggested < 60% of current) |
| RS002 | Memory request is much higher than max usage |
| RS003 | p95 CPU usage exceeds the CPU request (contention/throttling risk) |
| RS004 | Max memory within 5% of the limit: OOM risk, never shrunk |
| RS005 | Fewer than 48 samples: no recommendation |
| RS006 | CPU or memory request missing |
| RS007 | Packing estimate says suggested requests need fewer nodes |
| RS008 | Max memory above the request (evicted first under pressure) |
| RS009 | Peak day sets the CPU suggestion: the largest per-day p95 is over 10% above the pooled p95 (low; names the day and lists each day's p95) |

How suggestions are made: CPU = sizing p95 x headroom (default 1.20) rounded up to 10m. Memory = max observed x 1.15 rounded up to 16MiB (memory is not compressible, so no percentile). Floors of 10m and 32MiB. A workload close to its memory limit keeps at least its current memory request in the packing estimate.

Packing is first-fit-decreasing on requests with a 10% per-node reserve, plus per-workload anti-affinity (replicas never share a node). It is skipped unless every workload has requests and enough samples.

## Usage

```
python -m pip install -e .
oke-rightsizer examples/synthetic.json --output report.json
oke-rightsizer examples/csv-config.json --usage-csv examples/usage.csv   # per-pod CSV samples + workload config
oke-rightsizer examples/complete.json        # all workloads have data: shows the packing estimate
oke-rightsizer examples/synthetic.json --fail-on-high
python -m unittest discover -s tests -v
```

## Multi-day peaks and headroom

If a workload carries `sample_days` (one `YYYY-MM-DD` per sample, parallel to `cpu_usage_m`) and at least 3 days each have 12 or more samples, CPU is sized from the **largest per-day p95** (never lower than the pooled p95), so a rare daily peak is not hidden by quiet days. RS009 names the day that set it. With no day information, fewer than 3 usable days, or a single-day CSV, nothing changes from the earlier behaviour (the bundled single-window examples give identical reports).

Headroom per workload: `"headroom_class": "steady" | "bursty" | "critical"` = 1.20 / 1.40 / 1.50 (default steady), or an explicit `"cpu_headroom"` from 1.0 to 3.0 that overrides the class. Memory still uses max x 1.15. See `examples/multiday.json` (one synthetic workload with a 3 Sep peak, one steady).

Honest limits of this: the class values and the 3-day / 12-sample / 10% thresholds are my own assumptions, not tuned on real clusters. "Day" is the UTC date written in the data. A peak lasting minutes is only seen if samples are dense enough; a once-a-month spike is outside any export window.

## CSV importer

`--usage-csv` reads a local CSV (`timestamp,namespace,workload,pod,cpu_m,mem_mib`) and takes requests, limits and node shape from the config JSON given as the input argument (see `examples/csv-config.json`). When timestamps are ISO (`2026-09-01T...`) the importer keeps each sample's date for the peak-day logic. Pods of a workload are averaged per timestamp, so a hot single pod is hidden in the mean; replicas default to the number of pods seen. Malformed rows fail with a line number; duplicate samples, negative or NaN values, and samples for workloads missing from the config are rejected. The CSV is whatever you export yourself; nothing here talks to Prometheus or OCI Monitoring.

## Limits

- Usage samples are per-pod values in one export window; there is no seasonality and no burst modeling beyond the per-day p95 above, which needs day labels.
- No taints, tolerations, topology spread, DaemonSets, PodDisruptionBudgets, node pools or autoscaler behaviour in packing.
- Limits are only used for the OOM-risk check. Vertical Pod Autoscaler recommendations are not compared.
- A raise (RS003) can increase the estimated node count; the estimate is not always a saving.
- The CSV importer is a file reader; there is no exporter from real Prometheus or OCI Monitoring data, and it has only been run on synthetic CSVs.

## Tests

62 unit tests cover validation, percentile maths, recommendation rules, packing with anti-affinity, each finding rule, the case where right-sizing needs more nodes, the CSV importer (good, malformed, sparse data), multi-day peaks (bursty vs steady, tie-break, 10% boundary, thin days, fallback, a 60-series false-positive loop), headroom classes and validation, and the CLI. CI runs on Python 3.10, 3.11 and 3.12.

## License

MIT
