#!/usr/bin/env python3
"""Обновляет методологию промпт-архитектора в плагине из единого источника.

Источник правды: папка prompt-architect/ в рабочем пространстве Fusion (architect-core.md,
lessons-checklist.md); путь и таблица замен в tools/sync-config.local.json. Здесь НЕ второй вариант методологии, а механическая копия:
- текст ядра копируется как есть; меняются только несколько внутренних ссылок (таблица замен в конфиге);
- в чек-листе убирается последний столбец «кейс» (там названия клиентов), сами правила не меняются;
- архив уроков (lessons-archive.md) в плагин не входит: в нём данные клиентов.

Запуск:
    python3 tools/sync-methodology.py            обновить копии
    python3 tools/sync-methodology.py --check    только проверить, что копии свежие (код выхода 1, если нет)
Путь и замены: tools/sync-config.local.json. Другой путь: переменная FUSION_PROMPT_ARCHITECT_SRC.
После обновления скрипт сам ищет утечки (клиентские имена из tools/leak-names.local.txt, почты, ключи,
пути, идентификаторы) и останавливается, если нашёл.
"""

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "tools", "sync-config.local.json")
TARGET = os.path.join(ROOT, "plugins", "fusion-integrator", "skills", "prompt-architect", "references")
LOCAL_NAMES = os.path.join(ROOT, "tools", "leak-names.local.txt")
MARKER = ("<!-- Копия единого источника методологии Fusion. Руками не править: "
          "python3 tools/sync-methodology.py. Источник и контрольные суммы: SOURCE.json -->\n")

def load_config():
    """Путь к источнику и таблица замен лежат в tools/sync-config.local.json (в git не попадает):
    в них внутренние имена. Образец: tools/sync-config.example.json."""
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit("Нет tools/sync-config.local.json. Скопируй sync-config.example.json и заполни.")
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


GENERIC_LEAKS = [
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "почта"),
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "идентификатор UUID"),
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP-адрес"),
    (r"eyJ[A-Za-z0-9_-]{10,}", "токен JWT"),
    (r"\b(sk|pk|ghp|xox[bp])[-_][A-Za-z0-9_-]{16,}", "ключ"),
    (r"/Users/|/home/\w+|\\Users\\", "личный путь"),
    ("Айг" + "ерим", "запрещённое имя"),
]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def transform_core(text, replacements):
    for old, new in replacements:
        if old not in text:
            raise SystemExit(f"Источник изменился: не нашёл фразу для замены:\n  {old}\n"
                             "Обнови таблицу замен в tools/sync-config.local.json.")
        text = text.replace(old, new)
    head, separator, body = text.partition("\n---\n")
    return head + separator + MARKER + body if separator else MARKER + text


def transform_checklist(text):
    lines = []
    for line in text.split("\n"):
        if line.startswith("Формат: №"):
            line = "Формат: № | правило | как проверить."
        elif re.match(r"^\d+ \| ", line):
            parts = line.split(" | ")
            if len(parts) != 4:
                raise SystemExit(f"Строка чек-листа не из 4 столбцов, проверь источник:\n  {line}")
            line = " | ".join(parts[:3])
        lines.append(line)
    text = "\n".join(lines)
    head, separator, body = text.partition("\n---\n")
    return head + separator + MARKER + body if separator else MARKER + text


def scan_leaks(name, text):
    problems = []
    for pattern, label in GENERIC_LEAKS:
        for match in re.finditer(pattern, text):
            problems.append(f"{name}: {label}: {match.group(0)[:60]}")
    if os.path.exists(LOCAL_NAMES):
        with open(LOCAL_NAMES, encoding="utf-8") as handle:
            for word in (w.strip() for w in handle if w.strip() and not w.startswith("#")):
                if re.search(re.escape(word), text, re.I):
                    problems.append(f"{name}: клиентское имя из локального списка: {word}")
    return problems


def build(source_dir, replacements):
    outputs = {}
    sources = {}
    for name, transform in (("architect-core.md", lambda t: transform_core(t, replacements)),
                            ("lessons-checklist.md", transform_checklist)):
        path = os.path.join(source_dir, name)
        if not os.path.exists(path):
            raise SystemExit(f"Нет файла источника: {path}")
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
        outputs[name] = transform(raw)
        sources[name] = {"source_sha256": sha(raw), "plugin_copy_sha256": sha(outputs[name])}
    leaks = [problem for name, text in outputs.items() for problem in scan_leaks(name, text)]
    if leaks:
        raise SystemExit("Найдены утечки, копия не записана:\n  " + "\n  ".join(leaks))
    outputs["SOURCE.json"] = json.dumps(
        {"about": "Копия получена скриптом tools/sync-methodology.py из единого источника методологии Fusion.",
         "files": sources}, ensure_ascii=False, indent=1) + "\n"
    return outputs


def main(argv):
    config = load_config()
    source_dir = os.environ.get("FUSION_PROMPT_ARCHITECT_SRC", config["source_dir"])
    outputs = build(source_dir, config["replacements"])
    stale = []
    for name, text in outputs.items():
        path = os.path.join(TARGET, name)
        current = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if current != text:
            stale.append(name)
            if "--check" not in argv:
                os.makedirs(TARGET, exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
    if "--check" in argv:
        print("Копии свежие." if not stale else f"Устарели: {', '.join(stale)}. Запусти без --check.")
        return 1 if stale else 0
    print("Обновлено: " + ", ".join(stale) if stale else "Уже свежее, менять нечего.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
