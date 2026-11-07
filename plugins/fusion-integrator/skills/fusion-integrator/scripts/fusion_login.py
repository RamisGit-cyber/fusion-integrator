#!/usr/bin/env python3
"""Вход в кабинет клиента Fusion. Пароль вводится ТОЛЬКО скрытым вводом с клавиатуры.

    fusion-login <имя-клиента>            войти (спросит почту и пароль)
    fusion-login <имя-клиента> --status   показать, жива ли сессия
    fusion-login <имя-клиента> --logout   удалить сохранённую сессию

Что делает: логинится в API fusion-ai.app, сохраняет только токены сессии в
~/.config/fusion-integrator/<имя>.json (права 600) и сразу забывает пароль.
Пароль и токены не печатаются и в логи не пишутся.

Запускать нужно в обычном окне Терминала, не внутри чата Claude: скрипт читает
ввод напрямую с клавиатуры (/dev/tty) и откажется работать, если клавиатуры нет.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as lib  # noqa: E402


def read_hidden(prompt, echo):
    """Читает строку с клавиатуры. Без клавиатуры (канал, чат, скрипт) отказывается."""
    if os.name == "nt":
        import getpass
        return getpass.getpass(prompt) if not echo else input(prompt)
    try:
        import termios
        if not (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()):
            raise OSError("ввод или вывод перенаправлен")
        tty_in = open("/dev/tty", "r")
        tty_out = open("/dev/tty", "w")
        if os.tcgetpgrp(tty_in.fileno()) != os.getpgrp():
            raise OSError("процесс не на переднем плане терминала")
    except (OSError, ImportError):
        raise lib.FusionError(
            "Нет настоящего терминала с клавиатурой (запуск из чата, канала или фона). Запусти fusion-login "
            "в отдельном окне Терминала, а не внутри Claude: пароль не должен проходить через чат."
        ) from None
    with tty_in, tty_out:
        tty_out.write(prompt)
        tty_out.flush()
        descriptor = tty_in.fileno()
        saved = termios.tcgetattr(descriptor)
        try:
            if not echo:
                changed = termios.tcgetattr(descriptor)
                changed[3] &= ~termios.ECHO
                termios.tcsetattr(descriptor, termios.TCSADRAIN, changed)
            line = tty_in.readline()
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)
            if not echo:
                tty_out.write("\n")
                tty_out.flush()
    return line.rstrip("\r\n")


def do_login(client):
    email = read_hidden("Почта от кабинета клиента: ", echo=True).strip()
    password = read_hidden("Пароль (символы не появятся, это нормально): ", echo=False)
    if not email or not password:
        raise lib.FusionError("Почта или пароль пустые. Отмена.")
    try:
        tokens = lib.login_request(email, password)
    finally:
        password = None  # пароль дальше не нужен ни одной строке кода
    if "access_token" not in tokens:
        raise lib.FusionError("Платформа не вернула токен. Возможно, кабинет создан через вход Google: у него нет пароля.")
    lib.save_session(client, tokens)
    path = lib.session_path(client)
    print(f"\nГотово: вход выполнен как {lib.mask_email(email)}.")
    print(f"Сохранён только токен сессии: {path} (права 600). Пароль нигде не записан.")
    print_status(client)


def print_status(client):
    session = lib.load_session(client)
    left = lib.seconds_left(session["access_token"])
    refresh = lib.seconds_left(session.get("refresh_token", ""))
    print(f"Клиент «{client}»: рабочий ключ {'жив ещё ' + str(left // 60) + ' мин' if left > 0 else 'ПРОСРОЧЕН'}; "
          f"обновляющий {'жив ещё ' + str(refresh // 3600) + ' ч' if refresh > 0 else 'не действует'}.")
    api = lib.Api(client)
    me = api.get("/users/me")
    agents = api.get("/agents/my?limit=100")
    print(f"Кабинет открывается, роль: {me.get('role', me.get('role_name', '?'))}, агентов: {len(agents)}.")


def do_logout(client):
    path = lib.session_path(client)
    if os.path.exists(path):
        os.remove(path)
        print(f"Сессия клиента «{client}» удалена.")
    else:
        print("Сохранённой сессии нет.")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    client = lib.validate_client(argv[0])
    if "--status" in argv[1:]:
        print_status(client)
    elif "--logout" in argv[1:]:
        do_logout(client)
    else:
        do_login(client)
    return 0


if __name__ == "__main__":
    lib.run_main(lambda: main(sys.argv[1:]))
