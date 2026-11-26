"""Файлы для отправки: картинки, PDF и наборы (до 10 файлов). Агент сам находит и отправляет их клиенту.

Правила:
- у каждого файла и набора есть описание «КОГДА AI отправляет»: по нему агент решает, что отправить;
- набор создаётся ВЫКЛЮЧЕННЫМ, включать его нужно отдельной командой после проверки;
- удаления здесь нет: убрать файл или набор можно только в кабинете (после удаления вернуть нельзя);
- файлы читаются только из папки проекта (lib.safe_input_file), перед правкой сохраняется текущая запись.
"""

import json
import mimetypes
import os
import re

import fusion_lib as lib

ASSET_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf")
ASSET_MAX_BYTES = 15 * 1024 * 1024
SET_MAX_FILES = 10
MAGIC = {".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG",), ".gif": (b"GIF8",),
         ".webp": (b"RIFF",), ".pdf": (b"%PDF-",)}
TITLE_MAX, WHEN_MAX, CAPTION_MAX = 120, 600, 400
TAGS_RE = re.compile(r"[\wЀ-ӿ ,.-]{1,200}")
SHOWN_KEYS = ("id", "title", "is_enabled", "description_for_ai", "default_caption", "tags")


def _text(value, label, limit, required=False):
    if value is None:
        if required:
            raise lib.FusionError(f"Нужно поле {label}.")
        return None
    value = value.strip()
    if re.search(r"[\x00-\x08\x0b-\x1f]", value):
        raise lib.FusionError(f"В поле {label} есть управляющие символы.")
    if required and not value:
        raise lib.FusionError(f"Поле {label} пустое.")
    if len(value) > limit:
        raise lib.FusionError(f"Поле {label} длиннее {limit} знаков.")
    if lib.contains_secret(value):
        raise lib.FusionError(f"В поле {label} похоже на ключ или токен доступа. Такое не отправляют клиентам.")
    return value


def check_asset_file(path):
    real = lib.safe_input_file(path)
    if not real.lower().endswith(ASSET_EXTENSIONS):
        raise lib.FusionError(f"Формат не поддерживается. Бери один из: {', '.join(ASSET_EXTENSIONS)}.")
    size = os.path.getsize(real)
    if size == 0 or size > ASSET_MAX_BYTES:
        raise lib.FusionError("Файл пустой или больше 15 МБ.")
    return real


def read_asset(path):
    """Читает файл с проверкой начала (должно соответствовать расширению) и верхней границей размера."""
    real = check_asset_file(path)
    extension = os.path.splitext(real)[1].lower()
    with open(real, "rb") as handle:
        content = handle.read(ASSET_MAX_BYTES + 1)
    if len(content) > ASSET_MAX_BYTES:
        raise lib.FusionError("Файл больше 15 МБ.")
    if not content.startswith(MAGIC[extension]):
        raise lib.FusionError("Содержимое файла не похоже на его формат. Проверь, что это настоящая картинка или PDF.")
    return real, content


def _rows(payload):
    """Список записей: платформа отдаёт либо список, либо одну запись."""
    if isinstance(payload, list):
        return payload
    return [payload] if payload else []


def _short(record):
    if not isinstance(record, dict):
        return str(record)[:200]
    shown = {key: record[key] for key in SHOWN_KEYS if key in record}
    if isinstance(record.get("items"), list):  # состав набора: id и названия файлов, без служебных ссылок
        shown["items"] = [{"asset_id": item.get("asset_id"), "title": (item.get("asset") or {}).get("title"),
                           "is_enabled": (item.get("asset") or {}).get("is_enabled")}
                          for item in record["items"] if isinstance(item, dict)]
    return shown


def show(payload):
    """Печатает записи без служебных полей (временных ссылок на файлы и т.п.)."""
    rows = _rows(payload)
    for row in rows:
        lib.say(json.dumps(_short(row), ensure_ascii=False))
    if not rows:
        lib.say("Пусто.")


def _save_before(api, agent_id, label, record):
    path = lib.backup_path(api.client, agent_id, label)
    lib.write_private_file(path, json.dumps(_short(record), ensure_ascii=False, indent=1))  # без временных ссылок
    lib.say(f"Запись до правки сохранена: {path}")


def _find(api, agent_id, kind, target):
    for row in _rows(api.get(f"/agents/{agent_id}/{kind}")):
        if isinstance(row, dict) and row.get("id") == target:
            return row
    raise lib.FusionError(f"Не нашёл запись {target} у этого агента. Проверь id командой list.")


def _ids(agent_id, target, what):
    lib.validate_id(agent_id, "id агента")
    lib.validate_id(target, what)


# ---------- файлы ----------

