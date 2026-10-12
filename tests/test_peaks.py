import copy
import json
import os
import random
import tempfile
import unittest

from oke_rightsizer import findings, importer, model, recommend
from oke_rightsizer.cli import main

HERE = os.path.dirname(__file__)
EX = os.path.join(HERE, "..", "examples")
DAYS = ["2026-09-0%d" % i for i in range(1, 6)]


def series(peak_day=None, base=200.0, peak=900.0, noise=10.0, seed=1, hours=24):
    rr = random.Random(seed)
    cpu, days = [], []
    for d in DAYS:
        for h in range(hours):
            v = base + rr.uniform(-noise, noise)
            if d == peak_day and 9 <= h <= 13:
                v = peak + rr.uniform(-noise, noise)
            cpu.append(round(v, 1))
            days.append(d)
    return cpu, days


def wl(cpu, days=None, **kw):
    w = {"namespace": "ns", "name": "app", "replicas": 1, "cpu_request_m": 1000, "mem_request_mib": 1024,
         "cpu_usage_m": cpu, "mem_usage_mib": [500.0] * len(cpu)}
    if days is not None:
        w["sample_days"] = days
    w.update(kw)
    return w


def cluster(*ws):
    return model.load({"node_shape": {"name": "n", "cpu_m": 4000, "mem_mib": 16384}, "node_count": 3,
                       "workloads": list(ws)})


def rec(w):
    return recommend.recommend(cluster(w).workloads[0])


class PeakTests(unittest.TestCase):
    def test_bursty_day_sets_the_request_and_is_named(self):
        cpu, days = series(peak_day="2026-09-03")
        r = rec(wl(cpu, days))
        self.assertTrue(r.peak_aware)
        self.assertEqual(r.peak_day, "2026-09-03")
        self.assertGreater(r.peak_day_p95_m, 800)
        self.assertLess(r.pooled_p95_m, 300)           # pooled p95 hides the peak day
        self.assertGreaterEqual(r.cpu_request_m, r.peak_day_p95_m * 1.2)

    def test_same_series_without_days_is_sized_from_pooled_p95(self):
        cpu, days = series(peak_day="2026-09-03")
        pooled = rec(wl(cpu))
        aware = rec(wl(cpu, days))
        self.assertFalse(pooled.peak_aware)
        self.assertIsNone(pooled.peak_day)
        self.assertLess(pooled.cpu_request_m, aware.cpu_request_m)

    def test_steady_series_matches_pooled_result_closely(self):
        cpu, days = series(peak_day=None, noise=5)
        r = rec(wl(cpu, days))
        self.assertTrue(r.peak_aware)
        pooled = rec(wl(cpu)).cpu_request_m
        self.assertLessEqual(abs(r.cpu_request_m - pooled), 10)

    def test_steady_series_gives_no_peak_finding(self):
        cpu, days = series(peak_day=None, noise=5)
        fs, _ = findings.analyze(cluster(wl(cpu, days)))
        self.assertNotIn("RS009", {f.rule for f in fs})

    def test_peak_finding_has_day_and_evidence(self):
        cpu, days = series(peak_day="2026-09-04")
        fs, _ = findings.analyze(cluster(wl(cpu, days)))
        f = next(f for f in fs if f.rule == "RS009")
        self.assertEqual(f.evidence["peak_day"], "2026-09-04")
        self.assertIn("2026-09-04", f.message)
        self.assertEqual(set(f.evidence["day_p95_m"]), set(DAYS))

    def test_tie_between_days_reports_earliest(self):
        cpu = [100.0] * (24 * 3)
        days = [d for d in DAYS[:3] for _ in range(24)]
        r = rec(wl(cpu, days))
        self.assertEqual(r.peak_day, "2026-09-01")

    def test_peak_finding_boundary_ten_percent(self):
        def build(peak_level):
            cpu, days = [], []
            for i, d in enumerate(DAYS):
                day = [200.0] * 24
                if i == 1:
                    day[:6] = [peak_level] * 6          # 6 of 120 samples: pooled p95 stays at 200
                cpu += day
                days += [d] * 24
            return cluster(wl(cpu, days))
        c = build(219.0)                                # day p95 is 9.5% above pooled
        self.assertAlmostEqual(recommend.recommend(c.workloads[0]).pooled_p95_m, 200.0)
        self.assertNotIn("RS009", {f.rule for f in findings.analyze(c)[0]})
        self.assertIn("RS009", {f.rule for f in findings.analyze(build(222.0))[0]})   # 11% above

    def test_fewer_than_three_usable_days_falls_back_to_pooled(self):
        cpu, days = series(peak_day="2026-09-03")
        two = [(c, d) for c, d in zip(cpu, days) if d in DAYS[:2]]
        r = rec(wl([c for c, _ in two], [d for _, d in two]))
        self.assertFalse(r.peak_aware)

    def test_thin_days_do_not_count(self):
        cpu, days = [], []
        for d in DAYS[:3]:
            cpu += [100.0] * 24
            days += [d] * 24
        for d in DAYS[3:]:                       # two days with only 5 samples each
            cpu += [999.0] * 5
            days += [d] * 5
        r = rec(wl(cpu, days))
        self.assertTrue(r.peak_aware)
        self.assertEqual(set(r.day_p95_m), set(DAYS[:3]))
        self.assertLess(r.peak_day_p95_m, 200)

    def test_pooled_p95_wins_if_higher_than_every_day(self):
        cpu, days = series(peak_day=None, noise=5)
        r = rec(wl(cpu, days))
        self.assertGreaterEqual(r.cpu_p95_m, max(r.day_p95_m.values()) - 1e-9)

    def test_too_few_samples_still_no_recommendation(self):
        r = rec(wl([100.0] * 10, ["2026-09-01"] * 10))
        self.assertFalse(r.enough_data)
        self.assertIsNone(r.cpu_request_m)


