"""Общая часть инструментов Fusion-интегратора: вход, запросы к API, бэкапы.

Только стандартная библиотека Python 3.9+. Правила безопасности:
- пароль здесь никогда не появляется (его знает только fusion_login.py);
- на диске лежит только токен сессии, файл с правами 600 в папке с правами 700;
- токен и пароль не печатаются ни в выводе, ни в тексте ошибок;
- адрес API зашит в код, из окружения его подменить нельзя;
- переходы по редиректам запрещены, чтобы заголовок с токеном не ушёл на чужой адрес.
"""

import base64
import hashlib
import tempfile
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_BASE = "https://fusion-ai.app/api"
# Cloudflare режет запросы без похожего на браузер User-Agent.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "fusion-integrator")
BACKUP_DIR = os.path.join(CONFIG_DIR, "backups")
CLIENT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
LABEL_RE = re.compile(r"[a-z0-9-]{1,30}")
JWT_RE = re.compile(r"eyJ[\w-]{10,}(\.[\w-]+){0,2}")
SECRET_PATTERNS = (JWT_RE, re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
                   re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
                   re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"))
def _daily_limit():
    """Суточный предел тест-сообщений на клиента. По умолчанию 500, меняется переменной FUSION_DAILY_CHAT_LIMIT."""
    try:
        return max(1, int(os.environ.get("FUSION_DAILY_CHAT_LIMIT", "500")))
    except ValueError:
        return 500


DAILY_CHAT_LIMIT = _daily_limit()
# Имена персон, которые Fusion запретила. Собрано из частей, чтобы слово не светилось в тексте проекта.
FORBIDDEN_NAMES = ("Айг" + "ерим",)
REQUEST_TIMEOUT = 30
UPLOAD_TIMEOUT = 120


class FusionError(Exception):
    """Понятная ошибка для человека. Текст безопасно печатать."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Прокси из окружения не используем: через них токен мог бы уйти третьей стороне.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


# ---------- клиент и файл сессии ----------

def validate_id(value, what="id"):
    """Идентификатор подставляется в адрес запроса, поэтому только буквы, цифры, дефис и подчёркивание."""
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise FusionError(f"Некорректный {what}: допустимы латинские буквы, цифры, дефис и подчёркивание.")
    return value


def contains_secret(text):
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def validate_client(name):
    if not name or not CLIENT_NAME_RE.fullmatch(name):
        raise FusionError(
            "Имя клиента: латиницей, цифры, дефис или подчёркивание, до 40 знаков "
            "(например salon-aigul). Оно нужно только для имени файла."
        )
    return name


def resolve_client(explicit=None):
    name = explicit or os.environ.get("FUSION_CLIENT", "")
    if not name:
        raise FusionError(
            "Не указан клиент. Добавь --client <имя> или задай FUSION_CLIENT. "
            "Вход в кабинет клиента делается командой: fusion-login <имя>."
        )
    return validate_client(name)


def session_path(client):
    return os.path.join(CONFIG_DIR, f"{validate_client(client)}.json")


def ensure_private_dir(path):
    """Создаёт папку с правами 700. Права меняем только внутри папки плагина, чужие папки не трогаем."""
    path = os.path.abspath(path)
    inside = path == CONFIG_DIR or path.startswith(CONFIG_DIR + os.sep)
    os.makedirs(CONFIG_DIR if inside else path, mode=0o700, exist_ok=True)
    if inside:
        for folder in (CONFIG_DIR, path):
            os.makedirs(folder, mode=0o700, exist_ok=True)
            if os.name != "nt":
                os.chmod(folder, 0o700)


def write_private_file(path, text):
    """Пишет файл с правами 600: уникальный временный файл рядом, fsync, атомарная замена."""
    path = os.path.abspath(path)
    ensure_private_dir(os.path.dirname(path))
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".fusion-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _is_inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def _check_user_path(path):
    real = os.path.realpath(path)
    if os.path.realpath(os.getcwd()) in (os.path.realpath(os.path.expanduser("~")), os.path.abspath(os.sep)):
        raise FusionError("Запусти Claude Code из папки проекта клиента, а не из домашней папки: файлы читаются только внутри текущей папки.")
    if not _is_inside(real, os.getcwd()):
        raise FusionError("Файлы можно читать и писать только внутри текущей папки проекта: " + path)
    if _is_inside(real, CONFIG_DIR):
        raise FusionError("Служебная папка сессий недоступна для этой команды.")
    relative = os.path.relpath(real, os.path.realpath(os.getcwd()))
    if any(part.startswith(".") and part not in (".", "..") for part in relative.split(os.sep)):
        raise FusionError("Скрытые файлы и папки (начинаются с точки) недоступны для этой команды.")
    return real


def safe_input_file(path):
    real = _check_user_path(path)
    if not os.path.isfile(real):
        raise FusionError(f"Файл не найден: {path}")
    return real


def safe_output_file(path):
    real = _check_user_path(path)
    if os.path.exists(real):
        raise FusionError(f"Файл уже существует, не перезаписываю: {path}. Выбери другое имя.")
    return real


def save_session(client, tokens):
    """Сохраняет ТОЛЬКО токены сессии. Пароль сюда не передаётся."""
    record = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "saved_at": int(time.time()),
    }
    write_private_file(session_path(client), json.dumps(record))


def load_session(client):
    path = session_path(client)
    if not os.path.exists(path):
        raise FusionError(
            f"Для клиента «{client}» нет входа. Войди: python3 fusion_login.py {client} "
            f"(откроется форма входа в браузере; запасной путь: --terminal в Терминале)"
        )
    if os.name != "nt" and (os.stat(path).st_mode & 0o077 or os.stat(path).st_uid != os.getuid()):
        raise FusionError(f"У файла сессии слишком открытые права: {path}. Выполни chmod 600 на него.")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _claims(jwt_value):
    """Срок годности из середины JWT. Подпись проверяет сервер, не мы."""
    try:
        payload = jwt_value.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def seconds_left(jwt_value):
    return int(_claims(jwt_value).get("exp", 0) - time.time()) if jwt_value else 0


def mask_email(email):
    _, _, domain = email.partition("@")
    return ("***@" + domain) if domain else "***"


# ---------- HTTP ----------

def _scrub(text, secrets):
    for secret in secrets:
        if secret and len(secret) >= 4:
            for variant in (secret, urllib.parse.quote(secret), urllib.parse.quote_plus(secret)):
                text = text.replace(variant, "***")
    return JWT_RE.sub("***", text)


def raw_request(method, path, *, token=None, body=None, content_type=None, timeout=REQUEST_TIMEOUT, secrets=()):
    """Один запрос. Возвращает (код, байты). Ошибки сервера превращает в FusionError без секретов."""
    if (not path.startswith("/") or "//" in path or ".." in path or "#" in path
            or any(ord(c) < 32 for c in path)):
        raise FusionError("Недопустимый путь запроса.")
    headers = {"User-Agent": BROWSER_UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(API_BASE + path, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        if path.startswith("/auth/"):
            raise FusionError(f"Платформа ответила {error.code} на {method} {path}.") from None
        raw = error.read(4000).decode(errors="replace")
        detail = _scrub(raw, [token, *secrets])[:300]
        raise FusionError(f"Платформа ответила {error.code} на {method} {path}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise FusionError(f"Не достучались до fusion-ai.app ({method} {path}): {reason}") from None


class LoginRejected(FusionError):
    """Платформа ответила, что почта или пароль не подходят."""


def login_request(username, password):
    """Вход по почте и паролю. Вызывается только из fusion_login.py."""
    form = urllib.parse.urlencode({"username": username.lower(), "password": password}).encode()
    try:
        _, data = raw_request("POST", "/auth/login", body=form,
                              content_type="application/x-www-form-urlencoded", secrets=[password])
    except FusionError as error:
        if " 400 " in str(error) or " 401 " in str(error) or " 422 " in str(error):
            raise LoginRejected("Платформа не приняла почту или пароль. Проверь раскладку и язык ввода.") from None
        raise FusionError(_scrub(str(error), [password])) from None
    return json.loads(data)


class Api:
    """Запросы от имени клиента. Сам обновляет токен, если срок на исходе."""

    def __init__(self, client):
        self.client = validate_client(client)
        self.session = load_session(self.client)

    def _fresh_token(self):
        token = self.session.get("access_token", "")
        if seconds_left(token) > 120:
            return token
        refresh = self.session.get("refresh_token", "")
        if refresh and seconds_left(refresh) > 0:
            _, data = raw_request("POST", "/auth/refresh", body=json.dumps({"refresh_token": refresh}).encode(),
                                  content_type="application/json", secrets=[refresh])
            fresh = json.loads(data)
            fresh.setdefault("refresh_token", refresh)  # сервер мог не прислать новый
            self.session = fresh
            save_session(self.client, self.session)
            return self.session["access_token"]
        if seconds_left(token) > 0:
            return token
        raise FusionError(
            f"Сессия клиента «{self.client}» истекла. Попроси человека войти заново: "
            f"запусти fusion_login.py {self.client} (откроется форма в браузере)"
        )

    def call(self, method, path, payload=None, timeout=REQUEST_TIMEOUT):
        body = json.dumps(payload).encode() if payload is not None else None
        _, data = raw_request(method, path, token=self._fresh_token(), body=body,
                              content_type="application/json" if body is not None else None, timeout=timeout)
        return json.loads(data) if data else {}

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, payload=None, timeout=REQUEST_TIMEOUT):
        return self.call("POST", path, {} if payload is None else payload, timeout=timeout)

    def patch(self, path, payload):
        return self.call("PATCH", path, payload)

    def put(self, path, payload):
        return self.call("PUT", path, payload)

    def delete(self, path):
        return self.call("DELETE", path)

    def upload_file(self, path, field, filename, content):
        boundary = uuid.uuid4().hex
        filename = re.sub(r'["\r\n\\]', "_", os.path.basename(filename))
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        body = head + content + f"\r\n--{boundary}--\r\n".encode()
        _, data = raw_request("POST", path, token=self._fresh_token(), body=body,
                              content_type=f"multipart/form-data; boundary={boundary}", timeout=UPLOAD_TIMEOUT)
        return json.loads(data) if data else {}


def public_call(method, path, payload=None, timeout=REQUEST_TIMEOUT):
    """Запрос без токена (публичный тест-чат)."""
    body = json.dumps(payload).encode() if payload is not None else None
    _, data = raw_request(method, path, body=body,
                          content_type="application/json" if body is not None else None, timeout=timeout)
    return json.loads(data) if data else {}


# ---------- тексты, хэши, бэкапы ----------

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_prompt_header(text):
    """Снимает служебную шапку «# version: …», которую методология кладёт над промптом."""
    lines = text.split("\n")
    if lines and re.match(r"^#\s*version\s*:", lines[0], re.I):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines)


