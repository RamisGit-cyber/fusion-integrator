"""Проверки файлов для отправки без сети: python3 -m unittest discover -s tests"""

import os
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "plugins", "fusion-integrator", "skills",
                       "fusion-integrator", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))
import fusion_assets as assets  # noqa: E402
import fusion_lib as lib  # noqa: E402


class FakeApi:
    client = "salon"

    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        return [{"id": "a1", "title": "t"}]

    def post(self, path, payload=None):
        self.calls.append(("POST", path, payload))
        return {"id": "s1", "items": []}

    def patch(self, path, payload):
        self.calls.append(("PATCH", path, payload))
        return {"id": "a1"}


class AssetValidationTests(unittest.TestCase):
    def test_set_limits(self):
        with self.assertRaises(lib.FusionError):
            assets._asset_ids(",".join(f"a{i}" for i in range(11)))
        with self.assertRaises(lib.FusionError):
            assets._asset_ids("a1,a1")
        with self.assertRaises(lib.FusionError):
            assets._asset_ids("a1,../x")
        self.assertEqual(assets._asset_ids("a1, a2"), ["a1", "a2"])

    def test_text_rejects_token_and_long(self):
        with self.assertRaises(lib.FusionError):
            assets._text("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def", "--when", 600)
        with self.assertRaises(lib.FusionError):
            assets._text("x" * 601, "--when", 600)
        with self.assertRaises(lib.FusionError):
            assets._text("  ", "--title", 120, required=True)

    def test_file_must_be_inside_project_and_allowed_type(self):
        with tempfile.TemporaryDirectory() as folder:
            real = os.path.realpath(folder)
            for name, data in (("ok.png", b"x"), ("bad.exe", b"x"), ("empty.png", b"")):
                with open(os.path.join(real, name), "wb") as handle:
                    handle.write(data)
            cwd = os.getcwd()
            os.chdir(real)
            try:
                self.assertTrue(assets.check_asset_file("ok.png").endswith("ok.png"))
                for bad in ("bad.exe", "empty.png", "../outside.png"):
                    with self.assertRaises(lib.FusionError):
                        assets.check_asset_file(bad)
            finally:
                os.chdir(cwd)

    def test_content_must_match_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            real = os.path.realpath(folder)
            with open(os.path.join(real, "fake.png"), "wb") as handle:
                handle.write(b"not an image")
            with open(os.path.join(real, "ok.png"), "wb") as handle:
                handle.write(b"\x89PNG\r\n")
            cwd = os.getcwd()
            os.chdir(real)
            try:
                with self.assertRaises(lib.FusionError):
                    assets.read_asset("fake.png")
                self.assertEqual(assets.read_asset("ok.png")[1], b"\x89PNG\r\n")
            finally:
                os.chdir(cwd)

    def test_control_chars_rejected(self):
        with self.assertRaises(lib.FusionError):
            assets._text("a\x00b", "--title", 120)

    def test_update_requires_changes_and_saves_backup(self):
        api = FakeApi()
        with self.assertRaises(lib.FusionError):
            assets.update_asset(api, "agent1", "a1")
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(lib, "CONFIG_DIR", home), mock.patch.object(lib, "BACKUP_DIR", os.path.join(home, "b")):
                assets.update_asset(api, "agent1", "a1", when="Когда просят каталог")
                self.assertTrue(lib.list_backups("salon", "agent1"))
        self.assertEqual(api.calls[-1], ("PATCH", "/agents/agent1/assets/a1", {"description_for_ai": "Когда просят каталог"}))

    def test_set_create_sends_ids_and_warns_disabled(self):
        api = FakeApi()
        assets.create_set(api, "agent1", "Примеры", "Когда просят примеры", "a1,a2")
        self.assertEqual(api.calls[-1][2]["asset_ids"], ["a1", "a2"])
        self.assertEqual(api.calls[-1][1], "/agents/agent1/asset-collections")

    def test_upload_form_cleans_filename(self):
        sent = {}

        def fake(method, path, **kwargs):
            sent.update(kwargs)
            return 200, b"{}"

        class A:
            @staticmethod
            def _fresh_token():
                return "t"

        with mock.patch.object(lib, "raw_request", fake):
            lib.upload_form(A, "/x", {"title": "t"}, "file", 'a"\r\nb.png', b"data", "image/png")
        self.assertNotIn(b'filename="a"', sent["body"])
        self.assertIn(b"a___b.png", sent["body"])


if __name__ == "__main__":
    unittest.main()
