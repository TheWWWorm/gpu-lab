# SPDX-License-Identifier: Apache-2.0
import base64
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from PIL import Image
from dashboard_server import Dashboard, make_server

ID = "a"*16


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job = {"id": ID, "status": "running", "created_at": 1, "directory": str(self.root)}
        self.calls = []
        self.status = {"jobs": [], "active_jobs": [ID], "queued": [],
                       "gpus": {"0": {"available": True, "load": {"utilization_percent": 0}}}}
        self.dashboard = Dashboard(self.rpc, background=False)

    def tearDown(self):
        self.temp.cleanup()

    def rpc(self, method, args=None):
        self.calls.append((method, args))
        if method == "status":
            return self.status
        if method == "job":
            return self.job.copy()
        if method == "screenshot":
            path = self.root/"screen-123456789.png"
            output = io.BytesIO()
            Image.new("RGB", (10, 10), "blue").save(output, "PNG")
            path.write_bytes(output.getvalue())
            return {"path": str(path), "image_base64": base64.b64encode(output.getvalue()).decode()}
        if method == "logs":
            return {"job": self.job, "stdout": "hello", "stderr": ""}
        raise AssertionError("Unexpected RPC: "+method)

    def test_all_active_and_queued_jobs_are_resolved(self):
        self.status["active_jobs"] = []
        self.status["queued"] = [ID]
        self.job["status"] = "queued"
        self.dashboard.refresh()
        self.assertEqual(self.dashboard.snapshot()["jobs"], [self.job])

    def test_missing_telemetry_is_not_zero(self):
        self.dashboard.refresh()
        self.assertEqual(self.dashboard.snapshot()["history"]["0"][-1]["utilization"], 0)
        self.status["gpus"]["0"]["available"] = False
        self.dashboard.refresh()
        self.assertIsNone(self.dashboard.snapshot()["history"]["0"][-1]["utilization"])

    def test_history_is_bounded_and_snapshot_detached(self):
        for _ in range(65):
            self.dashboard.refresh()
        snapshot = self.dashboard.snapshot()
        self.assertEqual(len(snapshot["history"]["0"]), 60)
        snapshot["jobs"].clear()
        self.assertEqual(len(self.dashboard.snapshot()["jobs"]), 1)

    def test_preview_cache_removes_only_its_own_artifact(self):
        earlier = self.root/"screen-111.png"
        earlier.write_bytes(b"keep")
        first = self.dashboard.preview(ID)
        second = self.dashboard.preview(ID)
        self.assertEqual(first, second)
        self.assertEqual(first[1], "image/jpeg")
        self.assertEqual(sum(m == "screenshot" for m, _ in self.calls), 1)
        self.assertFalse((self.root/"screen-123456789.png").exists())
        self.assertEqual(earlier.read_bytes(), b"keep")

    def test_finished_job_keeps_only_cached_last_frame(self):
        self.dashboard.preview(ID)
        self.dashboard.interval = 0
        self.job["status"] = "completed"
        self.assertTrue(self.dashboard.preview(ID)[2])
        self.assertEqual(sum(m == "screenshot" for m, _ in self.calls), 1)
        with self.assertRaises(ValueError):
            self.dashboard.preview("b"*16)

    def test_full_resolution_download_is_png(self):
        data, mime, _ = self.dashboard.preview(ID, full=True)
        self.assertEqual(mime, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertFalse((self.root/"screen-123456789.png").exists())

    def test_unexpected_artifact_path_is_never_removed(self):
        unrelated = self.root/"important.png"
        unrelated.write_bytes(b"keep")
        original = self.rpc
        self.dashboard.rpc = lambda method, args=None: {"path": str(unrelated)} if method == "screenshot" else original(method,args)
        with self.assertRaises(RuntimeError):
            self.dashboard.preview(ID)
        self.assertEqual(unrelated.read_bytes(), b"keep")


class HTTPTests(DashboardTests):
    def setUp(self):
        super().setUp()
        (self.root/"index.html").write_text("hello")
        self.server = make_server(0, self.dashboard, "test-token", assets=self.root)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, path, method="GET", body=None, headers=None, authenticated=False):
        headers = dict(headers or {})
        if authenticated:
            headers["Cookie"] = "gpu_lab_session=test-token"
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        client.request(method, path, body=body, headers=headers)
        result = client.getresponse()
        response = (result.status, dict(result.getheaders()), result.read())
        client.close()
        return response

    def test_auth_required_for_private_data_but_not_shell(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/api/status")[0], 401)
        self.assertEqual(self.request("/api/status", authenticated=True)[0], 200)

    def test_session_cookie_is_http_only_same_site(self):
        status, headers, _ = self.request("/api/session", "POST", json.dumps({"token":"test-token"}), {"Content-Type":"application/json"})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_bad_session_input_is_rejected(self):
        for body, expected in [('[]',400),('{',400),('{"token":"wrong"}',403)]:
            self.assertEqual(self.request("/api/session", "POST", body, {"Content-Type":"application/json"})[0], expected)

    def test_cross_origin_and_dns_rebinding_rejected(self):
        for headers, expected in [({"Origin":"https://example.com"},403),({"Host":"example.com"},421),({"Sec-Fetch-Site":"cross-site"},403)]:
            self.assertEqual(self.request("/api/status", headers=headers, authenticated=True)[0], expected)

    def test_read_only_and_path_validation(self):
        self.assertEqual(self.request("/api/submit", "POST", authenticated=True)[0], 405)
        self.assertEqual(self.request("/api/jobs/../../etc/passwd", authenticated=True)[0], 404)
        self.assertEqual(self.request("/api/jobs/not-an-id/logs", authenticated=True)[0], 404)
        self.assertEqual(self.request("/../dashboard_server.py")[0], 404)
        self.assertEqual(self.calls, [])

    def test_logs_are_bounded_and_job_scoped(self):
        code, _, body = self.request(f"/api/jobs/{ID}/logs", authenticated=True)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["stdout"], "hello")
        self.assertIn(("logs", {"job_id":ID, "max_bytes":32000}), self.calls)


if __name__ == "__main__":
    unittest.main()
