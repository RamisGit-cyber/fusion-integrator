# Команды и что они вызывают на платформе

Запуск: `python3 "${CLAUDE_SKILL_DIR}/scripts/fusion.py" --client <имя> <группа> <команда> …` (или переменная `FUSION_CLIENT=<имя>`). Только стандартная библиотека Python 3.9+, каждый запрос с таймаутом (30 с, загрузка файла 120 с). Адрес API зашит в код: `https://fusion-ai.app/api`. Переходы по редиректам запрещены.

| Команда | Эндпоинт платформы | Заметки |
|---|---|---|
| вход (`fusion_login.py`) | `POST /auth/login` (форма `username`, `password`), `POST /auth/refresh` | сохраняется только токен, права 600 |
| `account` | `GET /subscriptions/my/messages` | лимит, использовано, осталось |
| `agents list` | `GET /agents/my` | |
| `agents show` | `GET /agents/{id}` | |
| `agents create` | `POST /agents`, затем `PATCH /agents/{id}` | температура 0,2, часовой пояс, голос и фото включены; каналы не подключены |
| `agents clone` | `POST /agents/{id}/clone` | перед этим бэкап |
| `agents set` | `PATCH /agents/{id}` | температура, задержка ответа, часовой пояс, голос, фото |
| `agents active on/off` | `PATCH /agents/{id}` (`is_active`) | включать после регресса; для `on` нужен `--confirm-live` (Claude ставит его сам) |
| `agents delete` | `DELETE /agents/{id}` | только имена, начинающиеся на «ТЕСТ интегратор-плагин» |
| `backup` / `backups` / `restore` | чтение: агент, дожимки, поля заявки, документы; откат: `PATCH /agents/{id}`, `PUT /agents/{id}/follow-ups`, `PATCH /threads/configure/{user_id}/{id}` | файлы в `~/.config/fusion-integrator/backups/<клиент>/<агент>/` |
| `prompt pull` | `GET /agents/{id}` | |
| `prompt push` | `PATCH /agents/{id}` (`system_prompt`) | линтер, бэкап, побайтная сверка, авто-откат при расхождении |
| `prompt lint` | локально | не требует входа |
| `kb list` | `GET /documents/?agent_id=` | |
| `kb upload` | `POST /documents/files/{id}` (multipart, поле `files`), опрос статуса | форматы md, txt, pdf, docx, xlsx, до 20 МБ; `--replace-id` удаляет старый документ после успешной загрузки нового |
| `kb delete` | `DELETE /documents/{doc}?agent_id=` | |
| `fields show` / `fields set` | `POST /threads/configure?agent_id=` (получить или создать), `PATCH /threads/configure/{user_id}/{agent_id}` | «Телефон» обязателен |
| `followups show` / `set` | `GET`, `PUT /agents/{id}/follow-ups` | |
| `notify link` | `POST /telegram-notifications/agents/{id}/links/group` | |
| `notify confirm` | `POST /telegram-notifications/agents/{id}/groups/confirm` (`code`) | |
| `notify status` | `GET …/recipients`, `GET …/settings` | |
| `channels` | `GET /messenger-connections/{канал}/{id}/connection` | только чтение |
| `chat` | `POST /agents/{id}/test-chat/share-link`, `POST` и `GET /public/agent-test-chat/{token}/messages`, `DELETE …/share-link` | лимит `--max` (по умолчанию 40, потолок 300), `--confirm-spend` обязателен (Claude ставит сам), суточный предел 500 сообщений на клиента (`FUSION_DAILY_CHAT_LIMIT`) |

Коды выхода: 0 — успех, 1 — понятная ошибка (текст без секретов), остальное — неожиданный сбой.

Бэкап содержит промпт клиента, поэтому лежит в личной папке пользователя (права 600), а не в проекте. В git эти файлы не попадают.

Чего плагин НЕ делает: не подключает каналы (QR, вход Meta), не включает CRM-интеграции, не меняет тарифы и оплату, не читает переписки клиентов, не рассылает сообщения.
