"""Recovery instructions and lifecycle templates must preserve safety contracts."""

from __future__ import annotations

import re
import unittest
from urllib.parse import unquote, urlsplit

from selftest import KIT, board


DOCUMENTS = (
    "docs/TROUBLESHOOTING.md",
    "docs/README.md",
    "templates/session.md",
    "docs/examples/session-example.md",
)


def read(relative):
    return (KIT / relative).read_text(encoding="utf-8")


def fenced_blocks(text):
    # Blockquoted historical transcripts use the same fence syntax.
    normalized = re.sub(r"(?m)^> ?", "", text)
    return re.findall(r"(?m)^```[^\n]*\n(.*?)^```\s*$", normalized, re.S)


def markdown_anchors(text):
    anchors = set(re.findall(r'<a\s+id="([^"]+)"', text))
    counts = {}
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+)$", text):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug + ("-" + str(count) if count else ""))
    return anchors


def local_links(relative):
    source = KIT / relative
    for destination in re.findall(r"\[[^\]\n]+\]\(([^)]+)\)", read(relative)):
        url = urlsplit(destination)
        if url.scheme or url.netloc:
            continue
        target = source.parent / unquote(url.path) if url.path else source
        yield destination, target, unquote(url.fragment)


class TestDocumentContracts(unittest.TestCase):
    def test_troubleshooting_has_no_executable_reference_repair_recipe(self):
        forbidden = (
            r"\bgit\s+(?:update-ref|symbolic-ref|pack-refs|reset)\b",
            r"\bgit\s+restore\s+\.(?:\s|$)",
            r"\bgit\s+push\b[^\n]*:refs/heads/",
            r"\b(?:rm|del|Remove-Item)\b[^\n]*\.git[/\\]",
            r"\bgh\s+api\b[^\n]*/git/(?:blobs|trees|commits|refs)(?:[/\s]|$)",
            r"\bhash-object\b[^\n]*[\"']-w[\"']",
            r"\bopen\([^\n]*[\"'](?:w|wb|a|ab)[\"']",
        )
        for block in fenced_blocks(read(DOCUMENTS[0])):
            for pattern in forbidden:
                self.assertNotRegex(block, pattern)

    def test_old_reference_incidents_link_to_current_stop_and_diagnose_boundary(self):
        text = read(DOCUMENTS[0])
        for number in (1, 2, 3, 8, 9, 10, 11, 12, 13, 14):
            marker = '<a id="b{}"></a>'.format(number)
            section = text.split(marker, 1)[1].split("\n---", 1)[0]
            with self.subTest(section=number):
                self.assertIn("停写", section)
                self.assertIn("(#b15)", section)
        self.assertNotIn("恢复步骤（照抄）", text)
        self.assertNotIn("每次新建分支后都要再 pack 一次", text)
        self.assertNotIn("绕过 git，用**文件编辑工具**直接写引用文件", text)
        b14 = text.split('<a id="b14"></a>', 1)[1].split("\n---", 1)[0]
        self.assertIn("正常 Git 会按需创建", b14)
        self.assertIn("支持嵌套分支", b14)

    def test_template_distinguishes_waiting_from_first_manual_claim(self):
        text = read("templates/session.md")
        header = text.split("## 当前进度", 1)[0]
        self.assertRegex(header, r"(?m)^- status: standby$")
        self.assertRegex(header, r"(?m)^- files: \[\]$")
        self.assertIn("工具缺失时", text)
        self.assertIn("`status` 改为 `active`", text)
        self.assertIn("`files` 改为完整非空 JSON 范围", text)
        self.assertIn("门禁拒绝时不能退回手工登记绕过", text)
        self.assertIn("未完成保留 blocked 与占用", text)

    def test_status_reference_lists_actual_supported_words(self):
        text = read("docs/README.md")
        documented = re.search(r"支持的状态词为 `([^`]+)`", text)
        self.assertIsNotNone(documented)
        words = {word.strip() for word in documented.group(1).split("/")}
        self.assertEqual(words, board.SESSION_STATUS_WORDS)
        self.assertIn("未知、缺失、拼接或损坏状态", text)
        self.assertIn("告警并保留占用", text)

    def test_records_are_not_proof_of_authorization_or_actual_test_completion(self):
        for relative in DOCUMENTS[1:]:
            text = read(relative)
            with self.subTest(document=relative):
                self.assertIn("不证明", text)
                self.assertIn("测试通过或用户授权", text)
                self.assertIn("子代理", text)
                self.assertIn("结果", text)

    def test_local_links_and_explicit_incident_anchors_resolve(self):
        count = 0
        for relative in DOCUMENTS:
            for destination, target, anchor in local_links(relative):
                count += 1
                with self.subTest(document=relative, link=destination):
                    self.assertTrue(target.is_file(), str(target))
                    if anchor and target.is_file():
                        self.assertIn(anchor, markdown_anchors(target.read_text(encoding="utf-8")))
        self.assertGreater(count, 0)
        text = read(DOCUMENTS[0])
        for number in range(1, 17):
            self.assertEqual(text.count('<a id="b{}"></a>'.format(number)), 1)


if __name__ == "__main__":
    unittest.main()
