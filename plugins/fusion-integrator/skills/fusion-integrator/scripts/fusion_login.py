#!/usr/bin/env python3
"""Вход в кабинет клиента Fusion. Пароль вводится ТОЛЬКО скрыто: в форме в браузере или в Терминале.

    fusion-login <имя-клиента>            войти (без клавиатуры открывается форма в браузере, в Терминале спросит там)
    fusion-login <имя-клиента> --form     войти через скрытую форму в браузере (на этом компьютере, адрес 127.0.0.1)
    fusion-login <имя-клиента> --terminal войти скрытым вводом в Терминале (запасной путь)
    fusion-login <имя-клиента> --status   показать, жива ли сессия
    fusion-login <имя-клиента> --logout   удалить сохранённую сессию

Что делает: логинится в API fusion-ai.app, сохраняет только токены сессии в
~/.config/fusion-integrator/<имя>.json (права 600) и сразу забывает пароль.
Пароль и токены не печатаются и в логи не пишутся.

Форма: команда ждёт, пока человек войдёт (до 10 минут), потом сама закрывается.
Терминальный путь читает ввод напрямую с клавиатуры (/dev/tty) и без неё откажется работать.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as lib  # noqa: E402
import fusion_loginform as loginform  # noqa: E402


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
            "Нет настоящего терминала с клавиатурой (запуск из чата, канала или фона). Используй форму "
            "в браузере (--form) или запусти fusion-login --terminal в окне Терминала: пароль не должен проходить через чат."
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


def has_keyboard():
    return sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()


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
    flags = argv[1:]
    if "--status" in flags:
        print_status(client)
    elif "--logout" in flags:
        do_logout(client)
    elif "--form" in flags or ("--terminal" not in flags and not has_keyboard()):
        loginform.run_form_login(client)
    else:
        do_login(client)
    return 0


if __name__ == "__main__":
    lib.run_main(lambda: main(sys.argv[1:]))
