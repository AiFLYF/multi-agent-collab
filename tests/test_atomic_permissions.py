"""Atomic state writes retain real permissions and stop on mode-copy errors."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from selftest import board


class TestAtomicPermissions(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Windows does not represent POSIX permission bits")
    def test_existing_permissions_survive_real_atomic_replacement(self):
        with tempfile.TemporaryDirectory(prefix="mac-atomic-mode-") as temporary:
            path = Path(temporary) / "record.md"
            for mode in (0o600, 0o640, 0o644, 0o750, 0o440):
                with self.subTest(mode=oct(mode)):
                    path.write_bytes(b"before\n")
                    path.chmod(mode)
                    previous_inode = path.stat().st_ino
                    board._write_lf_atomic(path, "after 中文\n")
                    self.assertEqual(path.read_bytes(), "after 中文\n".encode("utf-8"))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                    self.assertNotEqual(path.stat().st_ino, previous_inode)
                    path.chmod(0o600)
            self.assertEqual(list(path.parent.iterdir()), [path])

    @unittest.skipIf(os.name == "nt", "Windows does not represent POSIX permission bits")
    def test_new_private_record_keeps_mkstemp_owner_only_permissions(self):
        with tempfile.TemporaryDirectory(prefix="mac-atomic-default-") as temporary:
            path = Path(temporary) / "nested" / "record.md"
            board._write_lf_atomic(path, "new\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_bytes(), b"new\n")

    def test_mode_copy_failure_preserves_target_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory(prefix="mac-atomic-fail-") as temporary:
            path = Path(temporary) / "record.md"
            path.write_bytes(b"before\n")
            before = path.stat()
            operation = "fchmod" if hasattr(os, "fchmod") else "chmod"
            with mock.patch.object(board.os, operation, side_effect=PermissionError("mode copy denied")):
                with self.assertRaisesRegex(PermissionError, "mode copy denied"):
                    board._write_lf_atomic(path, "after\n")
            self.assertEqual(path.read_bytes(), b"before\n")
            self.assertEqual(path.stat().st_ino, before.st_ino)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(before.st_mode))
            self.assertEqual(list(path.parent.iterdir()), [path])

    @unittest.skipUnless(hasattr(os, "fchmod"), "This runtime already uses the chmod fallback")
    def test_chmod_fallback_copies_mode_and_refuses_errors(self):
        with tempfile.TemporaryDirectory(prefix="mac-atomic-fallback-") as temporary:
            path = Path(temporary) / "record.md"
            path.write_bytes(b"before\n")
            path.chmod(0o640)
            with mock.patch.object(board.os, "fchmod", None):
                board._write_lf_atomic(path, "fallback\n")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                with mock.patch.object(board.os, "chmod", side_effect=PermissionError("fallback denied")):
                    with self.assertRaisesRegex(PermissionError, "fallback denied"):
                        board._write_lf_atomic(path, "never replace\n")
            self.assertEqual(path.read_bytes(), b"fallback\n")
            self.assertEqual(list(path.parent.iterdir()), [path])
