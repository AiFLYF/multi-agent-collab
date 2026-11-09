"""Publishing must follow successful tests of the same main commit."""
import ast
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def job_block(source, name):
    """Read a top-level job; the CI linter validates the complete YAML."""
    match = re.search(r"^  " + re.escape(name) + r":\n(.*?)(?=^  [\w-]+:|\Z)",
                      source, flags=re.M | re.S)
    if not match:
        raise ValueError("missing job: " + name)
    return match.group(1)


def job_field(block, name):
    match = re.search(r"^    " + re.escape(name) + r":\s*([^\n]+)$", block, flags=re.M)
    if not match:
        raise ValueError("missing job field: " + name)
    return match.group(1).strip()


def evaluate_guard(expression, ref, result):
    """Evaluate the simple GitHub boolean guards without executing YAML text."""
    if not (expression.startswith("${{") and expression.endswith("}}")):
        raise ValueError("guard must be an explicit expression")
    expression = expression[3:-2].strip()
    expression = expression.replace("github.ref", "ref").replace("needs.verify.result", "result")
    expression = expression.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id in ("ref", "result"):
            return {"ref": ref, "result": result}[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [bool(visit(value)) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return visit(node.left) == visit(node.comparators[0])
        raise ValueError("unsupported publishing guard: " + ast.dump(node))

    return bool(visit(tree))


class PublishingWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        cls.tests = (ROOT / ".github/workflows/selftest.yml").read_text(encoding="utf-8")
        cls.verify = job_block(cls.pages, "verify")
        cls.deploy = job_block(cls.pages, "deploy")

    def test_failed_cancelled_skipped_or_non_main_run_cannot_publish(self):
        guard = job_field(self.deploy, "if")
        for ref in ("refs/heads/main", "refs/heads/develop", "refs/heads/feature/example", "refs/tags/v1.8.0"):
            for result in ("success", "failure", "cancelled", "skipped"):
                with self.subTest(ref=ref, result=result):
                    self.assertEqual(evaluate_guard(guard, ref, result),
                                     ref == "refs/heads/main" and result == "success")
        self.assertEqual(job_field(self.deploy, "needs"), "verify")

    def test_manual_feature_runs_cannot_start_the_publishing_verifier(self):
        guard = job_field(self.verify, "if")
        for ref in ("refs/heads/main", "refs/heads/develop", "refs/heads/feature/example"):
            with self.subTest(ref=ref):
                self.assertEqual(evaluate_guard(guard, ref, "success"), ref == "refs/heads/main")

    def test_verifier_reuses_complete_test_workflow_without_a_ref_override(self):
        self.assertEqual(job_field(self.verify, "uses"), "./.github/workflows/selftest.yml")
        self.assertNotRegex(self.verify, re.compile(r"^    (?:with|secrets):", re.M),
                            "caller must not override tested source")
        self.assertRegex(self.tests, r"(?m)^  workflow_call:\s*$")
        for stage in ("tools/agents_status.py --selfcheck", "tests/selftest.py",
                      'unittest discover -s tests -p "test_*.py"',
                      "scripts/sync_install_docs.py --check", "scripts/measure_context.py --check"):
            with self.subTest(stage=stage):
                self.assertIn(stage, self.tests)

    def test_test_and_publish_checkouts_use_the_event_commit_and_do_not_keep_credentials(self):
        for source in (self.tests, self.deploy):
            with self.subTest(source="selftest" if source == self.tests else "deploy"):
                checkout = re.search(r"(?m)^      - uses: actions/checkout@v4\n"
                                     r"        with:\n((?:          [^\n]+\n)+)", source)
                self.assertIsNotNone(checkout)
                self.assertIn("          ref: ${{ github.sha }}\n", checkout.group(1))
                self.assertIn("          persist-credentials: false\n", checkout.group(1))

    def test_only_deployment_job_receives_publishing_permissions(self):
        global_permissions = self.pages.split("jobs:", 1)[0]
        self.assertNotIn("pages: write", global_permissions)
        self.assertNotIn("id-token: write", global_permissions)
        self.assertNotIn("pages: write", self.verify)
        self.assertNotIn("id-token: write", self.verify)
        self.assertIn("pages: write", self.deploy)
        self.assertIn("id-token: write", self.deploy)

    def test_ci_python_stdio_handles_unicode_on_an_ansi_host(self):
        match = re.search(r"(?m)^env:\n((?:  [^\n]+\n)+)", self.tests)
        self.assertIsNotNone(match)
        setting = re.search(r"(?m)^  PYTHONIOENCODING:[ \t]+([^\n]+)$", match.group(1))
        self.assertIsNotNone(setting)
        encoding = setting.group(1).strip().strip("\"'")
        env = dict(os.environ, PYTHONUTF8="0", PYTHONIOENCODING="cp1252")
        env["PYTHONIOENCODING"] = encoding
        command = "import sys; sys.stdout.write('\\u534f\\u4f5c\\u6d4b\\u8bd5\\n')"
        result = subprocess.run([sys.executable, "-c", command], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode("utf-8", errors="strict").splitlines(), ["协作测试"])
        self.assertNotIn(b"Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
