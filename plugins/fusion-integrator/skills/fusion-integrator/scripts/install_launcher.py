#!/usr/bin/env python3
"""Ставит команду fusion-login, чтобы человек мог войти в кабинет клиента из обычного Терминала.

Копирует скрипты в ~/.config/fusion-integrator/bin (чтобы путь не зависел от версии плагина)
и кладёт короткую команду fusion-login в ~/.local/bin (Mac, Linux) или в ту же папку bin (Windows).
Запускать после установки и после каждого обновления плагина.
"""

import os
import shlex
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ("fusion_lib.py", "fusion_login.py")
TARGET = os.path.join(os.path.expanduser("~"), ".config", "fusion-integrator", "bin")


def main():
    os.makedirs(TARGET, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(os.path.dirname(TARGET), 0o700)
        os.chmod(TARGET, 0o700)
    for name in FILES:
        target_file = os.path.join(TARGET, name)
        if os.path.exists(target_file):
            os.chmod(target_file, 0o644)
        shutil.copyfile(os.path.join(HERE, name), target_file)
        os.chmod(target_file, 0o444)
    login = os.path.join(TARGET, "fusion_login.py")
    if os.name == "nt":
        shim = os.path.join(TARGET, "fusion-login.cmd")
        with open(shim, "w", encoding="utf-8") as handle:
            handle.write(f'@echo off\r\npython -I "{login}" %*\r\n')
        print(f"Готово. Команда: {shim}\nЧтобы писать просто fusion-login, добавь папку {TARGET} в PATH.")
        return
    bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    shim = os.path.join(bin_dir, "fusion-login")
    if os.path.islink(shim):
        os.remove(shim)
    with open(shim, "w", encoding="utf-8") as handle:
        handle.write(f'#!/bin/sh\nexec python3 -I {shlex.quote(login)} "$@"\n')
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Готово. Команда: {shim}")
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Папки {bin_dir} нет в PATH. Запускай полным путём:\n  {shim} <имя-клиента>")


if __name__ == "__main__":
    sys.exit(main())