def lint_prompt(text):
    """Возвращает (ошибки, предупреждения) по правилам методологии. Ошибки блокируют заливку."""
    errors, warnings = [], []
    if not text.strip():
        errors.append("Промпт пустой.")
    for marker in ("{{", "}}", "XXX", "TODO"):
        if marker in text:
            errors.append(f"В промпте остался служебный маркер «{marker}».")
    if re.search(r"(?m)^#\s*version\s*:", text):
        errors.append("Шапка «# version:» не снята.")
    for name in FORBIDDEN_NAMES:
        if name.lower() in text.lower():
            errors.append("В промпте стоит имя персоны, запрещённое Fusion. Придумай другое имя.")
    for banned in ("operator_help_needed", "operator_call", "save_user_data"):
        if banned in text:
            errors.append(f"Выдуманное или устаревшее имя инструмента «{banned}».")
    if re.search(r"(?i)ты работаешь на модел", text):
        errors.append("Нельзя писать «Ты работаешь на модели …».")
    if contains_secret(text):
        errors.append("В тексте похоже на ключ, токен или закрытый ключ. Такое в промпт и базу знаний не кладут.")
    size = len(text)
    if size > 60000:
        errors.append(f"Промпт {size} знаков: больше предела 60 000. Факты вынеси в базу знаний.")
    elif size > 40000:
        warnings.append(f"Промпт {size} знаков: это уже класс «сложный» (до 60 000). Проверь, нет ли лишнего.")
    if re.search(r"(?m)\*\*|^#{1,3} ", text):
        warnings.append("В промпте есть Markdown (** или заголовки #). Промпты пишутся XML-блоками без Markdown.")
    if re.search(r"(?i)\b(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b", text):
        warnings.append("В промпте упомянуты дни недели. Дату и день платформа даёт сама.")
    return errors, warnings


