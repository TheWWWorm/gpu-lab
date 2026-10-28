# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from gpu_lab import Lab, TERMINAL
from gpu_schedule import Admission


class ConcurrentJobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.metrics = {"utilization_percent": 5, "memory_total_mib": 24000, "memory_used_mib": 1000}
        self.lab = Lab({"home": str(root), "user": "test", "display": ":98",
                        "displays": [":98", ":108", ":118"], "max_concurrent": 3,
                        "launch_gap_seconds": 0, "minimum_samples": 1,
                        "sample_window_seconds": 0, "startup_reservation_seconds": 0},
                       {"uuid": "GPU-test", "vendor_device": "10de:2203", "name": "Test GPU"},
                       root/"run", root/"state", manage_display=False, require_cgroup=False,
                       load=lambda: {"GPU-test": dict(self.metrics)} if self.metrics else {})

    def tearDown(self):
        self.lab.shutdown()
        self.temp.cleanup()

    def submit(self, seconds=20, **args):
        return self.lab.submit({"argv": [sys.executable, "-c",
            "import os,time; print(os.environ['DISPLAY'], os.environ['XAUTHORITY'], flush=True); time.sleep("+str(seconds)+")"],
            "cwd": self.temp.name, "timeout_seconds": 30, **args})

    def until(self, condition, timeout=5):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(.025)
        self.fail("Condition did not become true")

    def job(self, j):
        return self.lab.call("job", {"job_id": j["id"]})

    def cancel(self, j):
        self.lab.call("cancel", {"job_id": j["id"]})
        self.until(lambda: "finished_at" in self.job(j))

    def test_three_jobs_overlap_with_distinct_displays_and_cancellation_is_local(self):
        jobs = [self.submit() for _ in range(4)]
        self.until(lambda: len(self.lab.call("status", {})["active_jobs"]) == 3)
        self.assertEqual(self.job(jobs[3])["status"], "queued")
        self.until(lambda: all(self.lab.call("logs", {"job_id": j["id"]})["stdout"] for j in jobs[:3]))
        logs = [self.lab.call("logs", {"job_id": j["id"]})["stdout"].strip().split() for j in jobs[:3]]
        self.assertEqual(len({r[0] for r in logs}), 3)
        self.assertEqual(len({r[1] for r in logs}), 3)
        self.cancel(jobs[0])
        self.until(lambda: self.job(jobs[3])["status"] == "running")
        self.assertTrue(all(self.job(j)["status"] == "running" for j in jobs[1:]))

    def test_heavy_job_excludes_siblings_and_is_not_starved_by_later_light_job(self):
        first = self.submit()
        self.until(lambda: self.job(first)["status"] == "running")
        heavy = self.submit(workload="heavy")
        light = self.submit(workload="light")
        time.sleep(.15)
        self.assertEqual(self.job(heavy)["status"], "queued")
        self.assertEqual(self.job(light)["status"], "queued")
        self.cancel(first)
        self.until(lambda: self.job(heavy)["status"] == "running")
        self.assertEqual(self.job(light)["status"], "queued")
        self.cancel(heavy)
        self.until(lambda: self.job(light)["status"] == "running")

    def test_vram_pressure_waits_then_recovers_without_resubmission(self):
        self.metrics["memory_used_mib"] = 23000
        self.until(lambda: self.lab.call("status", {})["recent_load"]["memory_used_mib"] == 23000)
        j = self.submit()
        time.sleep(.2)
        self.assertEqual(self.job(j)["status"], "queued")
        self.assertIn("VRAM", self.job(j)["waiting_reason"])
        self.metrics["memory_used_mib"] = 1000
        self.until(lambda: self.job(j)["status"] == "running")

    def test_busy_gpu_waits_for_lower_load(self):
        self.metrics["utilization_percent"] = 99
        self.until(lambda: self.lab.call("status", {})["recent_load"]["utilization_percent"] == 99)
        j = self.submit()
        time.sleep(.2)
        self.assertEqual(self.job(j)["status"], "queued")
        self.metrics["utilization_percent"] = 5
        self.until(lambda: self.job(j)["status"] == "running")

    def test_drain_rejects_new_jobs_but_completes_accepted_jobs(self):
        j = self.submit(.3)
        self.lab.call("drain", {"enabled": True})
        with self.assertRaises(RuntimeError):
            self.submit()
        self.until(lambda: "finished_at" in self.job(j))
        self.assertEqual(self.job(j)["status"], "completed")
        self.lab.call("drain", {"enabled": False})
        self.assertEqual(self.submit()["status"], "queued")

    def test_impossible_memory_request_rejected(self):
        self.until(lambda: self.lab.call("status", {})["recent_load"] is not None)
        with self.assertRaises(ValueError):
            self.submit(vram_mb=30000)


class AdmissionRules(unittest.TestCase):
    def setUp(self):
        self.a = Admission({"max_concurrent": 3})
        self.job = {"workload": "auto", "vram_mb": 2048}
        self.running = [{"workload": "auto", "vram_mb": 2048, "started_at": 99}]
        self.low = {"utilization_percent": 5, "memory_used_mib": 1000, "memory_total_mib": 24000}

    def test_one_quiet_sample_does_not_override_recent_peak(self):
        high = self.low | {"utilization_percent": 95}
        reason = self.a.reason(self.job, self.running, [(98, high), (99, self.low), (100, self.low)], 90, 100)
        self.assertIn("utilization", reason)

    def test_missing_telemetry_serializes(self):
        self.assertIsNone(self.a.reason(self.job, [], [], 0, 100))
        self.assertIn("telemetry", self.a.reason(self.job, self.running, [], 90, 100))

    def test_launch_gap_and_startup_memory_reservations(self):
        samples = [(i, self.low) for i in [98, 99, 100]]
        self.assertIn("settle", self.a.reason(self.job, self.running, samples, 99, 100))
        tight = self.low | {"memory_used_mib": 19500}
        self.assertIn("VRAM", self.a.reason(self.job, self.running, [(i, tight) for i in [98, 99, 100]], 90, 100))


if __name__ == "__main__":
    unittest.main()
