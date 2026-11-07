"""Проверки без сети: python3 -m unittest discover -s tests"""

import io
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "plugins", "fusion-integrator", "skills",
                       "fusion-integrator", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))
import fusion_lib as lib  # noqa: E402
import fusion_ops as ops  # noqa: E402
import fusion_kb_chat as kb  # noqa: E402


class SessionFileTests(unittest.TestCase):
    def test_session_has_private_mode_and_no_password(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(lib, "CONFIG_DIR", os.path.join(home, "cfg")):
                lib.save_session("salon", {"access_token": "a.b.c", "refresh_token": "r", "password": "SECRET-PASS"})
                path = lib.session_path("salon")
                mode = stat.S_IMODE(os.stat(path).st_mode)
                self.assertEqual(mode, 0o600)
                self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
                self.assertNotIn("SECRET-PASS", open(path).read())

    def test_client_name_validation(self):
        for bad in ("", "../x", "A B", "x" * 41, "Salon"):
            with self.assertRaises(lib.FusionError):
                lib.validate_client(bad)
        self.assertEqual(lib.validate_client("salon-nur"), "salon-nur")

    def test_loose_permissions_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(lib, "CONFIG_DIR", home):
                lib.save_session("salon", {"access_token": "a.b.c"})
                os.chmod(lib.session_path("salon"), 0o644)
                with self.assertRaises(lib.FusionError):
                    lib.load_session("salon")


class HttpSafetyTests(unittest.TestCase):
    def test_error_text_hides_token(self):
        token = "tok-1234567890-secret"
        error = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(f"echo {token}".encode()))
        with mock.patch.object(lib._OPENER, "open", side_effect=error):
            with self.assertRaises(lib.FusionError) as caught:
                lib.raw_request("GET", "/x", token=token)
        self.assertNotIn(token, str(caught.exception))

    def test_login_error_hides_password(self):
        error = urllib.error.HTTPError("u", 500, "bad", {}, io.BytesIO(b"password=Pa55word-long"))
        with mock.patch.object(lib._OPENER, "open", side_effect=error):
            with self.assertRaises(lib.FusionError) as caught:
                lib.login_request("a@b.c", "Pa55word-long")
        self.assertNotIn("Pa55word-long", str(caught.exception))

    def test_redirects_are_not_followed(self):
        handler = lib._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_api_base_is_fixed_https(self):
        self.assertTrue(lib.API_BASE.startswith("https://fusion-ai.app/"))


