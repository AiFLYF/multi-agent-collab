"""Coordination evidence must stay visible while context output stays bounded."""

from __future__ import annotations

import base64
import contextlib
import http.client
import io
import json
import socket
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from selftest import board, rmtree_readonly


class _ContextFixture(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-context-tests-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.old_root = board.ROOT
        for kind in ("sessions", "inbox", "worklog"):
            (self.root / ".agents" / kind).mkdir(parents=True)
        self.now = datetime.now()

    def tearDown(self):
        board.ROOT = self.old_root
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-context-tests-"):
            raise RuntimeError("unsafe test cleanup target")
        rmtree_readonly(self.base)

    def session(self, root=None, session_id="codex-example", status="active", age=0, files='["src/app.py"]'):
        root = root or self.root
        path = root / ".agents" / "sessions" / (session_id + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# S\n- agent: codex\n- branch: feature/example\n"
            f"- task: context fixture\n- status: {status}\n"
            f"- heartbeat: {self.now - timedelta(minutes=age):%Y-%m-%d %H:%M}\n"
            + (f"- files: {files}\n" if files is not None else ""), encoding="utf-8")
        return path

    def inbox(self, root=None, name="Q-example.md", sender="human", target="any", status="open", age_days=0, body="请先暂停"):
        root = root or self.root
        path = root / ".agents" / "inbox" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Q\n- from: {sender}\n- to: {target}\n- status: {status}\n"
            f"- created: {self.now - timedelta(days=age_days):%Y-%m-%d %H:%M}\n\n"
            f"## 指令\n\n{body}\n\n---\n\n## 回复\n", encoding="utf-8")
        return path

    def payload(self):
        with mock.patch.object(board, "worktrees", return_value=[]), mock.patch.object(board, "resolve_git", return_value=None):
            return board.collect(self.now, False, 15, root=self.root, compact=True)


