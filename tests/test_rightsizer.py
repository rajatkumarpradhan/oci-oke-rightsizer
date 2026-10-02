import copy
import json
import os
import tempfile
import unittest

from oke_rightsizer import findings, model, packing, recommend, stats
from oke_rightsizer.cli import main

HERE = os.path.dirname(__file__)
EX = os.path.join(HERE, "..", "examples", "synthetic.json")
with open(EX, encoding="utf-8") as f:
    BASE = json.load(f)


def complete_data():
    """Example without the two workloads that lack data, so packing runs."""
    d = copy.deepcopy(BASE)
    d["workloads"] = [w for w in d["workloads"] if w["name"] not in ("new-svc", "sidecar-agent")]
    return d


def rules(d):
    fs, est = findings.analyze(model.load(d))
    return {(f.rule, f.subject) for f in fs}, est


class StatsTests(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        self.assertEqual(stats.percentile([1, 2, 3, 4, 5], 100), 5)
        self.assertEqual(stats.percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertEqual(stats.percentile(list(range(1, 101)), 95), 95)

    def test_percentile_errors(self):
        with self.assertRaises(ValueError):
            stats.percentile([], 95)
        with self.assertRaises(ValueError):
            stats.percentile([1], 0)

    def test_round_up(self):
        self.assertEqual(stats.round_up(101, 10), 110)
        self.assertEqual(stats.round_up(100, 10), 100)


class ModelTests(unittest.TestCase):
    def test_loads(self):
        self.assertEqual(len(model.load(BASE).workloads), 7)

    def test_negative_rejected(self):
        d = copy.deepcopy(BASE)
        d["workloads"][0]["cpu_request_m"] = -5
        with self.assertRaises(model.ModelError):
            model.load(d)

    def test_duplicate_rejected(self):
        d = copy.deepcopy(BASE)
        d["workloads"].append(copy.deepcopy(d["workloads"][0]))
        with self.assertRaises(model.ModelError):
            model.load(d)

    def test_bad_samples_rejected(self):
        d = copy.deepcopy(BASE)
        d["workloads"][0]["cpu_usage_m"] = ["x"]
        with self.assertRaises(model.ModelError):
            model.load(d)

    def test_missing_key(self):
        with self.assertRaises(model.ModelError):
            model.load({"node_count": 1})

    def test_bad_reserve(self):
        d = copy.deepcopy(BASE)
        d["reserve_fraction"] = 0.95
        with self.assertRaises(model.ModelError):
            model.load(d)


class RecommendTests(unittest.TestCase):
    def wl(self, name):
        return next(w for w in model.load(BASE).workloads if w.name == name)

    def test_cpu_uses_p95_with_headroom(self):
        w = self.wl("web")
        r = recommend.recommend(w)
        self.assertEqual(r.cpu_request_m, stats.round_up(stats.percentile(w.cpu_usage_m, 95) * 1.2, 10))

    def test_memory_uses_max(self):
        w = self.wl("web")
        r = recommend.recommend(w)
        self.assertGreaterEqual(r.mem_request_mib, max(w.mem_usage_mib))

    def test_too_few_samples_no_recommendation(self):
        r = recommend.recommend(self.wl("new-svc"))
        self.assertFalse(r.enough_data)
        self.assertIsNone(r.cpu_request_m)

    def test_floor(self):
        w = self.wl("web")
        w.cpu_usage_m = [0.0] * 60
        w.mem_usage_mib = [0.0] * 60
        r = recommend.recommend(w)
        self.assertEqual((r.cpu_request_m, r.mem_request_mib), (10, 32))


class PackingTests(unittest.TestCase):
    def cluster(self):
        return model.load(BASE)

    def test_allocatable_reserve(self):
        self.assertEqual(packing.allocatable(self.cluster()), (3600, 14745))

    def test_simple_pack(self):
        c = self.cluster()
        self.assertEqual(packing.pack(c, [("a", 1800, 1000, False)] * 4), 2)

    def test_pod_too_big(self):
        self.assertIsNone(packing.pack(self.cluster(), [("a", 9999, 10, False)]))

    def test_anti_affinity_forces_spread(self):
        c = self.cluster()
        self.assertEqual(packing.pack(c, [("a", 100, 100, True)] * 3), 3)
        self.assertEqual(packing.pack(c, [("a", 100, 100, False)] * 3), 1)


class FindingTests(unittest.TestCase):
    def test_expected_rules(self):
        r, _ = rules(BASE)
        for k in [("RS001", "shop/web"), ("RS002", "data/batch"), ("RS003", "shop/api"), ("RS004", "shop/cache"),
                  ("RS005", "ops/new-svc"), ("RS006", "ops/sidecar-agent")]:
            self.assertIn(k, r)

    def test_incomplete_data_skips_packing(self):
        _, est = rules(BASE)
        self.assertIsNone(est)

    def test_packing_estimate_when_complete(self):
        r, est = rules(complete_data())
        self.assertIsNotNone(est)
        self.assertLess(est["nodes_needed_suggested_requests"], est["nodes_needed_current_requests"])
        self.assertIn(("RS007", "cluster"), r)

    def test_underrequested_workload_can_raise_node_need(self):
        d = complete_data()
        d["workloads"] = [w for w in d["workloads"] if w["name"] == "api"]
        d["workloads"][0]["replicas"] = 6
        d["workloads"][0]["anti_affinity"] = False
        _, est = rules(d)
        self.assertGreaterEqual(est["nodes_needed_suggested_requests"], est["nodes_needed_current_requests"])
        self.assertEqual(est["nodes_saved_estimate"], 0)

    def test_near_limit_workload_not_shrunk_in_packing(self):
        d = complete_data()
        c = model.load(d)
        fs, _ = findings.analyze(c)
        self.assertTrue(any(f.rule == "RS004" for f in fs))

    def test_right_sized_has_no_rs001(self):
        d = complete_data()
        for w in d["workloads"]:
            r = recommend.recommend(model.load(d).workloads[0])
        w = next(x for x in d["workloads"] if x["name"] == "web")
        wl = next(x for x in model.load(d).workloads if x.name == "web")
        w["cpu_request_m"] = recommend.recommend(wl).cpu_request_m
        w["mem_request_mib"] = recommend.recommend(wl).mem_request_mib
        r, _ = rules(d)
        self.assertNotIn(("RS001", "shop/web"), r)
        self.assertNotIn(("RS002", "shop/web"), r)

    def test_sorted(self):
        fs, _ = findings.analyze(model.load(BASE))
        sev = [findings.SEV[f.severity] for f in fs]
        self.assertEqual(sev, sorted(sev))


class CliTests(unittest.TestCase):
    def test_report(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "r.json")
            self.assertEqual(main([EX, "--output", out]), 0)
            with open(out, encoding="utf-8") as f:
                rep = json.load(f)
        self.assertEqual(rep["cluster"]["workloads"], 7)
        self.assertTrue(rep["findings"])

    def test_fail_on_high(self):
        self.assertEqual(main([EX, "--fail-on-high"]), 2)

    def test_bad_input(self):
        self.assertEqual(main(["/nonexistent.json"]), 1)


if __name__ == "__main__":
    unittest.main()
