"""Tracked commands preserve results, stay attributable and stop on lost gates."""
import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "tools"))
import agents_activity as activity
import agents_run as runner
import agents_session as sessions

PY = sys.executable


class TrackedCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="mac-run-tests-")
        cls.root = Path(cls.temporary.name).resolve()
        cls.git = sessions.board.resolve_git()
        if not cls.git:
            cls.temporary.cleanup()
            raise unittest.SkipTest("Git required for real command ownership")
        for command in (["init", "-q", "-b", "main"], ["config", "user.name", "run-test"],
                        ["config", "user.email", "run-test@example.invalid"]):
            subprocess.run([cls.git, *command], cwd=str(cls.root), check=True, capture_output=True, timeout=30)
        (cls.root / "app.py").write_text("pass\n", encoding="utf-8")
        subprocess.run([cls.git, "add", "app.py"], cwd=str(cls.root), check=True, capture_output=True, timeout=30)
        subprocess.run([cls.git, "commit", "-qm", "temporary fixture"], cwd=str(cls.root), check=True, capture_output=True, timeout=30)
        subprocess.run([cls.git, "switch", "-c", "feature/tracked-command"], cwd=str(cls.root), check=True, capture_output=True, timeout=30)
        cls.state = cls.root / ".agents"
        (cls.state / "sessions").mkdir(parents=True)
        (cls.state / "inbox").mkdir()
        cls.record_path = cls.state / "sessions/codex-run-test.md"

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        for path in (self.state / "inbox").glob("Q-*.md"):
            path.unlink()
        self.write_record()

    def write_record(self, status="active", files=None):
        if files is None:
            files = [] if status == "standby" else ["app.py"]
        old = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        self.record_path.write_text("# Session\n\n- agent: codex\n- branch: feature/tracked-command\n"
                                    "- task: tracked command\n- status: " + status + "\n- files: " + json.dumps(files)
                                    + "\n- heartbeat: " + old + "\n- workspace: " + str(self.root)
                                    + "\n- acknowledged_inbox: []\n- acknowledged_inbox_versions: {}\n", encoding="utf-8")

    def cli(self, command, agent="codex", identifier="codex-run-test"):
        result = subprocess.run([PY, "-B", str(KIT / "tools/agents_session.py"), "run", identifier,
                                 "--agent", agent, "--", *command], cwd=str(self.root),
                                capture_output=True, timeout=30)
        # Decode after both pipes are collected: reader-thread decode failures
        # must fail the test, rather than disappear behind the process exit code.
        result.stdout = result.stdout.decode("utf-8")
        result.stderr = result.stderr.decode("utf-8")
        return result

    def args(self, command):
        return argparse.Namespace(root=str(self.root), agent="codex", session="codex-run-test", command=command)

    def test_child_json_and_nonzero_exit_are_preserved(self):
        result = self.cli([PY, "-c", "import json,sys; print(json.dumps({'ok':False,'code':7})); sys.exit(7)"])
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": False, "code": 7})
        record = sessions.board.parse_kv(self.record_path)
        self.assertEqual(record["status"], "active")
        self.assertEqual(json.loads(record["files"]), ["app.py"])
        self.assertEqual(record["last_command"], "agents_session.run")
        self.assertIn("last_activity", record)

    @unittest.skipUnless(os.name == "nt", "Windows native DWORD exit statuses")
    def test_native_high_bit_exit_code_is_preserved_through_both_wrappers(self):
        command = [PY, "-c", "import ctypes; ctypes.windll.kernel32.ExitProcess(0xC0000005)"]
        original = subprocess.run(command, cwd=str(self.root), capture_output=True, timeout=10)
        result = self.cli(command)
        self.assertEqual(original.returncode, 0xC0000005)
        self.assertEqual(result.returncode, original.returncode, result.stderr)
        self.assertEqual(result.stdout, original.stdout.decode("utf-8"))
        self.assertEqual(result.stderr, original.stderr.decode("utf-8"))

    def test_arguments_are_literal_and_not_saved_in_session(self):
        values = ["literal $(Write-Output wrong)", "quoted space", "secret-argument-for-test"]
        result = self.cli([PY, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", *values])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), values)
        text = self.record_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-argument-for-test", text)
        self.assertNotIn("Write-Output", text)

    def test_missing_executable_returns_data_error_without_finishing_task(self):
        result = self.cli([str(self.root / "missing-executable")])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("[进程管理失败]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(sessions.board.parse_kv(self.record_path)["status"], "active")

    def test_blocked_and_standby_never_execute_the_command(self):
        marker = self.root / "must-not-run.txt"
        for status in ("blocked", "standby"):
            with self.subTest(status=status):
                self.write_record(status)
                result = self.cli([PY, "-c", "from pathlib import Path; Path('must-not-run.txt').write_text('wrong')"])
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(marker.exists())
                self.assertEqual(sessions.board.parse_kv(self.record_path)["status"], status)

    def test_done_and_foreign_owner_cannot_execute_or_revive(self):
        self.write_record("done")
        before = self.record_path.read_bytes()
        result = self.cli([PY, "-c", "print('must-not-run')"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, self.record_path.read_bytes())
        self.write_record()
        before = self.record_path.read_bytes()
        result = self.cli([PY, "-c", "print('must-not-run')"], agent="claude")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, self.record_path.read_bytes())

    def test_empty_active_scope_is_rejected(self):
        self.write_record(files=[])
        result = self.cli([PY, "-c", "print('must-not-run')"])
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("must-not-run", result.stdout)

    def test_separator_is_required_before_child_arguments(self):
        result = subprocess.run([PY, "-B", str(KIT / "tools/agents_session.py"), "run", "codex-run-test", "--agent", "codex"],
                                cwd=str(self.root), capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--", result.stderr)

    def test_unavailable_output_never_executes_or_creates_job_metadata(self):
        before = self.record_path.read_bytes()
        with mock.patch.object(runner.sys, "stdout", None), self.assertRaisesRegex(OSError, "未执行命令"):
            runner.execute_tracked(self.args([PY, "-c", "raise AssertionError('must not execute')"]))
        self.assertEqual(self.record_path.read_bytes(), before)

    def test_ordinary_human_note_after_launch_preserves_result_and_next_command_gate(self):
        message = self.state / "inbox/Q-after-launch.md"
        original_launch = runner._launch
        timer = None
        def write_message():
            message.write_text("# Follow-up\n- from: human-test\n- to: codex-run-test\n- status: open\n"
                               "- action: message\n\n## 问题\n当前命令结束后补充实际结果。\n", encoding="utf-8")
        def launch(command, **options):
            nonlocal timer
            child = original_launch(command, **options)
            timer = threading.Timer(0.05, write_message)
            timer.start()
            return child
        script = "import json,time; from pathlib import Path; time.sleep(1.2); Path('app.py').write_text('CMD_DONE'); print(json.dumps({'code':7})); raise SystemExit(7)"
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            try:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                        mock.patch.object(runner, "_launch", side_effect=launch), \
                        mock.patch.object(runner, "ACTIVITY_INTERVAL", 0.1), mock.patch.object(runner, "WAIT_SLICE", 0.02):
                    result = runner.execute_tracked(self.args([PY, "-c", script]))
            finally:
                if timer is not None:
                    timer.join()
            output.seek(0)
            errors.seek(0)
            self.assertEqual(result, 7)
            self.assertEqual(json.loads(output.read()), {"code": 7})
            self.assertEqual(errors.read().count("提醒：inbox"), 1)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "CMD_DONE")
        record = sessions.board.parse_kv(self.record_path)
        self.assertEqual(record["status"], "active")
        self.assertEqual(json.loads(record["running_jobs"]), {})
        next_result = self.cli([PY, "-c", "print('must not run before reading follow-up')"])
        self.assertEqual(next_result.returncode, 1)
        self.assertEqual(next_result.stdout, "")

    def test_changed_same_count_notice_is_delivered_without_repeating_each_poll(self):
        message = self.state / "inbox/Q-ai-follow-up.md"
        text = "# AI note\n- from: claude\n- to: codex-run-test\n- status: open\n- action: message\n\n## 问题\nversion_A\n"
        message.write_bytes(text.encode("utf-8"))
        original_time = message.stat()
        original_launch = runner._launch
        timer = None
        def edit_message():
            message.write_bytes(text.replace("version_A", "version_B").encode("utf-8"))
            os.utime(message, ns=(original_time.st_atime_ns, original_time.st_mtime_ns))
        def launch(command, **options):
            nonlocal timer
            child = original_launch(command, **options)
            timer = threading.Timer(0.15, edit_message)
            timer.start()
            return child
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            try:
                with contextlib.redirect_stderr(errors), mock.patch.object(runner, "_launch", side_effect=launch), \
                        mock.patch.object(runner, "ACTIVITY_INTERVAL", 0.1), mock.patch.object(runner, "WAIT_SLICE", 0.02):
                    result = runner.execute_tracked(self.args([PY, "-c", "import time; time.sleep(1.2)"]))
            finally:
                if timer is not None:
                    timer.join()
            errors.seek(0)
            self.assertEqual(result, 0)
            self.assertEqual(errors.read().count("提醒：inbox"), 2)

    def test_supervised_long_command_updates_activity_and_keeps_ownership(self):
        tracked = mock.Mock(wraps=activity.touch_activity)
        with mock.patch.object(runner, "ACTIVITY_INTERVAL", 0.3), mock.patch.object(runner, "WAIT_SLICE", 0.05), \
                mock.patch.object(activity, "touch_activity", tracked):
            result = runner.execute_tracked(self.args([PY, "-c", "import time; time.sleep(1.3)"]))
        self.assertEqual(result, 0)
        self.assertGreaterEqual(tracked.call_count, 3)
        record = sessions.board.parse_kv(self.record_path)
        self.assertEqual(record["status"], "active")
        self.assertEqual(json.loads(record["files"]), ["app.py"])

    def test_control_arriving_during_execution_stops_owned_process_tree(self):
        marker = self.root / "descendant-must-not-finish.txt"
        if marker.exists():
            marker.unlink()
        child_script = "import time; from pathlib import Path; time.sleep(3); Path('descendant-must-not-finish.txt').write_text('wrong')"
        parent_script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child_script) + "]); time.sleep(8)"
        def pause():
            (self.state / "inbox/Q-live-pause.md").write_text("# Pause\n\n- from: human-test\n- to: codex-run-test\n"
                                                         "- status: open\n- action: pause\n\n## 问题\nStop the tracked test\n", encoding="utf-8")
        timer = threading.Timer(0.3, pause)
        timer.start()
        try:
            with mock.patch.object(runner, "ACTIVITY_INTERVAL", 0.2), mock.patch.object(runner, "WAIT_SLICE", 0.05):
                with self.assertRaises(sessions.ConflictError):
                    runner.execute_tracked(self.args([PY, "-c", parent_script]))
        finally:
            timer.join()
        time.sleep(3.2)
        self.assertFalse(marker.exists(), "A tracked descendant survived cancellation")
        record = sessions.board.parse_kv(self.record_path)
        self.assertEqual(record["status"], "active")
        self.assertEqual(json.loads(record["files"]), ["app.py"])

    def test_keyboard_interrupt_returns_130_and_cleans_owned_child(self):
        options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        child = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"], **options)
        original_wait = child.wait
        interrupted = False
        def wait(timeout=None):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(timeout=timeout)
        try:
            with mock.patch.object(runner, "_launch", return_value=child), \
                    mock.patch.object(child, "wait", side_effect=wait), \
                    mock.patch.object(runner, "_stop_child", wraps=runner._stop_child) as stop:
                result = runner.execute_tracked(self.args([PY, "-c", "pass"]))
        finally:
            if child.poll() is None:
                runner._stop_child(child)
        self.assertEqual(result, 130)
        stop.assert_called_once_with(child)
        self.assertIsNotNone(child.poll())


if __name__ == "__main__":
    unittest.main()
