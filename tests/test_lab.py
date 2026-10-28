# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from gpu_lab import Lab, TERMINAL


class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name)
        self.lab = Lab({"home": str(p), "user": "test", "display": ":98"},
                       {"uuid": "GPU-test", "vendor_device": "10de:2203", "name": "Test GPU"},
                       p/"run", p/"state", manage_display=False, require_cgroup=False)

    def tearDown(self):
        self.lab.shutdown()
        self.tmp.cleanup()

    def submit(self, code, **kwargs):
        return self.lab.submit({"argv": [sys.executable, "-c", code], "cwd": self.tmp.name, **kwargs})

    def wait(self, ident, timeout=12):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            result = self.lab.call("job", {"job_id": ident})
            if result["status"] in TERMINAL and "finished_at" in result:
                return result
            time.sleep(.05)
        self.fail("Job did not finish")

    def test_exit_and_output(self):
        j = self.submit("import sys; print('hello'); print('error',file=sys.stderr); sys.exit(7)")
        result = self.wait(j["id"])
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["status"], "failed")
        logs = self.lab.call("logs", {"job_id": j["id"]})
        self.assertIn("hello", logs["stdout"])
        self.assertIn("error", logs["stderr"])

    def test_queue_serializes_and_cancellation(self):
        a = self.submit("import time; time.sleep(4)")
        b = self.submit("print('second')")
        time.sleep(.2)
        self.assertEqual(self.lab.call("job", {"job_id": b["id"]})["status"], "queued")
        self.lab.call("cancel", {"job_id": a["id"]})
        self.assertEqual(self.wait(a["id"])["status"], "cancelled")
        self.assertEqual(self.wait(b["id"])["status"], "completed")

    def test_timeout(self):
        j = self.submit("import time; time.sleep(30)", timeout_seconds=1)
        self.assertEqual(self.wait(j["id"])["status"], "timed_out")

    def test_logs_are_live_before_process_exits(self):
        j = self.submit("import time; print('ready',flush=True); time.sleep(4)")
        time.sleep(.5)
        logs = self.lab.call("logs", {"job_id": j["id"]})
        self.assertEqual(logs["job"]["status"], "running")
        self.assertIn("ready", logs["stdout"])
        self.lab.call("cancel", {"job_id": j["id"]})
        self.wait(j["id"])

    def test_protected_environment(self):
        for env in [{"DISPLAY": ":0"}, {"CUDA_VISIBLE_DEVICES": "0"}, {"NODEVICE_SELECT": "1"},
                    {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}]:
            with self.assertRaises(ValueError):
                self.submit("pass", env=env)

    def test_input_cannot_target_queued_or_finished_job(self):
        a = self.submit("import time; time.sleep(1)")
        b = self.submit("pass")
        with self.assertRaises(ValueError):
            self.lab.call("input", {"job_id": b["id"], "events": [{"type": "key", "key": "Return"}]})
        self.wait(a["id"])
        self.wait(b["id"])
        with self.assertRaises(ValueError):
            self.lab.call("screenshot", {"job_id": a["id"]})

    def test_log_output_is_bounded(self):
        j = self.submit("import sys; sys.stdout.write('x'*9000000)")
        result = self.wait(j["id"])
        self.assertTrue(result["stdout_truncated"])
        self.assertLessEqual((Path(result["directory"])/"stdout.log").stat().st_size, 8*1024*1024)

    def test_mcp_initializes_without_service(self):
        data = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/"gpu_lab.py"), "mcp"],
                                input=json.dumps(data)+"\n", capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["result"]["serverInfo"]["name"], "gpu-lab")


if __name__ == "__main__":
    unittest.main()