def list_assets(api, agent_id):
    show(api.get(f"/agents/{lib.validate_id(agent_id, 'id агента')}/assets"))


def upload_asset(api, agent_id, path, title, when, caption=None, tags=None, enabled=False):
    """По умолчанию файл загружается ВЫКЛЮЧЕННЫМ: агент не отправит его, пока его не включат (assets enable)."""
    lib.validate_id(agent_id, "id агента")
    real, content = read_asset(path)
    fields = {"title": _text(title, "--title", TITLE_MAX, True),
              "description_for_ai": _text(when, "--when (КОГДА AI отправляет)", WHEN_MAX, True),
              "is_enabled": "true" if enabled else "false"}
    if caption:
        fields["default_caption"] = _text(caption, "--caption", CAPTION_MAX)
    if tags:
        if not TAGS_RE.fullmatch(tags):
            raise lib.FusionError("Метки: слова через запятую, без спецсимволов.")
        fields["tags"] = _text(tags, "--tags", 200)
    mime = mimetypes.guess_type(real)[0] or "application/octet-stream"
    created = lib.upload_form(api, f"/agents/{agent_id}/assets", fields, "file", os.path.basename(real), content, mime)
    show(created)
    lib.say("Описание «КОГДА AI отправляет» должно совпадать с базой знаний и промптом. "
            + ("Файл включён: агент может отправить его клиентам." if enabled else "Файл ВЫКЛЮЧЕН: включи после проверки: assets enable --agent ID --asset ID on"))


def update_asset(api, agent_id, asset_id, title=None, when=None):
    _ids(agent_id, asset_id, "id файла")
    body = {key: value for key, value in (("title", _text(title, "--title", TITLE_MAX)),
                                          ("description_for_ai", _text(when, "--when", WHEN_MAX))) if value is not None}
    if not body:
        raise lib.FusionError("Нечего менять: укажи --title или --when.")
    _save_before(api, agent_id, "before-asset-update", _find(api, agent_id, "assets", asset_id))
    show(api.patch(f"/agents/{agent_id}/assets/{asset_id}", body))


def enable_asset(api, agent_id, asset_id, state):
    _ids(agent_id, asset_id, "id файла")
    _save_before(api, agent_id, "before-asset-enable", _find(api, agent_id, "assets", asset_id))
    show(api.post(f"/agents/{agent_id}/assets/{asset_id}/enable", {"is_enabled": state == "on"}))


# ---------- наборы ----------

def _asset_ids(raw):
    ids = [lib.validate_id(item.strip(), "id файла") for item in (raw or "").split(",") if item.strip()]
    if len(ids) != len(set(ids)):
        raise lib.FusionError("В наборе есть повторяющиеся файлы.")
    if len(ids) > SET_MAX_FILES:
        raise lib.FusionError(f"В наборе максимум {SET_MAX_FILES} файлов.")
    return ids


def list_sets(api, agent_id):
    show(api.get(f"/agents/{lib.validate_id(agent_id, 'id агента')}/asset-collections"))


def create_set(api, agent_id, title, when, assets):
    lib.validate_id(agent_id, "id агента")
    ids = _asset_ids(assets)
    if not ids:
        raise lib.FusionError("Укажи файлы набора: --assets ID1,ID2.")
    body = {"title": _text(title, "--title", TITLE_MAX, True),
            "description_for_ai": _text(when, "--when (КОГДА AI отправляет)", WHEN_MAX, True), "asset_ids": ids}
    show(api.post(f"/agents/{agent_id}/asset-collections", body))
    lib.say("Набор создан ВЫКЛЮЧЕННЫМ. Проверь состав и включи: sets enable --agent ID --set ID on")


def update_set(api, agent_id, set_id, title=None, when=None, assets=None):
    _ids(agent_id, set_id, "id набора")
    body = {key: value for key, value in (("title", _text(title, "--title", TITLE_MAX)),
                                          ("description_for_ai", _text(when, "--when", WHEN_MAX))) if value is not None}
    if assets is not None:
        body["asset_ids"] = _asset_ids(assets)
    if not body:
        raise lib.FusionError("Нечего менять: укажи --title, --when или --assets.")
    _save_before(api, agent_id, "before-set-update", _find(api, agent_id, "asset-collections", set_id))
    show(api.patch(f"/agents/{agent_id}/asset-collections/{set_id}", body))


def enable_set(api, agent_id, set_id, state):
    _ids(agent_id, set_id, "id набора")
    _save_before(api, agent_id, "before-set-enable", _find(api, agent_id, "asset-collections", set_id))
    show(api.post(f"/agents/{agent_id}/asset-collections/{set_id}/enable", {"is_enabled": state == "on"}))