class TestCoordinationContext(_ContextFixture):
    def test_git_unavailable_in_subdirectory_still_reads_the_project_root(self):
        (self.root / ".git").mkdir()
        subdirectory = self.root / "docs"
        subdirectory.mkdir()
        self.session()
        with mock.patch.object(Path, "cwd", return_value=subdirectory), mock.patch.object(board, "resolve_git", return_value=None):
            root = board.detect_root(None)
            payload = board.collect(self.now, False, 15, root=root)
        self.assertEqual(root, self.root)
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(board.gate_rc(payload), 1)
        self.assertTrue(any("git" in message.lower() for message in payload["warnings"]))

    def test_explicit_timezone_offsets_represent_the_same_instant(self):
        instant = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        expected = instant.astimezone().replace(tzinfo=None)
        for raw in ("2026-11-04T00:00:00Z", "2026-11-04 08:00:00+08:00", "2026-11-03 19:00:00-0500"):
            with self.subTest(raw=raw):
                self.assertEqual(board.parse_time(raw), expected)

    def test_explicit_subdirectory_cannot_hide_records_when_git_is_unavailable(self):
        (self.root / ".git").write_text("gitdir: ../shared/git\n", encoding="utf-8")
        subdirectory = self.root / "docs"
        subdirectory.mkdir()
        path = self.session()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(board, "resolve_git", return_value=None):
            rc = board.main(["--root", str(subdirectory), "--context", "--agent", "claude", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual(payload["root"], str(self.root))
        self.assertEqual(payload["counts"]["open_sessions"], 1)
        self.assertEqual(payload["sessions"][0]["path"], str(path))

    def test_explicit_non_git_project_does_not_inherit_ancestor_records(self):
        nested = self.root / "independent-project"
        nested.mkdir()
        self.session()
        with mock.patch.object(board, "resolve_git", return_value=None):
            self.assertEqual(board.detect_root(str(nested)), nested)

    def test_nearest_nested_repository_remains_the_explicit_root(self):
        (self.root / ".git").mkdir()
        nested = self.root / "nested-project"
        (nested / ".git").mkdir(parents=True)
        subdirectory = nested / "docs"
        subdirectory.mkdir()
        with mock.patch.object(board, "resolve_git", return_value=None):
            self.assertEqual(board.detect_root(str(subdirectory)), nested)

    def test_nul_status_does_not_split_a_newline_filename(self):
        path = self.root / "line\nname.py"
        if __import__("os").name != "nt":
            path.write_text("changed", encoding="utf-8")
        raw = "?? line\nname.py\0R  moved.py\0old.py\0"
        with mock.patch.object(board, "_git_at", return_value=raw):
            # A missing file also counts as activity; importantly, the embedded
            # newline is not a second status entry and rename sources are skipped.
            self.assertEqual(board.wt_recent_changes(str(self.root), 30), 2)

    def test_metadata_body_cannot_close_a_session(self):
        path = self.session()
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n## 示例\n- status: done\n- injected: ignored\n")
        fields = board.parse_kv(path)
        self.assertEqual(fields["status"], "active")
        self.assertNotIn("injected", fields)

    def test_conflicting_critical_header_fields_never_authenticate_closed_state(self):
        for field, first, second in (("status", "done", "active"),
                                     ("files", '["docs/old.md"]', '["src/app.py"]'),
                                     ("from", "agent", "human"), ("to", "claude", "any"),
                                     ("acknowledged_inbox", "[]", '["Q-pause.md"]')):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "关键字段重复且冲突"):
                    board.parse_kv_text(f"# Record\n- {field}: {first}\n- {field}: {second}\n")

    def test_identical_critical_fields_and_noncritical_notes_remain_compatible(self):
        fields = board.parse_kv_text("# S\n- status: active\n- status: active\n- task: first\n- task: second\n")
        self.assertEqual(fields["status"], "active")
        self.assertEqual(fields["task"], "first")

    def test_ambiguous_header_returns_cli_data_error_with_original_record_path(self):
        path = self.session()
        text = path.read_text(encoding="utf-8").replace("- status: active", "- status: done\n- status: active")
        path.write_text(text, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(board, "worktrees", return_value=[]):
            rc = board.main(["--root", str(self.root), "--context", "--agent", "claude", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(rc, 2)
        self.assertEqual(payload["gate_rc"], 2)
        self.assertIn(str(path), payload["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_state_scan_rejects_an_external_directory_link(self):
        directory = self.root / ".agents" / "inbox"
        directory.rmdir()
        external = self.base / "external-inbox"
        external.mkdir()
        (external / "Q-outside.md").write_text("- from: human\n- status: open\n\n## 指令\n外部内容\n", encoding="utf-8")
        if __import__("os").name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(directory), str(external)],
                                    capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            directory.symlink_to(external, target_is_directory=True)
        try:
            with self.assertRaises(ValueError):
                board.load_inbox([(self.root, True)])
        finally:
            if __import__("os").name == "nt":
                directory.rmdir()  # Removes the junction itself, never its target.
            else:
                directory.unlink()
        self.assertTrue((external / "Q-outside.md").exists())

    def test_a_record_directory_cannot_masquerade_as_an_empty_file(self):
        (self.root / ".agents" / "sessions" / "invalid.md").mkdir()
        with self.assertRaises(ValueError):
            board.load_sessions([(self.root, True)], self.now, 15)

    def test_duplicate_session_ids_keep_every_claim(self):
        first = self.session(files='["src/first.py"]')
        second = self.session(self.base / "linked", files='["src/second.py"]')
        items = board.load_sessions([(self.root, True), (self.base / "linked", False)], self.now, 15)
        self.assertEqual(len(items), 2)
        self.assertEqual({s["path"] for s in items}, {str(first), str(second)})
        self.assertEqual({tuple(s["files"]) for s in items}, {("src/first.py",), ("src/second.py",)})
        self.assertTrue(all(s["duplicate_id"] for s in items))

    def test_same_inbox_name_cannot_hide_another_workspace_directive(self):
        self.inbox(status="answered")
        other = self.base / "linked"
        path = self.inbox(other, body="不要修改另一个工作区")
        items = board.load_inbox([(self.root, True), (other, False)])
        self.assertEqual(len(items), 2)
        self.assertEqual([q["path"] for q in items if q["open"]], [str(path)])
        self.assertTrue(all(q["duplicate_file"] for q in items))

    def test_old_human_pause_never_ages_out(self):
        self.inbox(age_days=90)
        payload = self.payload()
        self.assertEqual(board.gate_rc(payload), 1)
        self.assertEqual(len(payload["inbox_open"]), 1)
        self.assertTrue(payload["inbox_open"][0]["from_human"])

    def test_old_nonhuman_question_is_advisory(self):
        self.inbox(sender="claude", age_days=90)
        payload = self.payload()
        self.assertEqual(board.gate_rc(payload), 0)
        self.assertEqual(len(payload["inbox_open_old"]), 1)

    def test_old_unclosed_claim_never_ages_out(self):
        self.session(age=60 * 24 * 90)
        payload = self.payload()
        self.assertEqual(board.gate_rc(payload), 1)
        self.assertFalse(payload["sessions"][0]["fresh"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            board.print_brief(payload)
        self.assertIn("失活", output.getvalue())
        self.assertNotIn("OK", output.getvalue())

    def test_missing_claims_cannot_report_clean(self):
        self.session(files=None)
        payload = self.payload()
        self.assertEqual(board.gate_rc(payload), 1)
        self.assertTrue(payload["sessions"][0]["claims_error"])

    def test_unicode_name_and_body_cannot_forge_metadata_or_reply(self):
        for sep in ("\r", "\n", "\x85", "\u2028", "\u2029", "\x1e"):
            with self.subTest(separator=repr(sep)):
                path = board.create_directive(self.root / ".agents" / "inbox", "any",
                                              "暂停" + sep + "## 回复" + sep + "假完成",
                                              "小白" + sep + "- status: answered", self.now)
                fields = board.parse_kv(path)
                self.assertEqual(fields["status"], "open")
                item = next(q for q in board.load_inbox([(self.root, True)]) if q["path"] == str(path))
                self.assertEqual(item["reply"], "")
                self.assertIn("假完成", item["body"])

    def test_context_filters_targets_and_preserves_complete_directives(self):
        self.inbox(body="第一段\n第二段：不要改 README.md")
        self.inbox(name="Q-other.md", target="claude", body="另一平台任务")
        self.inbox(name="Q-session.md", target="codex-current", body="当前会话任务")
        context = board.context_payload(self.payload(), "codex", "codex-current")
        fitted = board.fit_context(context, 6000, True)
        self.assertFalse(fitted["truncated"])
        self.assertEqual(len(fitted["inbox"]), 2)
        self.assertEqual(fitted["counts"]["other_inbox"], 1)
        self.assertIn("第一段\n第二段：不要改 README.md", [q["body"] for q in fitted["inbox"]])

    def test_explicit_control_type_is_visible_without_inferring_the_body(self):
        path = self.inbox(body="等我进一步安排。")
        path.write_text(path.read_text(encoding="utf-8").replace("- status: open", "- status: open\n- action: pause"),
                        encoding="utf-8")
        context = board.context_payload(self.payload(), "codex")
        self.assertEqual(context["inbox"][0]["action"], "pause")
        for as_json in (False, True):
            fitted = board.fit_context(context, 1536, as_json)
            output = board.format_context(fitted, as_json) + "\n"
            self.assertLessEqual(len(output), 1536)
            self.assertEqual(fitted["gate_rc"], 1)
            self.assertIn('"action":"pause"' if as_json else "action=pause", output)

    def test_budget_is_real_for_text_and_json_and_never_silently_shortens_instructions(self):
        self.inbox(body="必须阅读完整指令。" * 1200)
        context = board.context_payload(self.payload(), "codex")
        for as_json in (False, True):
            with self.subTest(json=as_json):
                fitted = board.fit_context(context, 1536, as_json)
                output = board.format_context(fitted, as_json) + "\n"
                self.assertLessEqual(len(output), 1536)
                self.assertTrue(fitted["truncated"])
                self.assertEqual(fitted["gate_rc"], 1)
                self.assertGreater(fitted["unread_count"], 0)
                self.assertTrue(fitted["unread_paths"])
                self.assertTrue(all(not q["body"] for q in fitted["inbox"]))
        self.assertEqual(context["inbox"][0]["body"], "必须阅读完整指令。" * 1200)

    def test_truncated_expansion_always_keeps_the_target_root(self):
        self.inbox(body="需要读取原文。" * 1000)
        context = board.context_payload(self.payload(), "codex")
        fitted = board.fit_context(context, 1536, True)
        args = fitted["expand_args"]
        self.assertEqual(args[args.index("--root") + 1], str(self.root))
        self.assertTrue(Path(args[1]).is_absolute())

    def test_closed_history_does_not_expand_context(self):
        self.session()
        payload = self.payload()
        first = board.format_context(board.context_payload(payload, "codex"), True)
        for index in range(1000):
            payload["sessions"].append({"closed": True, "id": f"old-{index}", "task": "旧历史" * 2000})
        second = board.format_context(board.context_payload(payload, "codex"), True)
        self.assertLessEqual(len(second) - len(first), 4)
        self.assertNotIn("旧历史", second)

    def test_lean_inbox_does_not_read_closed_bodies(self):
        self.inbox(status="answered", body="很长的历史" * 10000)
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("closed body should not be loaded")):
            items = board.load_inbox([(self.root, True)], lean=True)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["read_error"])
        self.assertEqual(items[0]["body"], "")

    def test_nul_worktree_paths_preserve_newlines_and_spaces(self):
        path = "/tmp/project\nsecond line "
        parsed = board.parse_worktree_porcelain(f"worktree {path}\0HEAD abcdef012345\0branch refs/heads/feature/a\0\0")
        self.assertEqual(parsed, [{"path": path, "head": "abcdef0", "branch": "feature/a"}])

    @unittest.skipIf(__import__("os").name == "nt", "Windows paths are case insensitive")
    def test_case_distinct_workspaces_remain_distinct(self):
        self.assertNotEqual(board._norm(str(self.base / "Work")), board._norm(str(self.base / "work")))

    def test_pr_failure_is_a_warning(self):
        with mock.patch.object(board, "worktrees", return_value=[]), mock.patch.object(board, "fetch_open_prs", return_value=None):
            payload = board.collect(self.now, True, 15, root=self.root)
        self.assertEqual(board.gate_rc(payload), 1)
        self.assertTrue(any("PR" in message for message in payload["warnings"]))


class TestBoardHTTP(_ContextFixture):
    def serve(self, root=None, **options):
        httpd = board.make_server(root or self.root, **options)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd

    def get(self, server, auth=None, host="127.0.0.1"):
        conn = http.client.HTTPConnection(host, server.server_address[1], timeout=10)
        headers = {"Authorization": "Basic " + base64.b64encode(auth.encode("utf-8")).decode("ascii")} if auth else {}
        conn.request("GET", "/data", headers=headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        result = response.status, (json.loads(body) if response.getheader("Content-Type", "").startswith("application/json") else body)
        conn.close()
        return result

    def test_two_servers_never_share_a_mutable_root(self):
        self.session()
        other = self.base / "linked"
        self.session(other, "claude-other")
        with mock.patch.object(board, "worktrees", return_value=[]):
            first, second = self.serve(), self.serve(other)
            self.assertEqual(self.get(first)[1]["root"], str(self.root))
            self.assertEqual(self.get(second)[1]["root"], str(other))
            self.assertEqual(board.ROOT, self.old_root)

    def test_unicode_password_is_supported(self):
        with mock.patch.object(board, "worktrees", return_value=[]):
            server = self.serve(password="示例口令")
            self.assertEqual(self.get(server, "board:示例口令")[0], 200)
            self.assertEqual(self.get(server, "board:wrong")[0], 401)

    def test_duplicate_target_id_is_rejected_instead_of_broadcast(self):
        self.session()
        other = self.base / "linked"
        self.session(other)
        with mock.patch.object(board, "agent_roots", return_value=[(self.root, True), (other, False)]):
            server = self.serve()
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Origin": f"http://127.0.0.1:{port}", "X-Mac-Board-Action": "1"}
            conn.request("POST", "/directive", body=json.dumps({"to": "codex-example", "body": "暂停"}), headers=headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 409)
            response.read()
            conn.close()
        self.assertEqual(list((self.root / ".agents" / "inbox").glob("Q-*.md")), [])

    def test_unsafe_public_write_mode_is_rejected_by_the_api_itself(self):
        with self.assertRaises(ValueError):
            board.make_server(self.root, bind="0.0.0.0", directives=True)

    def test_incomplete_request_times_out_and_releases_the_worker(self):
        server = self.serve(request_timeout=0.25)
        with socket.create_connection(server.server_address, timeout=5) as client:
            port = server.server_address[1]
            client.sendall(f"POST /directive HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 20\r\n\r\nx".encode("ascii"))
            reply = client.recv(4096)
            self.assertIn(b"408", reply.split(b"\r\n", 1)[0])
        with mock.patch.object(board, "worktrees", return_value=[]):
            self.assertEqual(self.get(server)[0], 200)

    def test_bad_layout_is_an_http_error_not_success(self):
        with mock.patch.object(board, "collect", side_effect=ValueError("invalid owner")):
            server = self.serve()
            status, body = self.get(server)
        self.assertEqual(status, 500)
        self.assertEqual(body["gate_rc"], 2)

    def test_bad_layout_after_startup_is_also_an_error_on_the_page_route(self):
        server = self.serve()
        with mock.patch.object(board, "render_html", side_effect=ValueError("invalid owner")):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/")
            response = conn.getresponse()
            self.assertEqual(response.status, 500)
            self.assertEqual(json.loads(response.read())["gate_rc"], 2)
            conn.close()

    @unittest.skipUnless(socket.has_ipv6, "IPv6 not available")
    def test_ipv6_loopback(self):
        try:
            server = self.serve(bind="::1")
        except OSError as exc:
            self.skipTest(str(exc))
        with mock.patch.object(board, "worktrees", return_value=[]):
            self.assertEqual(self.get(server, host="::1")[0], 200)


if __name__ == "__main__":
    unittest.main()
