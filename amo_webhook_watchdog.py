#!/usr/bin/env python3
"""
Watchdog для вебхука AmoCRM -> Keitaro (amo-keitaro).
AmoCRM периодически сам отключает вебхук после невалидных ответов
(наблюдается не только у нашего вебхука, но и у других интеграций на
этом аккаунте — судя по всему, это общая агрессивная политика AmoCRM,
а не баг именно нашего сервиса).

Идея: вместо того чтобы гоняться за первопричиной каждого отключения,
просто периодически (cron, раз в 5-10 минут) заново подписываемся на
тот же вебхук с теми же событиями. Если он активен — переподписка
безвредна. Если отключён — переподписка его реактивирует, и простой
сокращается с "часов до ручного обнаружения" до "нескольких минут".

ТРЕБУЕТСЯ ЗАПОЛНИТЬ:
- AMO_BASE_URL   — поддомен вашего AmoCRM, например https://youraccount.amocrm.ru
- ACCESS_TOKEN   — актуальный access_token интеграции (тот же, что использует amo_to_keitaro.py)
- WEBHOOK_URL    — адрес нашего вебхука
- EVENTS         — список событий, на которые подписан вебхук (как на скриншоте)
"""

import requests
import sys

AMO_BASE_URL = "https://daangrah000.amocrm.ru"
ACCESS_TOKEN = "ВСТАВЬТЕ_ТОКЕН_ИЗ_/root/fetch_all.py_(переменная AMO_TOKEN)"             # TODO: тот же токен, что в amo_to_keitaro.py
WEBHOOK_URL  = "http://167.233.96.7:8002/amo-webhook"

EVENTS = [
    "add_lead",
    "update_lead",
    "status_lead",  # "Сделка добавлена / изменена / статус изменена" — уточнить точные коды ниже
]


def resubscribe():
    resp = requests.post(
        f"{AMO_BASE_URL}/api/v4/webhooks",
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "destination": WEBHOOK_URL,
            "settings": EVENTS,
        },
        timeout=15,
    )

    print(f"[{resp.status_code}] {resp.text[:300]}")

    if resp.status_code not in (200, 201):
        # Не считаем это провалом скрипта — просто логируем, чтобы
        # не заспамить cron ошибками при штатном "уже подписан" ответе.
        print("Переподписка вернула не 200/201 — проверить вручную, если повторяется.", file=sys.stderr)


if __name__ == "__main__":
    resubscribe()
