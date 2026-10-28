# SPDX-License-Identifier: Apache-2.0
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest

from gpu_pool import Pool


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"lock_path": str(Path(self.temp.name)/"schedule.lock"), "workers": {
            slot: {"socket": slot, "gpu_uuid": "GPU-"+slot} for slot in ["0", "1"]}}
        self.states = {slot: {"gpu": {"uuid": "GPU-"+slot}, "active_job": None, "queued": [],
                              "jobs": [], "device_isolation": True} for slot in ["0", "1"]}
        self.jobs = {}
        self.down = set()
        self.loads = {}
        self.events = []
        self.pool = Pool(self.config, self.transport, lambda: self.loads)

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, method, args, socket_path):
        slot = socket_path
        if slot in self.down:
            raise RuntimeError("worker unavailable")
        if method == "status":
            return copy.deepcopy(self.states[slot])
        if method == "submit":
            time.sleep(.01)  # Exposes races if the selection lock is missing.
            ident = "job-"+str(len(self.jobs))
            job = {"id": ident, "status": "queued", "created_at": time.time()}
            self.jobs[ident] = slot
            self.states[slot]["jobs"].append(job)
            self.states[slot]["queued"].append(ident)
            return job
        if self.jobs.get(args["job_id"]) != slot:
            raise RuntimeError("Unknown job_id")
        if method != "job":
            self.events.append((slot, method, args["job_id"]))
        return {"id": args["job_id"]}

    def test_concurrent_clients_share_both_workers(self):
        def submit(_):
            # Independent adapters, as with unrelated Codex and Claude sessions.
            return Pool(self.config, self.transport, lambda: {}).call("submit", {})
        with ThreadPoolExecutor(max_workers=12) as executor:
            jobs = list(executor.map(submit, range(12)))
        self.assertEqual(sum(j["gpu_slot"] == "0" for j in jobs), 6)
        self.assertEqual(sum(j["gpu_slot"] == "1" for j in jobs), 6)
        self.assertEqual(len(self.jobs), 12)

    def test_available_worker_beats_busy_worker_even_with_desktop_load(self):
        self.states["1"]["active_job"] = "long-test"
        self.loads = {"GPU-0": {"utilization_percent": 30}, "GPU-1": {"utilization_percent": 0}}
        self.assertEqual(self.pool.submit({})["gpu_slot"], "0")

    def test_external_load_breaks_equal_queue_ties(self):
        self.loads = {"GPU-0": {"utilization_percent": 1}, "GPU-1": {"utilization_percent": 90}}
        self.assertEqual(self.pool.submit({})["gpu_slot"], "0")

    def test_explicit_gpu_is_respected_and_unavailable_gpu_not_substituted(self):
        self.assertEqual(self.pool.submit({"gpu": "0"})["gpu_slot"], "0")
        self.down.add("0")
        with self.assertRaises(RuntimeError):
            self.pool.submit({"gpu": "0"})
        self.assertEqual(self.pool.submit({})["gpu_slot"], "1")

    def test_input_logs_cancel_follow_job_owner(self):
        a = self.pool.submit({"gpu": "0"})
        b = self.pool.submit({"gpu": "1"})
        for job in [a, b]:
            for method in ["input", "screenshot", "logs", "cancel"]:
                self.pool.call(method, {"job_id": job["id"]})
                self.assertEqual(self.events[-1], (job["gpu_slot"], method, job["id"]))

    def test_uuid_mismatch_excludes_worker(self):
        self.states["0"]["gpu"]["uuid"] = "unexpected"
        status = self.pool.call("status", {})
        self.assertFalse(status["gpus"]["0"]["available"])
        self.assertEqual(self.pool.submit({})["gpu_slot"], "1")

    def test_unknown_job_never_sends_input(self):
        with self.assertRaises(ValueError):
            self.pool.call("input", {"job_id": "unknown"})
        self.assertEqual(self.events, [])

    def test_uncertain_submission_is_not_duplicated(self):
        def uncertain(method, args, socket_path):
            result = self.transport(method, args, socket_path)
            if method == "submit":
                raise RuntimeError("connection lost after submission")
            return result
        with self.assertRaises(RuntimeError):
            Pool(self.config, uncertain, lambda: {}).submit({})
        self.assertEqual(len(self.jobs), 1)


if __name__ == "__main__":
    unittest.main()
