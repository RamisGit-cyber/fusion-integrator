"""Операции над агентом: бэкап, откат, промпт, поля заявки, дожимки. Эндпоинты проверены по коду платформы."""

import json
import os
import re
import uuid

import fusion_lib as lib

# Поля агента, которые бэкап сохраняет и откат возвращает. is_active не трогаем: включение — отдельное решение.
AGENT_FIELDS = (
    "name", "description", "temperature", "system_prompt", "reply_delay_seconds",
    "disable_ai_on_help_request", "disable_ai_on_human_intervention",
    "is_auto_reengage_enabled", "auto_reengage_delay_seconds",
    "is_voice_processing_enabled", "is_image_processing_enabled", "agent_timezone",
)
FOLLOWUP_FIELDS = ("enabled", "steps", "safe_window")

DEFAULT_LEAD_FIELDS = [
    {"label": "Телефон", "required": True, "description": "Номер телефона клиента для связи менеджера.",
     "examples": ["0555 123 456", "+996 555 123 456"]},
    {"label": "Имя клиента", "required": False, "description": "Как зовут клиента.", "examples": ["Азамат", "Гульнара"]},
    {"label": "Интерес", "required": False, "description": "Что клиента интересует: услуга, товар, объём.", "examples": []},
    {"label": "Вопрос менеджеру", "required": False,
     "description": "Что клиент просил уточнить у менеджера.", "examples": []},
]


# ---------- агент, бэкап, откат ----------

def get_agent(api, agent_id):
    lib.validate_id(agent_id, "id агента")
    return api.get(f"/agents/{agent_id}")


def read_configure(api, agent_id):
    lib.validate_id(agent_id, "id агента")
    """Получить или создать конфигурацию заявок агента (так устроен эндпоинт платформы)."""
    return api.post(f"/threads/configure?agent_id={agent_id}", {})


def take_snapshot(api, agent_id, label):
    agent = get_agent(api, agent_id)
    configure = read_configure(api, agent_id)
    try:
        followups = api.get(f"/agents/{agent_id}/follow-ups")
    except lib.FusionError:
        followups = None
    documents = api.get(f"/documents/?agent_id={agent_id}")
    snapshot = {
        "taken_at": lib.now_stamp(),
        "agent_id": agent_id,
        "agent": {key: agent.get(key) for key in AGENT_FIELDS},
        "prompt_sha256": lib.sha256_text(agent.get("system_prompt") or ""),
        "followups": {key: followups.get(key) for key in FOLLOWUP_FIELDS} if followups else None,
        "configure": {
            "user_id": configure.get("user_id"),
            "individual_notifications": configure.get("individual_notifications"),
            "user_profile_template": configure.get("user_profile_template") or [],
        },
        "documents": [{"id": d.get("id"), "name": d.get("name")} for d in (documents or [])],
    }
    path = lib.backup_path(api.client, agent_id, label)
    lib.write_private_file(path, json.dumps(snapshot, ensure_ascii=False, indent=1))
    return path, snapshot


