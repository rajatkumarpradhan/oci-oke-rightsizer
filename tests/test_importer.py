import json
import os
import tempfile
import unittest

from oke_rightsizer import importer, model
from oke_rightsizer.cli import main

HERE = os.path.dirname(__file__)
EX = os.path.join(HERE, "..", "examples")
HDR = "timestamp,namespace,workload,pod,cpu_m,mem_mib\n"


def tmp(content, suffix=".csv"):
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


class ImporterTests(unittest.TestCase):
    def cfg(self, names=(("shop", "web"),)):
        return tmp(json.dumps({"node_shape": {"name": "s", "cpu_m": 4000, "mem_mib": 16384}, "node_count": 2,
                               "workloads": [{"namespace": n, "name": w, "cpu_request_m": 500,
                                              "mem_request_mib": 512} for n, w in names]}), ".json")

    def test_example_loads(self):
        c = importer.from_csv(os.path.join(EX, "usage.csv"), os.path.join(EX, "csv-config.json"))
        web = next(w for w in c.workloads if w.name == "web")
        self.assertEqual(web.replicas, 3)
        self.assertEqual(len(web.cpu_usage_m), 60)

    def test_pods_averaged_per_timestamp(self):
        csv_path = tmp(HDR + "t1,shop,web,p0,100,200\nt1,shop,web,p1,300,400\nt2,shop,web,p0,50,60\n")
        c = importer.from_csv(csv_path, self.cfg())
        w = c.workloads[0]
        self.assertEqual(w.cpu_usage_m, [200.0, 50.0])
        self.assertEqual(w.mem_usage_mib, [300.0, 60.0])
        self.assertEqual(w.replicas, 2)

    def test_missing_column(self):
        with self.assertRaises(model.ModelError):
            importer.read_samples(tmp("timestamp,namespace,workload,pod,cpu_m\nt,a,b,c,1\n"))

    def test_non_numeric(self):
        with self.assertRaises(model.ModelError) as e:
            importer.read_samples(tmp(HDR + "t,a,b,c,abc,1\n"))
        self.assertIn("line 2", str(e.exception))

    def test_negative_and_nan(self):
        for bad in ("-1,5", "nan,5"):
            with self.assertRaises(model.ModelError):
                importer.read_samples(tmp(HDR + f"t,a,b,c,{bad}\n"))

    def test_empty_field(self):
        with self.assertRaises(model.ModelError):
            importer.read_samples(tmp(HDR + "t,a,,c,1,1\n"))

    def test_duplicate_sample(self):
        with self.assertRaises(model.ModelError):
            importer.read_samples(tmp(HDR + "t,a,b,c,1,1\nt,a,b,c,2,2\n"))

    def test_no_rows(self):
        with self.assertRaises(model.ModelError):
            importer.read_samples(tmp(HDR))

    def test_workload_not_in_config(self):
        with self.assertRaises(model.ModelError):
            importer.from_csv(tmp(HDR + "t,x,y,p,1,1\n"), self.cfg())

    def test_sparse_workload_gets_rs005_path(self):
        c = importer.from_csv(tmp(HDR + "t1,shop,web,p0,1,1\n"), self.cfg())
        from oke_rightsizer import findings
        fs, _ = findings.analyze(c)
        self.assertIn("RS005", {f.rule for f in fs})

    def test_config_workload_without_samples(self):
        c = importer.from_csv(tmp(HDR + "t1,shop,web,p0,1,1\n"), self.cfg((("shop", "web"), ("shop", "idle"))))
        idle = next(w for w in c.workloads if w.name == "idle")
        self.assertEqual(idle.cpu_usage_m, [])


class CliCsvTests(unittest.TestCase):
    def test_cli_with_csv(self):
        self.assertEqual(main([os.path.join(EX, "csv-config.json"), "--usage-csv", os.path.join(EX, "usage.csv")]), 0)

    def test_cli_bad_csv(self):
        self.assertEqual(main([os.path.join(EX, "csv-config.json"), "--usage-csv", tmp(HDR + "t,a,b,c,x,1\n")]), 1)

    def test_cli_missing_csv(self):
        self.assertEqual(main([os.path.join(EX, "csv-config.json"), "--usage-csv", "/nope.csv"]), 1)


if __name__ == "__main__":
    unittest.main()
