"""База знаний и тест-чат. Тест-чат тратит сообщения тарифа клиента, поэтому ограничен и требует подтверждения."""

import json
import os
import time
import urllib.parse

import fusion_lib as lib

KB_EXTENSIONS = (".md", ".txt", ".pdf", ".docx", ".xlsx")
KB_MAX_BYTES = 20 * 1024 * 1024
SERVICE_MARKERS = ("# version", "парный файл", "служебн", "здесь только справка", "для промпта")
CHAT_DEFAULT_MAX = 40
CHAT_HARD_CAP = 300
CHAT_POLL_SECONDS = 3
CHAT_POLL_ROUNDS = 40


# ---------- база знаний ----------

def list_documents(api, agent_id):
    lib.validate_id(agent_id, "id агента")
    return api.get(f"/documents/?agent_id={agent_id}")


def check_kb_file(path):
    path = lib.safe_input_file(path)
    if not path.lower().endswith(KB_EXTENSIONS):
        raise lib.FusionError(f"Формат не поддерживается. Бери один из: {', '.join(KB_EXTENSIONS)}.")
    if os.path.getsize(path) > KB_MAX_BYTES:
        raise lib.FusionError("Файл больше 20 МБ.")
    if path.lower().endswith((".md", ".txt")):
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read(2_000_000)
            head = content[:2000].lower()
        if lib.contains_secret(content):
            raise lib.FusionError("В файле похоже на ключ или токен. Такое в базу знаний не кладут.")
        found = [m for m in SERVICE_MARKERS if m in head]
        if found:
            raise lib.FusionError(
                f"В начале файла служебные пометки {found}. Клиенту такие строки могут утечь в ответ. Убери шапку."
            )


def wait_processed(api, agent_id, name, timeout_seconds=180, ignore_ids=()):
    deadline = time.time() + timeout_seconds
    status = "?"
    while time.time() < deadline:
        for document in list_documents(api, agent_id):
            if document.get("name") == name and document.get("id") not in ignore_ids:
                status = str(document.get("processing_status"))
                if "COMPLETED" in status.upper():
                    return document
                if "FAIL" in status.upper() or "ERROR" in status.upper():
                    raise lib.FusionError(f"Платформа не смогла обработать «{name}»: статус {status}.")
        time.sleep(3)
    raise lib.FusionError(f"«{name}» не обработался за {timeout_seconds} с (последний статус: {status}).")


def upload_kb(api, agent_id, path, replace_id=None):
    check_kb_file(path)
    path = lib.safe_input_file(path)
    name = os.path.basename(path)
    if replace_id:
        lib.validate_id(replace_id, "id документа")
    known = {d.get("id") for d in list_documents(api, agent_id)}
    with open(path, "rb") as handle:
        api.upload_file(f"/documents/files/{agent_id}", "files", name, handle.read())
    document = wait_processed(api, agent_id, name, ignore_ids=known)
    if replace_id:
        api.delete(f"/documents/{replace_id}?agent_id={agent_id}")
    return document


def delete_document(api, agent_id, document_id):
    lib.validate_id(document_id, "id документа")
    lib.validate_id(agent_id, "id агента")
    api.delete(f"/documents/{document_id}?agent_id={agent_id}")


# ---------- тест-чат ----------

def count_questions(dialogs):
    return sum(len(turns) for turns in dialogs.values())


def _messages_url(token):
    return f"/public/agent-test-chat/{urllib.parse.quote(token, safe='')}/messages"


def _ask(token, text, seen):
    lib.public_call("POST", _messages_url(token), {"text": text})
    previous = None
    replies = []
    for _ in range(CHAT_POLL_ROUNDS):
        time.sleep(CHAT_POLL_SECONDS)
        data = lib.public_call("GET", _messages_url(token))
        replies = [m for m in data.get("messages", []) if m.get("role") == "ai"]
        key = [m.get("id") for m in replies]
        if len(replies) > seen and key == previous:
            break
        previous = key
    return replies


def run_chat(api, agent_id, dialogs, max_messages):
    """Каждый диалог идёт по новой ссылке (новый чат). Возвращает расшифровку."""
    lib.validate_id(agent_id, "id агента")
    total = count_questions(dialogs)
    if total > max_messages:
        raise lib.FusionError(f"Запрошено {total} сообщений, а лимит прогона {max_messages}. Сократи сценарий.")
    transcript = {}
    try:
        for name, turns in dialogs.items():
            link = api.post(f"/agents/{agent_id}/test-chat/share-link", {})
            token, seen, record = lib.validate_id(link["token"], "токен тест-чата"), 0, []
            for text in turns:
                replies = _ask(token, text, seen)
                answers = [(m.get("content") or {}).get("text") or "" for m in replies[seen:]]
                seen = len(replies)
                record.append({"client": text, "agent": answers})
            transcript[name] = record
    finally:
        try:
            api.delete(f"/agents/{agent_id}/test-chat/share-link")
        except lib.FusionError:
            pass  # ссылка и так живёт ограниченное время
    return transcript


def messages_left(api):
    try:
        data = api.get("/subscriptions/my/messages")
    except lib.FusionError:
        return None
    return data.get("remaining")


def dump_transcript(transcript, path):
    lib.write_private_file(path, json.dumps(transcript, ensure_ascii=False, indent=1))


def reserve_daily_budget(client, count):
    """Суточный предел тестовых сообщений на клиента: защита от бесконечных прогонов."""
    path = os.path.join(lib.CONFIG_DIR, f"spend-{lib.validate_client(client)}.json")
    today = time.strftime("%Y-%m-%d")
    used = 0
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        used = record.get("count", 0) if record.get("day") == today else 0
    if used + count > lib.DAILY_CHAT_LIMIT:
        raise lib.FusionError(f"Суточный предел тест-сообщений {lib.DAILY_CHAT_LIMIT}: уже {used}, ещё {count} нельзя. "
                              "Нужно больше: задай FUSION_DAILY_CHAT_LIMIT или продолжи завтра.")
    lib.write_private_file(path, json.dumps({"day": today, "count": used + count}))
