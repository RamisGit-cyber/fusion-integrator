#!/usr/bin/env python3
"""Fusion-интегратор: команды для сборки и настройки агента клиента.

    python3 fusion.py --client <имя> <группа> <команда> [параметры]

Вход выполняется отдельно: fusion-login <имя> (скрытый ввод пароля в Терминале).
Каждая правка сначала делает бэкап в ~/.config/fusion-integrator/backups/.
Список команд: python3 fusion.py --help
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_kb_chat as kb  # noqa: E402
import fusion_lib as lib  # noqa: E402
import fusion_ops as ops  # noqa: E402

TEST_NAME_PREFIX = "ТЕСТ интегратор-плагин"
CHANNELS = ("telegram", "whatsapp", "whatsapp-cloud-api", "instagram", "web-widget")


# ---------- агенты ----------

def cmd_agents_list(api, args):
    for agent in api.get("/agents/my?limit=100"):
        prompt_len = len(agent.get("system_prompt") or "")
        state = "включён" if agent.get("is_active") else "выключен"
        lib.say(f"{agent['id']}  {state:9}  промпт {prompt_len:>6} знаков  {agent['name']}")


def cmd_agents_show(api, args):
    agent = ops.get_agent(api, args.agent)
    prompt = agent.get("system_prompt") or ""
    lib.say(json.dumps({key: agent.get(key) for key in ("id", "name", "is_active", "temperature", "reply_delay_seconds",
                                                        "agent_timezone", "is_voice_processing_enabled",
                                                        "is_image_processing_enabled", "disable_ai_on_human_intervention")},
                       ensure_ascii=False, indent=1))
    lib.say(f"промпт: {len(prompt)} знаков, sha256 {lib.sha256_text(prompt)[:8]}")


def cmd_agents_create(api, args):
    prompt = ""
    if args.prompt_file:
        prompt = lib.strip_prompt_header(open(lib.safe_input_file(args.prompt_file), encoding="utf-8").read())
        errors, _ = lib.lint_prompt(prompt)
        if errors:
            raise lib.FusionError("Линтер не пропустил промпт:\n- " + "\n- ".join(errors))
    agent = api.post("/agents", {"name": args.name, "description": args.description or "",
                                 "temperature": 0.2, "system_prompt": prompt})
    api.patch(f"/agents/{agent['id']}", {"temperature": 0.2, "agent_timezone": args.timezone,
                                         "is_voice_processing_enabled": True, "is_image_processing_enabled": True})
    lib.say(f"Агент создан: {agent['id']} «{args.name}». Каналы не подключены, клиентам он не отвечает.")


def cmd_agents_clone(api, args):
    ops.take_snapshot(api, args.agent, "before-clone")
    clone = api.post(f"/agents/{args.agent}/clone", {})
    lib.say(f"Копия создана: {clone['id']} «{clone['name']}». Правь копию, живого не трогай.")


def cmd_agents_set(api, args):
    changes = {}
    if args.temperature is not None:
        if not 0.0 <= args.temperature <= 1.0:
            raise lib.FusionError("Температура от 0 до 1.")
        changes["temperature"] = args.temperature
    if args.reply_delay is not None:
        changes["reply_delay_seconds"] = args.reply_delay
    if args.timezone:
        changes["agent_timezone"] = args.timezone
    if args.voice is not None:
        changes["is_voice_processing_enabled"] = args.voice == "on"
    if args.image is not None:
        changes["is_image_processing_enabled"] = args.image == "on"
    if not changes:
        raise lib.FusionError("Нечего менять: укажи хотя бы один параметр.")
    path, _ = ops.take_snapshot(api, args.agent, "before-settings")
    lib.say(f"Бэкап до правки: {path}")
    api.patch(f"/agents/{args.agent}", changes)
    lib.say(f"Изменено: {json.dumps(changes, ensure_ascii=False)}")


def cmd_agents_active(api, args):
    if args.state == "on" and not args.confirm_live:
        raise lib.FusionError("Включение агента для живых клиентов: сначала регресс и «ок» человека в этом разговоре, "
                              "потом добавь --confirm-live.")
    path, _ = ops.take_snapshot(api, args.agent, "before-active")
    lib.say(f"Бэкап: {path}")
    api.patch(f"/agents/{args.agent}", {"is_active": args.state == "on"})
    lib.say(f"Агент {'включён' if args.state == 'on' else 'выключен'}.")


def cmd_agents_delete(api, args):
    agent = ops.get_agent(api, args.agent)
    if not agent["name"].startswith(TEST_NAME_PREFIX):
        raise lib.FusionError(f"Удалять можно только тестовых агентов с именем, начинающимся на «{TEST_NAME_PREFIX}». "
                              f"У этого имя «{agent['name']}». Удаление боевого агента делает человек в кабинете.")
    ops.take_snapshot(api, args.agent, "before-delete")
    api.delete(f"/agents/{args.agent}")
    lib.say(f"Тестовый агент «{agent['name']}» удалён.")


# ---------- бэкап, откат ----------

def cmd_backup(api, args):
    path, snapshot = ops.take_snapshot(api, args.agent, args.label)
    lib.say(f"Бэкап: {path}\nпромпт sha256 {snapshot['prompt_sha256'][:8]}, "
            f"полей заявки {len(snapshot['configure']['user_profile_template'])}, документов {len(snapshot['documents'])}")
    lib.say("База знаний в бэкап входит только списком имён: файлы после удаления автоматически не вернуть.")


def cmd_backups_list(api, args):
    for path in lib.list_backups(api.client, args.agent):
        lib.say(path)


def cmd_restore(api, args):
    path = args.file or (lib.list_backups(api.client, args.agent) or [None])[-1]
    if not path:
        raise lib.FusionError("Бэкапов нет.")
    ok = ops.restore_snapshot(api, args.agent, path)
    lib.say(f"Откат из {path}: {'промпт совпал побайтно' if ok else 'ПРОМПТ НЕ СОВПАЛ, проверь вручную'}.")


# ---------- промпт ----------

def cmd_prompt_pull(api, args):
    text = ops.get_agent(api, args.agent).get("system_prompt") or ""
    lib.write_private_file(lib.safe_output_file(args.out), text)
    lib.say(f"Промпт сохранён в {args.out}: {len(text)} знаков, sha256 {lib.sha256_text(text)[:8]}")


def cmd_prompt_push(api, args):
    text = open(lib.safe_input_file(args.file), encoding="utf-8").read()
    digest, size = ops.push_prompt(api, args.agent, text)
    lib.say(f"Промпт залит и сверен побайтно: {size} знаков, sha256 {digest[:8]}.")


def cmd_prompt_lint(api, args):
    errors, warnings = lib.lint_prompt(lib.strip_prompt_header(open(lib.safe_input_file(args.file), encoding="utf-8").read()))
    for line in errors:
        lib.say(f"ОШИБКА: {line}")
    for line in warnings:
        lib.say(f"Предупреждение: {line}")
    lib.say("Линтер: чисто." if not errors and not warnings else "")
    if errors:
        sys.exit(1)


# ---------- база знаний, поля, дожимки, уведомления ----------

def cmd_kb_list(api, args):
    for doc in kb.list_documents(api, args.agent):
        lib.say(f"{doc.get('id')}  {doc.get('processing_status')}  {doc.get('name')}")


def cmd_kb_upload(api, args):
    ops.take_snapshot(api, args.agent, "before-kb")
    document = kb.upload_kb(api, args.agent, args.file, args.replace_id)
    lib.say(f"Загружено и обработано: {document.get('name')} ({document.get('id')})."
            + (" Старый документ удалён." if args.replace_id else ""))


def cmd_kb_delete(api, args):
    ops.take_snapshot(api, args.agent, "before-kb-delete")
    kb.delete_document(api, args.agent, args.doc)
    lib.say("Документ удалён.")


def cmd_fields_show(api, args):
    config = ops.read_configure(api, args.agent)
    lib.say(f"Личные уведомления: {config.get('individual_notifications')}")
    for field in config.get("user_profile_template") or []:
        lib.say(f"- {field.get('label')} ({'обязательное' if field.get('is_required') else 'необязательное'}) "
                f"id {field.get('id')}")
    if not config.get("user_profile_template"):
        lib.say("Полей заявки НЕТ: агент не сможет передать заявку менеджеру. Выполни: fields set")


def cmd_fields_set(api, args):
    spec = ops.read_json_file(args.file) if args.file else ops.DEFAULT_LEAD_FIELDS
    template, ok = ops.set_lead_fields(api, args.agent, spec)
    lib.say(f"Поля заявки заданы ({len(template)}), обязательных: {sum(f['is_required'] for f in template)}. "
            f"Перечитал с сервера: {'сохранилось' if ok else 'НЕ СОХРАНИЛОСЬ'}.")
    lib.say("Обязательные поля должны входить в то, что промпт собирает ДО передачи заявки.")


def cmd_followups_show(api, args):
    lib.say(json.dumps(api.get(f"/agents/{args.agent}/follow-ups"), ensure_ascii=False, indent=1))


def cmd_followups_set(api, args):
    result = ops.set_followups(api, args.agent, ops.read_json_file(args.file))
    lib.say(f"Дожимки заданы: включены={result.get('enabled')}, шагов {len(result.get('steps') or [])}.")


def cmd_notify_link(api, args):
    info = api.post(f"/telegram-notifications/agents/{args.agent}/links/group", {})
    url = str(info.get("url", ""))
    if not url.startswith(("https://t.me/", "https://telegram.me/")):
        raise lib.FusionError("Платформа вернула неожиданную ссылку, не показываю её. Скажи человеку.")
    lib.say(f"Ссылка подключения (действует до {info.get('expires_at')}): {url}")
    lib.say("Дальше: клиент создаёт группу в Telegram, добавляет в неё @FusionNotify_Bot; бот пришлёт код "
            "вида XXXX-XXXX. Код вводим командой: notify confirm --code XXXX-XXXX")


def cmd_notify_confirm(api, args):
    code = args.code.strip().upper()
    if not ops.NOTIFY_CODE_RE.match(code):
        raise lib.FusionError("Код должен быть вида XXXX-XXXX (буквы и цифры).")
    api.post(f"/telegram-notifications/agents/{args.agent}/groups/confirm", {"code": code})
    lib.say("Группа подтверждена. Проверь: notify status")


def cmd_notify_status(api, args):
    lib.say(json.dumps(api.get(f"/telegram-notifications/agents/{args.agent}/recipients"), ensure_ascii=False, indent=1))
    lib.say(json.dumps(api.get(f"/telegram-notifications/agents/{args.agent}/settings"), ensure_ascii=False, indent=1))


def cmd_channels(api, args):
    for channel in CHANNELS:
        try:
            info = api.get(f"/messenger-connections/{channel}/{args.agent}/connection")
            lib.say(f"{channel:20} подключён: {'да' if info else 'нет'}")
        except lib.FusionError:
            lib.say(f"{channel:20} не подключён")
    lib.say("Подключение каналов (QR, вход Meta) человек делает в кабинете сам.")


def cmd_account(api, args):
    data = api.get("/subscriptions/my/messages")
    lib.say(f"Сообщений: лимит {data.get('total_limit')}, использовано {data.get('used')}, "
            f"осталось {data.get('remaining')}, подписка активна: {data.get('subscription_active')}")


# ---------- тест-чат ----------

def cmd_chat(api, args):
    if args.scenario:
        dialogs = ops.read_json_file(args.scenario)
    elif args.message:
        dialogs = {"диалог": args.message}
    else:
        raise lib.FusionError("Нужны --message (можно несколько) или --scenario файл.json.")
    if not isinstance(dialogs, dict) or not all(isinstance(v, list) and v for v in dialogs.values()):
        raise lib.FusionError('Сценарий: {"имя диалога": ["реплика 1", "реплика 2"], ...}')
    if args.out:
        lib.safe_output_file(args.out)  # проверяем до траты сообщений
    max_messages = min(args.max, kb.CHAT_HARD_CAP)
    total = kb.count_questions(dialogs)
    left = kb.messages_left(api)
    lib.say(f"ВНИМАНИЕ: прогон отправит {total} сообщений; каждое списывается из тарифа клиента "
            f"(осталось: {left if left is not None else 'неизвестно'}). Лимит прогона: {max_messages}.")
    if not args.confirm_spend:
        raise lib.FusionError("Не запущено. Спроси человека и добавь --confirm-spend, если он согласен.")
    kb.reserve_daily_budget(api.client, total)
    transcript = kb.run_chat(api, args.agent, dialogs, max_messages)
    for name, record in transcript.items():
        lib.say(f"\n=== {name} ===")
        for turn in record:
            lib.say(f"КЛИЕНТ: {turn['client']}")
            for answer in turn["agent"]:
                lib.say(f"АГЕНТ:  {answer}")
    if args.out:
        kb.dump_transcript(transcript, lib.safe_output_file(args.out))
        lib.say(f"\nРасшифровка: {args.out}")


# ---------- разбор аргументов ----------

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", help="имя клиента (или переменная FUSION_CLIENT)")
    groups = parser.add_subparsers(dest="group", required=True)

    def add(_unused, name, handler, help_text, *arguments, parent=None):
        sub = parent.add_parser(name, help=help_text)
        for flags, options in arguments:
            sub.add_argument(*flags, **options)
        sub.set_defaults(handler=handler)
        return sub

    A = (("--agent",), {"required": True, "help": "id агента"})

    add("", "account", cmd_account, "сколько сообщений осталось у клиента", parent=groups)
    agents = groups.add_parser("agents", help="агенты").add_subparsers(dest="command", required=True)
    add("", "list", cmd_agents_list, "список агентов", parent=agents)
    add("", "show", cmd_agents_show, "параметры агента", A, parent=agents)
    add("", "create", cmd_agents_create, "создать агента (без каналов)",
        (("--name",), {"required": True}), (("--description",), {}), (("--prompt-file",), {}),
        (("--timezone",), {"default": "Asia/Bishkek"}), parent=agents)
    add("", "clone", cmd_agents_clone, "сделать копию агента для правок", A, parent=agents)
    add("", "set", cmd_agents_set, "температура, задержка, часовой пояс, голос, фото", A,
        (("--temperature",), {"type": float}), (("--reply-delay",), {"type": int}), (("--timezone",), {}),
        (("--voice",), {"choices": ("on", "off")}), (("--image",), {"choices": ("on", "off")}), parent=agents)
    add("", "active", cmd_agents_active, "включить или выключить агента", A,
        (("state",), {"choices": ("on", "off")}),
        (("--confirm-live",), {"action": "store_true", "help": "регресс пройден, человек сказал «ок»"}), parent=agents)
    add("", "delete", cmd_agents_delete, "удалить ТЕСТОВОГО агента (имя начинается на «ТЕСТ интегратор-плагин»)", A, parent=agents)

    add("", "backup", cmd_backup, "снять бэкап агента", A, (("--label",), {"default": "manual"}), parent=groups)
    add("", "backups", cmd_backups_list, "список бэкапов", A, parent=groups)
    add("", "restore", cmd_restore, "откат из бэкапа (по умолчанию из последнего)", A, (("--file",), {}), parent=groups)

    prompt = groups.add_parser("prompt", help="промпт").add_subparsers(dest="command", required=True)
    add("", "pull", cmd_prompt_pull, "выгрузить промпт в файл", A, (("--out",), {"required": True}), parent=prompt)
    add("", "push", cmd_prompt_push, "залить промпт с побайтной сверкой", A, (("--file",), {"required": True}), parent=prompt)
    add("", "lint", cmd_prompt_lint, "проверить файл промпта линтером", (("--file",), {"required": True}), parent=prompt)

    knowledge = groups.add_parser("kb", help="база знаний").add_subparsers(dest="command", required=True)
    add("", "list", cmd_kb_list, "документы базы", A, parent=knowledge)
    add("", "upload", cmd_kb_upload, "загрузить файл в базу", A, (("--file",), {"required": True}),
        (("--replace-id",), {"help": "id старого документа, удалить после успешной загрузки"}), parent=knowledge)
    add("", "delete", cmd_kb_delete, "удалить документ базы", A, (("--doc",), {"required": True}), parent=knowledge)

    fields = groups.add_parser("fields", help="поля заявки").add_subparsers(dest="command", required=True)
    add("", "show", cmd_fields_show, "показать поля заявки", A, parent=fields)
    add("", "set", cmd_fields_set, "задать поля заявки («Телефон» обязателен)", A, (("--file",), {}), parent=fields)

    follow = groups.add_parser("followups", help="дожимки").add_subparsers(dest="command", required=True)
    add("", "show", cmd_followups_show, "показать дожимки", A, parent=follow)
    add("", "set", cmd_followups_set, "задать дожимки из JSON", A, (("--file",), {"required": True}), parent=follow)

    notify = groups.add_parser("notify", help="уведомления в Telegram").add_subparsers(dest="command", required=True)
    add("", "link", cmd_notify_link, "получить ссылку подключения группы", A, parent=notify)
    add("", "confirm", cmd_notify_confirm, "подтвердить группу кодом от бота", A, (("--code",), {"required": True}), parent=notify)
    add("", "status", cmd_notify_status, "кто получает уведомления", A, parent=notify)

    add("", "channels", cmd_channels, "какие каналы подключены (только чтение)", A, parent=groups)
    add("", "chat", cmd_chat, "тест-чат (тратит сообщения клиента)", A,
        (("--message",), {"action": "append"}), (("--scenario",), {}), (("--out",), {}),
        (("--max",), {"type": int, "default": kb.CHAT_DEFAULT_MAX}),
        (("--confirm-spend",), {"action": "store_true", "help": "человек согласился на трату сообщений"}), parent=groups)
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    for name in ("agent", "doc", "replace_id"):
        if getattr(args, name, None):
            lib.validate_id(getattr(args, name), name)
    offline = args.handler is cmd_prompt_lint  # линтер работает без входа
    api = None if offline else lib.Api(lib.resolve_client(args.client))
    args.handler(api, args)


if __name__ == "__main__":
    lib.run_main(lambda: main(sys.argv[1:]))
