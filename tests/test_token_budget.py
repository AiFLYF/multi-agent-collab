"""Keep startup loading bounded and optional token measurement offline."""

import importlib.util
import sys
import types
import unittest
from unittest import mock

from selftest import KIT

spec = importlib.util.spec_from_file_location("measure_context", KIT / "scripts" / "measure_context.py")
measurement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measurement)


class TestTokenBudget(unittest.TestCase):
    def test_required_startup_stays_within_character_budget_without_dependencies(self):
        with mock.patch.object(measurement, "load_encoder", return_value=(None, "not installed")):
            result = measurement.report()
        self.assertTrue(result["budget_ok"], result["installed_startup"])
        self.assertIsNone(result["installed_startup"]["tokens"])
        self.assertLessEqual(result["installed_startup"]["chars"], 6500)

    def test_missing_tokenizer_cache_never_triggers_a_download(self):
        package = types.ModuleType("tiktoken")
        loader = types.ModuleType("tiktoken.load")
        original = mock.Mock(side_effect=AssertionError("a network request must not happen"))
        loader.read_file = original
        package.load = loader
        package.get_encoding = lambda name: loader.read_file("https://example.invalid/tokenizer")
        with mock.patch.dict(sys.modules, {"tiktoken": package, "tiktoken.load": loader}):
            encoder, status = measurement.load_encoder("o200k_base")
        self.assertIsNone(encoder)
        self.assertIn("downloads are disabled", status)
        original.assert_not_called()
        self.assertIs(loader.read_file, original)


if __name__ == "__main__":
    unittest.main()
