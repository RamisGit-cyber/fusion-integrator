"""Проверки скрытой веб-формы входа без сети: настоящий сервер на 127.0.0.1, ответ Fusion подменён."""

import http.client
import io
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "plugins", "fusion-integrator", "skills",
                       "fusion-integrator", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))
import fusion_lib as lib  # noqa: E402
import fusion_loginform as form  # noqa: E402

TEST_PASSWORD = "Pa55-form-test-value"


class LoginFormTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(lib, "CONFIG_DIR", os.path.join(self.home.name, "cfg"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.home.cleanup)
        self.server = form.create_server("salon")
        self.outcome = {}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _run(self):
        try:
            self.outcome["email"] = form.serve_until_login(self.server, timeout=20)
        except lib.FusionError as error:
            self.outcome["error"] = str(error)

    def _stop(self):
        self.server.locked_out = True
        self.thread.join(5)

    def request(self, method, path, body=None, headers=None):
        base = {"Host": f"127.0.0.1:{self.server.port}"}
        base.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        conn.request(method, path, body=body, headers=base)
        response = conn.getresponse()
        data = response.read().decode()
        conn.close()
        return response.status, data

    def post_login(self, token=None, origin="same", extra=None):
        token = token or self.server.secret
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin == "same":
            headers["Origin"] = f"http://127.0.0.1:{self.server.port}"
        elif origin:
            headers["Origin"] = origin
        headers.update(extra or {})
        body = f"email=Owner%40Example.com&password={TEST_PASSWORD}"
        return self.request("POST", f"/{token}/login", body, headers)

    def test_link_part_is_long_and_binds_loopback(self):
        self.assertGreaterEqual(len(self.server.secret), 22)  # 22 символа base64 = 132 бита и больше
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_wrong_link_part_is_403(self):
        self.assertEqual(self.request("GET", "/wrong/")[0], 403)
        with mock.patch.object(lib, "login_request") as login:
            self.assertEqual(self.post_login(token="wrong")[0], 403)
            login.assert_not_called()

    def test_foreign_or_missing_origin_is_403(self):
        with mock.patch.object(lib, "login_request") as login:
            self.assertEqual(self.post_login(origin="https://evil.example")[0], 403)
            self.assertEqual(self.post_login(origin=None)[0], 403)
            self.assertEqual(self.post_login(extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
            login.assert_not_called()

    def test_foreign_host_is_403(self):
        status, _ = self.request("GET", self.server.base_path, headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_page_has_hidden_password_field_and_note(self):
        status, html = self.request("GET", self.server.base_path)
        self.assertEqual(status, 200)
        self.assertIn('type="password"', html)
        self.assertIn("Пароль уходит только в Fusion и не сохраняется", html)
        self.assertNotIn(self.server.secret, html)

    def test_wrong_password_keeps_form_open(self):
        with mock.patch.object(lib, "login_request", side_effect=lib.LoginRejected("нет")):
            status, data = self.post_login()
        self.assertEqual(status, 401)
        self.assertIn("Неверный логин или пароль", data)
        self.assertNotIn(TEST_PASSWORD, data)
        self.assertEqual(self.request("GET", self.server.base_path)[0], 200)
        self.assertFalse(os.path.exists(lib.session_path("salon")))

    def test_success_saves_private_token_without_password_then_stops(self):
        tokens = {"access_token": "a.b.c", "refresh_token": "r"}
        with mock.patch.object(lib, "login_request", return_value=tokens) as login:
            status, data = self.post_login()
        self.assertEqual(status, 200)
        self.assertEqual(login.call_args[0][1], TEST_PASSWORD)
        path = lib.session_path("salon")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, encoding="utf-8") as handle:
            self.assertNotIn(TEST_PASSWORD, handle.read())
        self.assertNotIn(TEST_PASSWORD, data)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.outcome.get("email"), "Owner@Example.com")
        with self.assertRaises(OSError):
            self.request("GET", self.server.base_path)

    def test_no_request_logging(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.request("GET", self.server.base_path)
        self.assertNotIn(self.server.secret, err.getvalue())

    def test_silent_connection_does_not_hang_server(self):
        import socket
        silent = socket.create_connection(("127.0.0.1", self.server.port), timeout=2)
        self.addCleanup(silent.close)
        # пока молчащее соединение открыто, форма должна отвечать после таймаута сокета
        status, _ = self.request("GET", self.server.base_path)
        self.assertEqual(status, 200)

    def test_ten_failures_lock_the_form(self):
        with mock.patch.object(lib, "login_request", side_effect=lib.LoginRejected("нет")):
            for _ in range(form.MAX_FAILED_ATTEMPTS):
                self.post_login()
        self.thread.join(5)
        self.assertIn("error", self.outcome)


class LoginFormTimeoutTests(unittest.TestCase):
    def test_timeout_stops_server(self):
        quick = form.create_server("salon2")
        port = quick.port
        with self.assertRaises(lib.FusionError):
            form.serve_until_login(quick, timeout=0.2)
        with self.assertRaises(OSError):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/")


if __name__ == "__main__":
    unittest.main()
