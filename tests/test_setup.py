# SPDX-License-Identifier: Apache-2.0
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import register_clients
import setup_gpu_lab

UUID0 = "GPU-00000000-0000-0000-0000-000000000001"
UUID1 = "GPU-00000000-0000-0000-0000-000000000002"


class SetupTests(unittest.TestCase):
    def test_plan_uses_supplied_account_and_distinct_gpu_assignments(self):
        commands = setup_gpu_lab.plan("alice", UUID0, UUID1)
        self.assertIn("alice", commands[0])
        self.assertEqual(commands[0][-1], UUID1)
        self.assertEqual(commands[1][-1], UUID0)
        self.assertEqual(Path(commands[-1][1]).name, "install_dispatch.py")

    def test_duplicate_or_invalid_identity_is_rejected(self):
        for user, a, b in [("root\nUser=alice",UUID0,UUID1),("alice",UUID0,UUID0),("alice","0",UUID1)]:
            with self.assertRaises(ValueError):
                setup_gpu_lab.plan(user,a,b)

    @patch("setup_gpu_lab.subprocess.run")
    @patch("setup_gpu_lab.prerequisites")
    def test_default_preview_does_not_probe_write_or_start_services(self, preflight, run):
        with contextlib.redirect_stdout(io.StringIO()):
            result=setup_gpu_lab.main(["--user","alice","--gpu0",UUID0,"--gpu1",UUID1])
        self.assertEqual(result,0)
        preflight.assert_not_called()
        run.assert_not_called()

    @patch("setup_gpu_lab.os.geteuid", return_value=0)
    @patch("setup_gpu_lab.prerequisites", return_value=[])
    @patch("setup_gpu_lab.Path.exists", return_value=True)
    @patch("setup_gpu_lab.subprocess.run")
    def test_apply_refuses_existing_installation_without_starting_commands(self, run, exists, preflight, uid):
        with contextlib.redirect_stderr(io.StringIO()):
            result=setup_gpu_lab.main(["--user","alice","--gpu0",UUID0,"--gpu1",UUID1,"--apply"])
        self.assertEqual(result,1)
        run.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_claude_registration_preserves_unrelated_settings(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            path=home/".claude.json"
            path.write_text(json.dumps({"custom":42,"mcpServers":{"existing":{"command":"test"}}}))
            register_clients.register_claude(home,"/usr/local/bin/gpu-lab")
            data=json.loads(path.read_text())
            self.assertEqual(data["custom"],42)
            self.assertIn("existing",data["mcpServers"])
            self.assertEqual(data["mcpServers"]["gpu_lab"]["args"],["mcp"])
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(list(home.glob(".claude.json.before-*"))),1)

    def test_instruction_update_replaces_only_its_marked_block(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"AGENTS.md"
            path.write_text("Before\n<!-- gpu-lab:start -->old<!-- gpu-lab:end -->\nAfter\n")
            block="<!-- gpu-lab:start -->new<!-- gpu-lab:end -->\n"
            register_clients.update_instructions(path,block)
            self.assertEqual(path.read_text(),"Before\n"+block.rstrip()+"\nAfter\n")

    @patch("register_clients.register_claude")
    @patch("register_clients.subprocess.run")
    def test_client_preview_does_not_mutate_configuration(self, run, claude):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(register_clients.main(["--client","claude"]),0)
        run.assert_not_called()
        claude.assert_not_called()


if __name__ == "__main__":
    unittest.main()
