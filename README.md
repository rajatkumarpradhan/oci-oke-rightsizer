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

How suggestions are made: CPU = p95 x 1.20 rounded up to 10m. Memory = max observed x 1.15 rounded up to 16MiB (memory is not compressible, so no percentile). Floors of 10m and 32MiB. A workload close to its memory limit keeps at least its current memory request in the packing estimate.

Packing is first-fit-decreasing on requests with a 10% per-node reserve, plus per-workload anti-affinity (replicas never share a node). It is skipped unless every workload has requests and enough samples.

## Usage

```
python -m pip install -e .
oke-rightsizer examples/synthetic.json --output report.json
oke-rightsizer examples/complete.json        # all workloads have data: shows the packing estimate
oke-rightsizer examples/synthetic.json --fail-on-high
python -m unittest discover -s tests -v
```

## Limits

- Usage samples are per-pod values in one export window; there is no seasonality, no multi-day peaks, no burst modeling.
- No taints, tolerations, topology spread, DaemonSets, PodDisruptionBudgets, node pools or autoscaler behaviour in packing.
- Limits are only used for the OOM-risk check. Vertical Pod Autoscaler recommendations are not compared.
- A raise (RS003) can increase the estimated node count; the estimate is not always a saving.
- No exporter from real Prometheus or OCI Monitoring data exists yet.

## Tests

27 unit tests cover validation, percentile maths, recommendation rules, packing with anti-affinity, each finding rule, the case where right-sizing needs more nodes, and the CLI. CI runs on Python 3.10, 3.11 and 3.12.

## License

MIT
