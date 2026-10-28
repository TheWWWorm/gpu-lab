# SPDX-License-Identifier: Apache-2.0
import json
import unittest
from unittest.mock import patch

from gpu_lab import rpc


class RoutingTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    @patch("gpu_lab.worker_rpc", return_value={"queued": []})
    @patch("gpu_lab.Path")
    def test_configured_dispatcher_is_used_for_all_client_calls(self, path, transport):
        path.return_value.exists.return_value = True
        path.return_value.read_text.return_value = json.dumps({"dispatcher_socket": "/run/queue.sock"})
        self.assertEqual(rpc("status"), {"queued": []})
        transport.assert_called_once_with("status", None, socket_path="/run/queue.sock")

    @patch.dict("os.environ", {}, clear=True)
    @patch("gpu_lab.worker_rpc", return_value={})
    @patch("gpu_lab.Path")
    def test_explicit_worker_socket_stays_available_for_administration(self, path, transport):
        rpc("status", socket_path="/run/worker.sock")
        transport.assert_called_once_with("status", None, "/run/worker.sock")
        path.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    @patch("gpu_lab.worker_rpc", side_effect=RuntimeError("dispatcher offline"))
    @patch("gpu_lab.Path")
    def test_dispatcher_outage_never_bypasses_shared_queue(self, path, transport):
        path.return_value.exists.return_value = True
        path.return_value.read_text.return_value = json.dumps({"dispatcher_socket": "/run/queue.sock"})
        with self.assertRaisesRegex(RuntimeError, "offline"):
            rpc("submit", {"argv":["true"], "cwd":"/home/test-user"})
        self.assertEqual(transport.call_count, 1)


if __name__ == "__main__":
    unittest.main()
