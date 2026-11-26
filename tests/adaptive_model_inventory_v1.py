"""Isolated inventory contract tests; no real Ollama or database required."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import adaptive_model_inventory_v1 as inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.provider = {"enabled": True, "model": "qwen:latest", "base_url": "http://127.0.0.1:11434", "secret": "private"}

    def test_reported_is_not_verified(self):
        result = inventory.inventory_snapshot(self.provider, {"reachable": True, "models": ["qwen:latest", "vendor/model:cloud", "qwen:latest"]})
        self.assertEqual(result["discovery_state"], "reported")
        self.assertTrue(result["configured_model_reported"])
        self.assertEqual(len(result["models"]), 2)
        self.assertTrue(all(not m["routing_eligible"] and not m["execution_verified"] and m["locality"] == "unverified" for m in result["models"]))
        self.assertNotIn("private", str(result))
        self.assertNotIn("127.0.0.1", str(result))

    def test_invalid_names_and_bounds(self):
        names = [None, {}, "https://example.com/model", "../bad", "a/../b", "a//b", "bad\nname", "a" * 161, "ok:tag"] + [f"model{i}" for i in range(300)]
        result = inventory.inventory_snapshot(self.provider, {"reachable": True, "models": names})
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["models"]), inventory.MAX_MODELS)
        self.assertEqual(result["models"][0]["model"], "ok:tag")
        self.assertFalse(result["configured_model_reported"])

    def test_unreachable_disabled_and_malformed(self):
        self.assertEqual(inventory.inventory_snapshot(self.provider)["discovery_state"], "unreachable")
        self.assertEqual(inventory.inventory_snapshot({**self.provider, "enabled": False}, {"reachable": True, "models": ["qwen:latest"]})["models"], [])
        self.assertEqual(inventory.inventory_snapshot(self.provider, {"reachable": True, "models": "bad"})["discovery_state"], "invalid_response")
        self.assertFalse(inventory.inventory_snapshot(self.provider, {"reachable": True, "models": []})["configured_model_reported"])

    def test_collect_uses_existing_discovery(self):
        fake = types.ModuleType("app.services.providers")
        fake.ProviderError = type("ProviderError", (RuntimeError,), {})
        fake.get_ollama = Mock(return_value=self.provider)
        fake.discover_ollama_models = Mock(return_value={"reachable": True, "models": ["qwen:latest"]})
        import app.services
        with patch.dict(sys.modules, {"app.services.providers": fake}), patch.object(app.services, "providers", fake, create=True):
            self.assertTrue(inventory.collect_inventory()["configured_model_reported"])
            fake.discover_ollama_models.assert_called_once_with(self.provider["base_url"])
            fake.discover_ollama_models.side_effect = fake.ProviderError("private error")
            self.assertEqual(inventory.collect_inventory()["discovery_state"], "unreachable")
            fake.discover_ollama_models.reset_mock()
            fake.get_ollama.return_value = {**self.provider, "enabled": False}
            self.assertEqual(inventory.collect_inventory()["discovery_state"], "disabled")
            fake.discover_ollama_models.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