def now_stamp():
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def backup_folder(client, agent_id):
    folder = os.path.join(BACKUP_DIR, validate_client(client), validate_id(agent_id, "id агента"))
    if not _is_inside(folder, BACKUP_DIR):
        raise FusionError("Путь бэкапа вне папки бэкапов.")
    return folder


def backup_path(client, agent_id, label):
    if not LABEL_RE.fullmatch(label or ""):
        raise FusionError("Метка бэкапа: латиница в нижнем регистре, цифры и дефис, до 30 знаков.")
    return os.path.join(backup_folder(client, agent_id), f"{now_stamp()}-{label}.json")


def list_backups(client, agent_id):
    folder = backup_folder(client, agent_id)
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".json"))


def say(text=""):
    print(text, flush=True)


def fail(text):
    print(f"ОШИБКА: {text}", file=sys.stderr, flush=True)
    sys.exit(1)


def run_main(main):
    """Единая обёртка запуска: понятные ошибки без трассировок (трассировка только при FUSION_DEBUG=1)."""
    try:
        main()
    except FusionError as error:
        fail(str(error))
    except KeyboardInterrupt:
        fail("Прервано.")
    except BrokenPipeError:
        sys.exit(1)
    except Exception as error:  # noqa: BLE001
        if os.environ.get("FUSION_DEBUG") == "1":
            raise
        fail(f"Внутренняя ошибка ({type(error).__name__}). Повтори позже или запусти с FUSION_DEBUG=1 в своём окне Терминала.")