def restore_snapshot(api, agent_id, path):
    if not lib._is_inside(path, lib.backup_folder(api.client, agent_id)):
        raise lib.FusionError("Откат возможен только из бэкапа этого агента (папка бэкапов плагина).")
    with open(path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    if snapshot.get("agent_id") != agent_id:
        raise lib.FusionError("Этот бэкап снят с другого агента. Откат отменён.")
    take_snapshot(api, agent_id, "before-restore")
    api.patch(f"/agents/{agent_id}", {k: v for k, v in snapshot["agent"].items()
                                      if k in AGENT_FIELDS and v is not None})
    if snapshot.get("followups"):
        api.put(f"/agents/{agent_id}/follow-ups", snapshot["followups"])
    configure = snapshot.get("configure") or {}
    if configure.get("user_id"):
        lib.validate_id(configure["user_id"], "id владельца")
        api.patch(f"/threads/configure/{configure['user_id']}/{agent_id}", {
            "individual_notifications": configure.get("individual_notifications"),
            "user_profile_template": configure.get("user_profile_template") or [],
        })
    live = get_agent(api, agent_id).get("system_prompt") or ""
    return lib.sha256_text(live) == snapshot["prompt_sha256"]


# ---------- промпт ----------

def push_prompt(api, agent_id, text):
    """Заливает промпт и сверяет побайтно. При расхождении сам откатывает и сообщает."""
    text = lib.strip_prompt_header(text)
    errors, warnings = lib.lint_prompt(text)
    for warning in warnings:
        lib.say(f"Предупреждение: {warning}")
    if errors:
        raise lib.FusionError("Линтер не пропустил промпт:\n- " + "\n- ".join(errors))
    backup, snapshot = take_snapshot(api, agent_id, "before-prompt")
    lib.say(f"Бэкап до правки: {backup}")
    api.patch(f"/agents/{agent_id}", {"system_prompt": text})
    live = get_agent(api, agent_id).get("system_prompt") or ""
    if live.encode("utf-8") != text.encode("utf-8"):
        api.patch(f"/agents/{agent_id}", {"system_prompt": snapshot["agent"]["system_prompt"] or ""})
        raise lib.FusionError(
            "Побайтная сверка НЕ прошла: на платформе лежит не тот текст. Промпт возвращён из бэкапа. "
            "Пересоздай файл без особых символов и повтори."
        )
    return lib.sha256_text(text), len(text)


# ---------- поля заявки ----------

def build_lead_fields(agent_id, spec):
    template = []
    labels = set()
    for item in spec:
        label = str(item.get("label", "")).strip()
        if not 1 <= len(label) <= 50:
            raise lib.FusionError(f"Название поля «{label}» должно быть от 1 до 50 знаков.")
        if label in labels:
            raise lib.FusionError(f"Поле «{label}» повторяется.")
        labels.add(label)
        description = (item.get("description") or "")[:300]
        examples = [str(e) for e in (item.get("examples") or [])][:5]
        template.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"fusion-integrator/{agent_id}/{label}")),
            "label": label,
            "description": description,
            "examples": examples,
            "is_visible": True,
            "is_required": bool(item.get("required", False)),
        })
    phone = [f for f in template if f["label"] == "Телефон"]
    if not phone or not phone[0]["is_required"]:
        raise lib.FusionError(
            "Поле «Телефон» должно быть в списке и быть обязательным: без него заявка менеджеру не уходит."
        )
    return template


def set_lead_fields(api, agent_id, spec):
    template = build_lead_fields(agent_id, spec)
    backup, _ = take_snapshot(api, agent_id, "before-fields")
    lib.say(f"Бэкап до правки: {backup}")
    configure = read_configure(api, agent_id)
    lib.validate_id(configure["user_id"], "id владельца")
    api.patch(f"/threads/configure/{configure['user_id']}/{agent_id}",
              {"individual_notifications": True, "user_profile_template": template})
    after = read_configure(api, agent_id)
    ok = bool(after.get("individual_notifications")) and len(after.get("user_profile_template") or []) == len(template)
    return template, ok


# ---------- дожимки ----------

def validate_followups(settings):
    unknown = set(settings) - set(FOLLOWUP_FIELDS)
    if unknown:
        raise lib.FusionError(f"Лишние ключи в дожимках: {sorted(unknown)}. Допустимы: {list(FOLLOWUP_FIELDS)}.")
    steps = settings.get("steps", [])
    if len(steps) > 5:
        raise lib.FusionError("Шагов дожима не больше 5.")
    for number, step in enumerate(steps, 1):
        if not 1 <= int(step.get("delay_minutes", 0)) <= 1380:
            raise lib.FusionError(f"Шаг {number}: задержка от 1 до 1380 минут (23 часа).")
        prompt = step.get("prompt", "")
        if not 1 <= len(prompt) <= 4000:
            raise lib.FusionError(f"Шаг {number}: текст от 1 до 4000 знаков.")
        if "молчи" not in prompt.lower():
            lib.say(f"Предупреждение: в шаге {number} нет хвоста «молчи, если…» (отказ, обещал написать сам, "
                    "заявка уже у менеджера, файл не отправлялся). Платформа не знает состояния диалога.")
    window = settings.get("safe_window")
    if window and window.get("enabled") is not True:
        lib.say("Предупреждение: «безопасное время» выключено, дожимки могут уходить ночью.")
    return settings


def set_followups(api, agent_id, settings):
    validate_followups(settings)
    backup, _ = take_snapshot(api, agent_id, "before-followups")
    lib.say(f"Бэкап до правки: {backup}")
    return api.put(f"/agents/{agent_id}/follow-ups", settings)


# ---------- мелочи ----------

NOTIFY_CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")


def read_json_file(path):
    path = lib.safe_input_file(path)
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as error:
            raise lib.FusionError(f"Файл {path} не JSON: {error}") from None