class HardeningTests(unittest.TestCase):
    def test_client_name_newline_rejected(self):
        with self.assertRaises(lib.FusionError):
            lib.validate_client("abc\n")

    def test_ids_with_path_tricks_rejected(self):
        for bad in ("../agents/x", "a/b", "a?b", "a b", "", "x" * 65, "a\n"):
            with self.assertRaises(lib.FusionError):
                lib.validate_id(bad)
        self.assertEqual(lib.validate_id("64c7fd3d-b0df"), "64c7fd3d-b0df")

    def test_request_path_guard(self):
        for bad in ("/a/../b", "//evil", "/a#b", "relative"):
            with self.assertRaises(lib.FusionError):
                lib.raw_request("GET", bad)

    def test_backup_label_and_agent_checked(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(lib, "CONFIG_DIR", home), mock.patch.object(lib, "BACKUP_DIR", os.path.join(home, "backups")):
                with self.assertRaises(lib.FusionError):
                    lib.backup_path("salon", "AG1", "x/../../../victim")
                with self.assertRaises(lib.FusionError):
                    lib.backup_path("salon", "../..", "ok")
                self.assertTrue(lib.backup_path("salon", "AG1", "before-prompt").startswith(home))

    def test_long_error_body_does_not_leak_token_fragment(self):
        token = "eyJhbGciOiJIUzI1NiJ9.payloadpayloadpayload.signaturesignature"
        body = ("x" * 280 + token).encode()
        error = urllib.error.HTTPError("u", 422, "bad", {}, io.BytesIO(body))
        with mock.patch.object(lib._OPENER, "open", side_effect=error):
            with self.assertRaises(lib.FusionError) as caught:
                lib.raw_request("POST", "/agents", token=token)
        self.assertNotIn("payloadpayload", str(caught.exception))

    def test_auth_errors_have_no_body(self):
        error = urllib.error.HTTPError("u", 422, "bad", {}, io.BytesIO(b"refresh_token=SECRETVALUE"))
        with mock.patch.object(lib._OPENER, "open", side_effect=error):
            with self.assertRaises(lib.FusionError) as caught:
                lib.raw_request("POST", "/auth/refresh")
        self.assertNotIn("SECRETVALUE", str(caught.exception))

    def test_user_paths_confined_to_project_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            real = os.path.realpath(folder)
            old = os.getcwd()
            os.chdir(real)
            try:
                open("ok.md", "w").write("x")
                os.symlink("/etc/hosts", "link.md")
                self.assertTrue(lib.safe_input_file("ok.md").endswith("ok.md"))
                for bad in ("/etc/hosts", "link.md", "../x"):
                    with self.assertRaises(lib.FusionError):
                        lib.safe_input_file(bad)
                with self.assertRaises(lib.FusionError):
                    lib.safe_output_file("ok.md")
                with self.assertRaises(lib.FusionError):
                    lib.safe_output_file(".hidden")
            finally:
                os.chdir(old)

    def test_secret_in_prompt_blocked(self):
        errors, _ = lib.lint_prompt("ключ eyJabcdefghijklmnop.aaaa.bbbb")
        self.assertTrue(errors)

    def test_write_private_file_mode(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(lib, "CONFIG_DIR", home):
                path = os.path.join(home, "sub", "f.json")
                lib.write_private_file(path, "{}")
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class PromptLintTests(unittest.TestCase):
    def test_clean_prompt(self):
        errors, _ = lib.lint_prompt("<роль>Ты помощник.</роль>")
        self.assertEqual(errors, [])

    def test_blocks_markers_and_bad_names(self):
        banned_name = "Айг" + "ерим"
        text = f"Ты {banned_name}. {{{{ПЛЕЙСХОЛДЕР}}}} TODO operator_call"
        errors, _ = lib.lint_prompt(text)
        joined = " ".join(errors)
        for needle in ("{{", "TODO", "operator_call", "запрещённое Fusion"):
            self.assertIn(needle, joined)

    def test_header_is_stripped(self):
        text = "# version: v1.0 | date: 2026-11-07\n\n<роль>x</роль>"
        self.assertEqual(lib.strip_prompt_header(text), "<роль>x</роль>")


class OpsTests(unittest.TestCase):
    def test_phone_is_required(self):
        with self.assertRaises(lib.FusionError):
            ops.build_lead_fields("a1", [{"label": "Имя клиента", "required": True}])
        with self.assertRaises(lib.FusionError):
            ops.build_lead_fields("a1", [{"label": "Телефон", "required": False}])
        template = ops.build_lead_fields("a1", ops.DEFAULT_LEAD_FIELDS)
        self.assertTrue(next(f for f in template if f["label"] == "Телефон")["is_required"])

    def test_field_ids_stable(self):
        first = ops.build_lead_fields("a1", ops.DEFAULT_LEAD_FIELDS)
        second = ops.build_lead_fields("a1", ops.DEFAULT_LEAD_FIELDS)
        self.assertEqual([f["id"] for f in first], [f["id"] for f in second])

    def test_followup_limits(self):
        with self.assertRaises(lib.FusionError):
            ops.validate_followups({"steps": [{"delay_minutes": 2000, "prompt": "молчи"}]})
        with self.assertRaises(lib.FusionError):
            ops.validate_followups({"steps": [{"delay_minutes": 5, "prompt": "x"}] * 6})
        with self.assertRaises(lib.FusionError):
            ops.validate_followups({"extra": 1})

    def test_chat_limit_enforced(self):
        dialogs = {"a": ["1", "2", "3"], "b": ["4", "5"]}
        self.assertEqual(kb.count_questions(dialogs), 5)
        with self.assertRaises(lib.FusionError):
            kb.run_chat(None, "x", dialogs, 4)

    def test_kb_service_header_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write("# version: 1\nпарный файл — промпт\nЦены")
        try:
            with self.assertRaises(lib.FusionError):
                kb.check_kb_file(handle.name)
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()
