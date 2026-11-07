"""Вход в кабинет клиента через скрытую веб-форму на компьютере партнёра.

Поднимает маленький сервер ТОЛЬКО на 127.0.0.1 (случайный порт, одноразовый секрет в адресе),
открывает форму в браузере и ждёт вход. Пароль идёт из браузера прямо в Fusion, в чат не попадает.
Сохраняется только токен сессии (права 600), пароль забывается. Сервер сам выключается после входа
или через 10 минут. Запросы в журнал не пишутся: в адресе лежит секрет формы.
"""

import hmac
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import fusion_lib as lib

TIMEOUT_SECONDS = 600
MAX_BODY = 8192
MAX_FAILED_ATTEMPTS = 10

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход в кабинет клиента</title>
<style>
:root{--brand:#6739FF;--brand-dark:#5128d6;--text:#1c1730;--muted:#6b6683;--line:#dcd7ee;--bg:#f6f4ff;--err:#c0262d;--ok:#1a7f4b}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;padding:16px}
main{width:100%;max-width:400px;background:#fff;border-radius:16px;padding:32px 28px;box-shadow:0 8px 32px rgba(103,57,255,.12)}
.brand{color:var(--brand);font-weight:700;font-size:15px;letter-spacing:.02em;margin:0 0 8px}
h1{font-size:22px;margin:0 0 20px;line-height:1.3}
label{display:block;font-size:14px;font-weight:600;margin:14px 0 6px}
input{width:100%;font:inherit;font-size:16px;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--text)}
input:focus{outline:2px solid var(--brand);outline-offset:1px;border-color:var(--brand)}
button{width:100%;margin-top:22px;font:inherit;font-weight:600;font-size:16px;padding:13px;border:0;border-radius:10px;background:var(--brand);color:#fff;cursor:pointer}
button:hover{background:var(--brand-dark)}
button:disabled{opacity:.6;cursor:default}
.note{font-size:13px;color:var(--muted);margin:18px 0 0;line-height:1.5}
#msg{margin-top:16px;font-size:14px;line-height:1.5;min-height:1.5em}
#msg.err{color:var(--err)}
#msg.ok{color:var(--ok);font-weight:600}
</style></head><body><main>
<p class="brand">Fusion AI</p>
<h1>Вход в кабинет клиента</h1>
<form id="f" method="post" action="login" autocomplete="off">
<label for="email">Почта</label>
<input id="email" name="email" type="email" required autocomplete="username" autofocus>
<label for="password">Пароль</label>
<input id="password" name="password" type="password" required autocomplete="off">
<button id="go" type="submit">Войти</button>
</form>
<div id="msg" role="status" aria-live="polite"></div>
<p class="note">Пароль уходит только в Fusion и не сохраняется. В чат с Claude его писать не нужно. Браузеру сохранить пароль не разрешайте.</p>
<noscript><p class="note">Для входа нужен включённый JavaScript.</p></noscript>
<script nonce="__NONCE__">
var form=document.getElementById("f"),msg=document.getElementById("msg"),go=document.getElementById("go");
function show(text,kind){msg.textContent=text;msg.className=kind}
form.addEventListener("submit",function(ev){
ev.preventDefault();go.disabled=true;show("Входим…","");
var body=new URLSearchParams({email:form.email.value,password:form.password.value}).toString();
fetch("login",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:body,credentials:"omit",cache:"no-store"})
.then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})})
.then(function(res){
form.password.value="";
if(res.ok&&res.j.ok){form.style.display="none";show("Готово, вернитесь в Claude и напишите «готово».","ok")}
else{go.disabled=false;show(res.j.error||"Не получилось войти, попробуйте ещё раз.","err")}})
.catch(function(){form.password.value="";go.disabled=false;show("Форма уже закрыта или нет связи. Если вход не состоялся, попросите Claude открыть форму заново.","err")});
});
</script></main></body></html>
"""


class _FormServer(HTTPServer):
    """Сервер одной формы. Однопоточный: запросы идут по очереди, гонок нет."""

    allow_reuse_address = False

    def __init__(self, client, secret):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.client = client
        self.secret = secret
        self.port = self.server_address[1]
        self.finished_email = None
        self.failed_attempts = 0
        self.locked_out = False
        self.timeout = 1

    @property
    def base_path(self):
        return f"/{self.secret}/"

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}{self.base_path}"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "FusionLogin"
    sys_version = ""
    timeout = 3  # молчащее или медленное соединение не должно вешать однопоточный сервер

    def log_message(self, *_args):  # в адресе секрет формы: журнал запросов отключён
        return

    def log_error(self, *_args):
        return

    def _reply(self, status, body, content_type, extra=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._reply(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _forbid(self):
        self._reply(403, "Доступ закрыт", "text/plain; charset=utf-8")

    def _host_ok(self):
        return self.headers.get("Host", "") == f"127.0.0.1:{self.server.port}"

    def _origin_ok(self):
        # Браузер при POST всегда шлёт Origin. Нет Origin или чужой: отказ (защита от CSRF).
        if self.headers.get("Origin", "") != f"http://127.0.0.1:{self.server.port}":
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def _path_ok(self, suffix):
        expected = (self.server.base_path + suffix).encode()
        return hmac.compare_digest(self.path.encode("utf-8", "replace"), expected)

    def do_GET(self):
        if not (self._host_ok() and self._path_ok("")):
            return self._forbid()
        nonce = secrets.token_urlsafe(16)
        csp = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-%s'; connect-src 'self'; "
               "form-action 'none'; base-uri 'none'; frame-ancestors 'none'" % nonce)
        self._reply(200, PAGE.replace("__NONCE__", nonce), "text/html; charset=utf-8",
                    {"Content-Security-Policy": csp})

    def do_POST(self):
        if not (self._host_ok() and self._path_ok("login") and self._origin_ok()):
            return self._forbid()
        if self.server.locked_out:
            return self._json(429, {"ok": False, "error": "Слишком много попыток. Попросите Claude открыть форму заново."})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY or not self.headers.get("Content-Type", "").startswith(
                "application/x-www-form-urlencoded"):
            return self._json(400, {"ok": False, "error": "Не получилось прочитать форму, попробуйте ещё раз."})
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        email = (fields.get("email", [""])[0]).strip()
        password = fields.get("password", [""])[0]
        fields = None
        if not email or "@" not in email or len(email) > 254 or not password or len(password) > 1024:
            return self._json(400, {"ok": False, "error": "Введите почту и пароль."})
        self._attempt_login(email, password)

    def _attempt_login(self, email, password):
        server = self.server
        try:
            tokens = lib.login_request(email, password)
            if not isinstance(tokens, dict) or "access_token" not in tokens:
                return self._json(400, {"ok": False, "error": (
                    "Платформа не вернула токен. Возможно, кабинет создан через вход Google: у него нет пароля.")})
            lib.save_session(server.client, tokens)
        except lib.LoginRejected:
            server.failed_attempts += 1
            if server.failed_attempts >= MAX_FAILED_ATTEMPTS:
                server.locked_out = True
            return self._json(401, {"ok": False, "error": "Неверный логин или пароль, попробуйте ещё раз."})
        except (lib.FusionError, OSError, ValueError):
            return self._json(502, {"ok": False, "error": "Не получилось связаться с Fusion или сохранить сессию. Попробуйте ещё раз."})
        finally:
            password = None  # noqa: F841 - пароль дальше не нужен
        server.finished_email = email
        self._json(200, {"ok": True})


def _open_browser(url):
    """Открывает адрес в браузере по умолчанию. Возвращает True, если команда отработала."""
    try:
        if sys.platform == "darwin":
            command = ["open", url]
        elif os.name == "nt":
            os.startfile(url)  # noqa: S606 - адрес собран нами, это http://127.0.0.1
            return True
        else:
            command = ["xdg-open", url]
        # без ожидания: xdg-open на части систем не возвращается, пока открыт браузер
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return bool(webbrowser.open(url))
    except webbrowser.Error:
        return False


def serve_until_login(server, timeout=TIMEOUT_SECONDS):
    """Крутит сервер, пока человек не войдёт, не исчерпает попытки или не истечёт время."""
    deadline = time.monotonic() + timeout
    try:
        while server.finished_email is None and not server.locked_out:
            if time.monotonic() >= deadline:
                raise lib.FusionError("Время вышло (10 минут): форма закрыта, вход не состоялся. Попросите Claude открыть её заново.")
            server.handle_request()
    finally:
        server.server_close()
    if server.finished_email is None:
        raise lib.FusionError("Слишком много неверных попыток, форма закрыта. Попросите Claude открыть её заново.")
    return server.finished_email


def create_server(client):
    try:
        return _FormServer(client, secrets.token_urlsafe(24))  # 192 бита
    except OSError:
        raise lib.FusionError(
            "Не удалось запустить локальную форму входа (порт закрыт системой или песочницей). "
            "Запасной путь: fusion_login.py <имя> --terminal в окне Терминала."
        ) from None


def run_form_login(client):
    server = create_server(client)
    opened = _open_browser(server.url)
    lib.say("Открыла форму входа в браузере, введите почту и пароль клиента там." if opened else
            "Не смогла открыть браузер сама. Откройте форму входа вручную по ссылке ниже и введите почту и пароль клиента там.")
    lib.say(f"Ссылка на форму (живёт до входа, только на этом компьютере): {server.url}")
    sys.stdout.flush()
    email = serve_until_login(server)
    lib.say(f"Готово: вход выполнен как {lib.mask_email(email)}.")
    lib.say(f"Сохранён только токен сессии: {lib.session_path(client)} (права 600). Пароль нигде не записан.")