class HeadroomTests(unittest.TestCase):
    def setUp(self):
        self.cpu = [200.0] * 60

    def test_default_class_is_unchanged(self):
        self.assertEqual(rec(wl(self.cpu)).cpu_request_m, 240)
        self.assertEqual(rec(wl(self.cpu)).headroom, 1.2)

    def test_classes(self):
        self.assertEqual(rec(wl(self.cpu, headroom_class="bursty")).cpu_request_m, 280)
        self.assertEqual(rec(wl(self.cpu, headroom_class="critical")).cpu_request_m, 300)

    def test_explicit_headroom_overrides_class(self):
        self.assertEqual(rec(wl(self.cpu, headroom_class="critical", cpu_headroom=1.0)).cpu_request_m, 200)
        self.assertEqual(rec(wl(self.cpu, cpu_headroom=2.0)).cpu_request_m, 400)

    def test_bad_values_are_rejected(self):
        for bad in ({"headroom_class": "wild"}, {"cpu_headroom": 0.9}, {"cpu_headroom": 3.1},
                    {"cpu_headroom": "1.2"}, {"cpu_headroom": True}):
            with self.subTest(bad=bad), self.assertRaises(model.ModelError):
                cluster(wl(self.cpu, **bad))

    def test_sample_days_validation(self):
        with self.assertRaises(model.ModelError):
            cluster(wl(self.cpu, ["2026-09-01"] * 59))              # length mismatch
        with self.assertRaises(model.ModelError):
            cluster(wl(self.cpu, ["01/09/2026"] * 60))              # not ISO
        with self.assertRaises(model.ModelError):
            cluster(wl(self.cpu, "2026-09-01"))                     # not a list


class ImporterAndExamples(unittest.TestCase):
    def test_csv_with_several_days_becomes_peak_aware(self):
        cpu, days = series(peak_day="2026-09-02")
        with tempfile.TemporaryDirectory() as d:
            csvp, cfgp = os.path.join(d, "u.csv"), os.path.join(d, "c.json")
            with open(csvp, "w", encoding="utf-8") as f:
                f.write("timestamp,namespace,workload,pod,cpu_m,mem_mib\n")
                for i, (c, day) in enumerate(zip(cpu, days)):
                    f.write(f"{day}T{i % 24:02d}:00:00Z,ns,app,app-0,{c},500\n")
            with open(cfgp, "w", encoding="utf-8") as f:
                json.dump({"node_shape": {"name": "n", "cpu_m": 4000, "mem_mib": 16384}, "node_count": 3,
                           "workloads": [{"namespace": "ns", "name": "app", "cpu_request_m": 1000,
                                          "mem_request_mib": 1024}]}, f)
            c = importer.from_csv(csvp, cfgp)
            self.assertEqual(c.workloads[0].sample_days, days)
            self.assertEqual(recommend.recommend(c.workloads[0]).peak_day, "2026-09-02")

    def test_single_day_csv_example_is_not_peak_aware(self):
        c = importer.from_csv(os.path.join(EX, "usage.csv"), os.path.join(EX, "csv-config.json"))
        for w in c.workloads:
            self.assertFalse(recommend.recommend(w).peak_aware)
        fs, _ = findings.analyze(c)
        self.assertNotIn("RS009", {f.rule for f in fs})

    def test_existing_examples_have_no_peak_finding(self):
        for name in ("synthetic.json", "complete.json"):
            fs, _ = findings.analyze(model.load_file(os.path.join(EX, name)))
            self.assertNotIn("RS009", {f.rule for f in fs}, name)

    def test_multiday_example_reports_peak_and_cli_runs(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.json")
            self.assertEqual(main([os.path.join(EX, "multiday.json"), "--output", out]), 0)
            rep = json.load(open(out, encoding="utf-8"))
        rs9 = [f for f in rep["findings"] if f["rule"] == "RS009"]
        self.assertEqual([f["subject"] for f in rs9], ["shop/batch-report"])
        self.assertEqual(rs9[0]["evidence"]["peak_day"], "2026-09-03")
        self.assertNotIn("shop/web", [f["subject"] for f in rs9])   # the steady workload stays quiet

    def test_false_positive_loop_on_steady_workloads(self):
        bad = 0
        for seed in range(60):
            cpu, days = series(peak_day=None, noise=15, seed=seed)
            fs, _ = findings.analyze(cluster(wl(cpu, days)))
            bad += any(f.rule == "RS009" for f in fs)
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
