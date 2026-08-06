#!/usr/bin/env python3
"""
keitaro_update_costs.py

Работа с Keitaro Admin API для кампании:
  --discover        показать потоки кампании и их sub_id_N -> значение "Ключевик",
                     чтобы понять, какой sub_id_N соответствует параметру Ключевик
  --test             dry-run: посчитать что будет отправлено, но НЕ отправлять
  --live             реально отправить update_costs

Использование:
  export $(cat /root/.keitaro_admin_api.env | xargs)
  python3 keitaro_update_costs.py --discover --campaign-id 7
  python3 keitaro_update_costs.py --test --campaign-id 7 --keyword tricikly --cost 12.34 --date 2026-08-06
  python3 keitaro_update_costs.py --live --campaign-id 7 --keyword tricikly --cost 12.34 --date 2026-08-06
"""

import os
import sys
import argparse
import json
from datetime import datetime, timedelta

import requests

BASE_URL = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY")

if not API_KEY:
    sys.exit("ERROR: переменная окружения KEITARO_ADMIN_API_KEY не установлена. "
              "export $(cat /root/.keitaro_admin_api.env | xargs)")

HEADERS = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
}


def get_campaign(campaign_id):
    url = f"{BASE_URL}/admin_api/v1/campaigns/{campaign_id}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def get_streams(campaign_id):
    # Потоки кампании обычно вложены в объект кампании; если нет — отдельный endpoint.
    campaign = get_campaign(campaign_id)
    if "streams" in campaign:
        return campaign["streams"]
    url = f"{BASE_URL}/admin_api/v1/campaigns/{campaign_id}/streams"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def discover(campaign_id):
    print(f"== Campaign {campaign_id}: получаем потоки ==")
    try:
        streams = get_streams(campaign_id)
    except requests.HTTPError as e:
        print(f"Не удалось получить потоки напрямую ({e}), пробуем альтернативный endpoint...")
        url = f"{BASE_URL}/admin_api/v1/streams?campaign_id={campaign_id}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        streams = r.json()

    if not streams:
        print("Потоки не найдены — проверь campaign_id.")
        return

    print(f"Найдено потоков: {len(streams)}\n")
    for s in streams:
        name = s.get("name", "?")
        stream_id = s.get("id")
        filters = s.get("filters") or s.get("action_filters") or []
        print(f"--- Поток: {name} (id={stream_id}) ---")
        if not filters:
            print("   (нет явных фильтров в этом объекте — проверь вручную в UI: "
                  "Обслуживание -> Поток -> Фильтр по sub_id_N)")
        for f in filters:
            print(f"   raw filter: {json.dumps(f, ensure_ascii=False)}")
        print()

    print(
        "Подсказка: зайди в UI на вкладку кампании 'Параметры' и посмотри, "
        "какому GET-параметру назначено имя 'Ключевик' (обычно keyword или utm_term), "
        "а также сверь это с sub_id_N, который использован в фильтре каждого потока "
        "(Условие -> Sub ID N = <значение ключевика>)."
    )


def update_costs(campaign_id, sub_id_field, keyword, cost, date_str, currency="USD",
                  timezone="Europe/Moscow", live=False):
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)
    payload = {
        "start_date": date_str,
        "end_date": end_dt.strftime("%Y-%m-%d"),
        "cost": cost,
        "currency": currency,
        "timezone": timezone,
        "only_campaign_uniques": False,
        "filters": {
            sub_id_field: keyword
        }
    }

    url = f"{BASE_URL}/admin_api/v1/campaigns/{campaign_id}/update_costs"

    print(f"{'[LIVE]' if live else '[DRY-RUN]'} POST {url}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not live:
        print("\n(dry-run — запрос не отправлен, добавь --live чтобы отправить по-настоящему)")
        return

    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    print(f"\nHTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    except ValueError:
        print(r.text)
    r.raise_for_status()


def update_costs_batch(campaign_id, sub_id_field, costs, date_str, currency="USD",
                        timezone="Europe/Moscow", live=False):
    """costs: dict keyword -> cost. Отправляет update_costs по очереди для каждого keyword."""
    results = {}
    for keyword, cost in costs.items():
        print(f"\n===== {keyword}: {cost} =====")
        try:
            update_costs(
                campaign_id=campaign_id,
                sub_id_field=sub_id_field,
                keyword=keyword,
                cost=cost,
                date_str=date_str,
                currency=currency,
                timezone=timezone,
                live=live,
            )
            results[keyword] = "OK"
        except Exception as e:
            print(f"ОШИБКА для {keyword}: {e}")
            results[keyword] = f"FAIL: {e}"

    print("\n===== ИТОГО =====")
    for k, v in results.items():
        print(f"{k}: {v}")
    return results


def main():
    parser = argparse.ArgumentParser(description="Keitaro Admin API update_costs helper")
    parser.add_argument("--campaign-id", type=int, required=True)
    parser.add_argument("--discover", action="store_true", help="показать потоки и их фильтры")
    parser.add_argument("--test", action="store_true", help="dry-run update_costs")
    parser.add_argument("--live", action="store_true", help="реально отправить update_costs")
    parser.add_argument("--sub-id-field", default="keyword",
                         help="имя поля фильтра потока (по discover это буквально 'keyword', не sub_id_N)")
    parser.add_argument("--keyword", help="значение ключевика (напр. tricikly) — для одиночного запуска")
    parser.add_argument("--cost", type=float, help="полная сумма расхода за день — для одиночного запуска")
    parser.add_argument("--costs-json",
                         help='JSON-строка или путь к .json файлу вида {"tricikly": 45.2, "conder": 12.0, ...} '
                              'для отправки сразу нескольких keyword одним запуском')
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                         help="дата в формате YYYY-MM-DD, по умолчанию сегодня")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--timezone", default="Europe/Moscow")

    args = parser.parse_args()

    if args.discover:
        discover(args.campaign_id)
        return

    if args.costs_json and (args.test or args.live):
        # Строка JSON или путь к файлу
        if os.path.isfile(args.costs_json):
            with open(args.costs_json, "r", encoding="utf-8") as f:
                costs = json.load(f)
        else:
            costs = json.loads(args.costs_json)
        update_costs_batch(
            campaign_id=args.campaign_id,
            sub_id_field=args.sub_id_field,
            costs=costs,
            date_str=args.date,
            currency=args.currency,
            timezone=args.timezone,
            live=args.live,
        )
        return

    if args.test or args.live:
        if not args.keyword or args.cost is None:
            sys.exit("Для --test/--live нужны --keyword и --cost (или --costs-json для batch-режима)")
        update_costs(
            campaign_id=args.campaign_id,
            sub_id_field=args.sub_id_field,
            keyword=args.keyword,
            cost=args.cost,
            date_str=args.date,
            currency=args.currency,
            timezone=args.timezone,
            live=args.live,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
