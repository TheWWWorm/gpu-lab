# SPDX-License-Identifier: Apache-2.0
import copy
import json
from pathlib import Path
import tempfile
import unittest

from gpu_dispatch import Dispatcher, MARKER, Rejected, Uncertain


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = 1000.
        self.config = {"lock_path": str(self.root/"lock"), "workers": {
            s: {"socket": s, "gpu_uuid": "GPU-"+s, "state_path": str(self.root/s)} for s in ["0","1"]}}
        self.states, self.jobs, self.loads, self.calls = {}, {}, {}, []
        self.down = set()
        self.lose_reply = False
        for s in ["0", "1"]:
            self.states[s] = {"gpu": {"uuid": "GPU-"+s}, "active_job": None, "active_jobs": [],
                              "queued": [], "jobs": [], "running": [], "device_isolation": True,
                              "accepting": True, "scheduler": {"max_concurrent": 3},
                              "displays": [{"display": ":"+s+str(n), "job_id": None, "error": None} for n in range(3)]}
            self.loads["GPU-"+s] = {"utilization_percent": 5, "memory_used_mib": 512, "memory_total_mib": 24576}
        self.dispatcher = self.make()

    def tearDown(self):
        self.temp.cleanup()

    def make(self):
        return Dispatcher(self.config, self.root/"queue", self.rpc, lambda: self.loads, lambda: self.clock)

    def rpc(self, method, args, socket_path):
        slot = socket_path
        if slot in self.down:
            raise ConnectionRefusedError("worker unavailable")
        self.calls.append((slot, method, copy.deepcopy(args)))
        state = self.states[slot]
        if method == "status":
            return copy.deepcopy(state)
        if method == "submit":
            ident = format(len(self.jobs)+1, "016x")
            job = {"id": ident, "status": "running", "created_at": self.clock, "started_at": self.clock,
                   "argv": args["argv"], "cwd": args["cwd"], "label": args.get("label", "test"),
                   "workload": args.get("workload", "auto"), "vram_mb": args.get("vram_mb", 2048),
                   "env": args.get("env", {}), "display": ":98", "gpu_uuid": "GPU-"+slot}
            self.jobs[ident] = (slot, job)
            self.sync(slot)
            p = self.root/slot/"jobs"/ident/"job.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(job))
            if self.lose_reply:
                self.lose_reply = False
                raise Uncertain("lost reply")
            return {k:v for k,v in job.items() if k != "env"}
        ident = args["job_id"]
        if ident not in self.jobs or self.jobs[ident][0] != slot:
            raise Rejected("Unknown job_id")
        job = self.jobs[ident][1]
        if method == "cancel":
            job.update(status="cancelled", finished_at=self.clock)
            self.sync(slot)
        public = {k:v for k,v in job.items() if k != "env"}
        if method in {"job", "cancel"}:
            return public
        if method == "logs":
            return {"job": public, "stdout": "sample output", "stderr": ""}
        if method == "windows":
            return {"windows": "GPU Lab test window"}
        return {"job_id": ident, "display": job["display"]}

    def sync(self, slot):
        jobs = [j for s,j in self.jobs.values() if s == slot]
        running = [j for j in jobs if j["status"] == "running"]
        state = self.states[slot]
        state.update(jobs=[{k:v for k,v in j.items() if k != "env"} for j in jobs],
                     running=copy.deepcopy(running), active_jobs=[j["id"] for j in running],
                     active_job=running[0]["id"] if running else None)
        for i, display in enumerate(state["displays"]):
            display["job_id"] = running[i]["id"] if i < len(running) else None

    def submit(self, **extra):
        return self.dispatcher.submit({"argv": ["/bin/true"], "cwd": str(self.root), **extra})

    def job(self, submitted):
        return self.dispatcher.call("job", {"job_id": submitted["id"]})

    def advance(self, seconds=1):
        self.clock += seconds
        self.dispatcher.tick()

    def test_burst_spreads_before_sharing(self):
        a, b = self.submit(), self.submit()
        self.assertEqual(a["gpu_slot"], "auto")
        self.dispatcher.tick()
        self.advance()
        self.assertEqual({self.job(a)["gpu_slot"], self.job(b)["gpu_slot"]}, {"0", "1"})

    def test_choice_is_made_when_capacity_becomes_available(self):
        for load in self.loads.values():
            load["utilization_percent"] = 99
        job = self.submit()
        self.dispatcher.tick()
        self.assertEqual(self.job(job)["status"], "queued")
        self.assertEqual(self.job(job)["gpu_slot"], "auto")
        self.loads["GPU-1"]["utilization_percent"] = 0
        self.advance(8)
        self.assertEqual(self.job(job)["gpu_slot"], "1")
        self.assertEqual(self.job(job)["status"], "running")

    def test_warmup_and_lower_load_control_sharing(self):
        a,b,c = self.submit(),self.submit(),self.submit()
        self.dispatcher.tick(); self.advance()
        self.advance(10)
        self.assertEqual(self.job(c)["status"], "queued")
        self.loads["GPU-0"]["utilization_percent"] = 50
        self.advance(12)
        # Need a complete fresh telemetry window after a long sample gap.
        self.advance(); self.advance()
        self.assertEqual(self.job(c)["status"], "running")
        self.assertEqual(self.job(c)["gpu_slot"], "1")

    def test_pinned_wait_does_not_block_other_gpu(self):
        self.loads["GPU-0"]["utilization_percent"] = 99
        pinned, auto = self.submit(gpu="0"), self.submit()
        self.dispatcher.tick()
        self.assertEqual(self.job(pinned)["status"], "queued")
        self.assertEqual(self.job(auto)["gpu_slot"], "1")

    def test_heavy_front_job_is_not_starved(self):
        a,b=self.submit(),self.submit()
        self.dispatcher.tick(); self.advance()
        heavy, light = self.submit(workload="heavy"),self.submit(workload="light")
        self.advance(25);self.advance();self.advance()
        self.assertEqual(self.job(heavy)["status"],"queued")
        self.assertEqual(self.job(light)["status"],"queued")
        self.dispatcher.call("cancel", {"job_id": a["id"]})
        self.advance()
        self.assertEqual(self.job(heavy)["status"],"running")

    def test_cancel_before_dispatch_never_runs(self):
        job=self.submit()
        self.dispatcher.call("cancel", {"job_id":job["id"]})
        self.dispatcher.tick()
        self.assertEqual(self.job(job)["status"],"cancelled")
        self.assertEqual(self.jobs,{})

    def test_queued_jobs_survive_restart(self):
        job=self.submit()
        self.dispatcher=self.make()
        self.dispatcher.tick()
        self.assertEqual(self.job(job)["status"],"running")

    def test_lost_reply_recovers_without_duplicate(self):
        job=self.submit(env={"PRIVATE_KEY":"secret"})
        self.lose_reply=True
        self.dispatcher.tick()
        self.assertEqual(len(self.jobs),1)
        self.dispatcher=self.make()
        self.dispatcher.tick()
        self.assertEqual(self.job(job)["status"],"running")
        self.assertEqual(len(self.jobs),1)
        self.assertNotIn("secret",json.dumps(self.dispatcher.status()))

    def test_unresolved_outcome_never_retries(self):
        original=self.dispatcher.pool.transport
        def lost(method,args,socket_path):
            if method=="submit":raise Uncertain("lost before known outcome")
            return original(method,args,socket_path)
        self.dispatcher.pool.transport=lost
        job=self.submit()
        self.dispatcher.tick()
        self.dispatcher=self.make()
        self.advance(30)
        self.assertEqual(len(self.jobs),0)
        self.assertIn("duplicate",self.job(job)["waiting_reason"])

    def test_uncertain_submission_reserves_only_its_gpu(self):
        original = self.dispatcher.pool.transport
        def lost(method, args, socket_path):
            if method == "submit": raise Uncertain("unknown acceptance")
            return original(method, args, socket_path)
        self.dispatcher.pool.transport = lost
        first = self.submit()
        self.dispatcher.tick()
        uncertain_gpu = self.job(first)["gpu_slot"]
        self.dispatcher = self.make()
        second, third = self.submit(), self.submit(gpu=uncertain_gpu)
        self.advance(30)
        self.assertNotEqual(self.job(second)["gpu_slot"], uncertain_gpu)
        self.advance()
        self.assertEqual(self.job(third)["status"], "queued")

    def test_legacy_job_access_survives_other_worker_outage(self):
        legacy = self.rpc("submit", {"argv":["true"], "cwd":str(self.root)}, "1")
        self.down.add("0")
        self.assertEqual(self.dispatcher.call("logs", {"job_id":legacy["id"]})["stdout"], "sample output")

    def test_job_scoped_actions_and_public_ids(self):
        job=self.submit()
        self.dispatcher.tick();self.advance()
        current=self.job(job)
        self.assertNotEqual(current["id"],current["worker_job_id"])
        for method in ["logs","input","screenshot"]:
            result=self.dispatcher.call(method,{"job_id":job["id"]})
            self.assertEqual(result.get("job_id",result.get("job",{}).get("id")),job["id"])
            self.assertEqual(result["gpu_slot"], current["gpu_slot"])
        windows = self.dispatcher.call("windows", {"job_id":job["id"]})
        self.assertIn("test window", windows["windows"])
        self.assertEqual(windows["gpu_slot"], current["gpu_slot"])
        status=self.dispatcher.status()
        self.assertIn(job["id"],status["active_jobs"])
        self.assertEqual(len([j for j in status["jobs"] if j["status"]=="running"]),1)
        self.dispatcher.call("cancel",{"job_id":job["id"]})
        self.assertEqual(self.job(job)["status"],"cancelled")

    def test_legacy_jobs_remain_visible_and_accessible(self):
        legacy=self.rpc("submit",{"argv":["true"],"cwd":str(self.root)},"0")
        self.dispatcher.tick()
        self.assertIn(legacy["id"],[j["id"] for j in self.dispatcher.status()["jobs"]])
        self.assertEqual(self.dispatcher.call("logs",{"job_id":legacy["id"]})["stdout"],"sample output")

    def test_unavailable_worker_and_impossible_memory(self):
        self.down.add("1")
        job=self.submit()
        self.dispatcher.tick()
        self.assertEqual(self.job(job)["gpu_slot"],"0")
        huge=self.submit(gpu="0",vram_mb=99999)
        self.advance()
        self.assertEqual(self.job(huge)["status"],"failed")

    def test_marker_cannot_be_overridden(self):
        with self.assertRaises(ValueError):
            self.submit(env={MARKER:"forged"})


if __name__ == "__main__":
    unittest.main()
