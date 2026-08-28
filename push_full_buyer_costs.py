#!/usr/bin/env python3
"""
Заливает в Keitaro РЕАЛЬНЫЙ ПОЛНЫЙ TikTok-расход по баеру (BNS/VAD) за
14-27 августа - сумма по ВСЕМ кампаниям баера (обычные ссылки + тег
Keitaro вместе), по дням, на уровень кампании целиком (без keyword-фильтра).

Это финальная перезапись поверх всех прошлых конфликтующих пушей
(auto_spend_sync.py по keyword + предыдущий push_keitaro_tag_costs.py
только по тегу) - призвана дать правильный ИТОГ в дашборде.

ВАЖНО: перед запуском останови cron auto_spend_sync.py, иначе он
продолжит писать поверх своими отдельными keyword-суммами и общая
цифра снова разъедется.
"""

import os
import requests
from datetime import datetime, timedelta

KEITARO_BASE = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
ADMIN_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY")

BUYER_TO_CAMPAIGN_ID = {
    "BNS": 10,
    "VAD": 5,
}

# день -> сумма USD, ВЕСЬ реальный расход баера (все ссылки), из tt_links_2.xlsx
DAILY_SPEND = {
    "BNS": {
        "2026-08-14": 594.25,
        "2026-08-15": 947.91,
        "2026-08-16": 404.19,
        "2026-08-17": 727.08,
        "2026-08-18": 684.61,
        "2026-08-19": 586.25,
        "2026-08-20": 795.78,
        "2026-08-21": 663.89,
        "2026-08-22": 465.80,
        "2026-08-24": 420.62,
        "2026-08-25": 589.34,
        "2026-08-26": 677.30,
        "2026-08-27": 774.34,
    },
    "VAD": {
        "2026-08-14": 710.01,
        "2026-08-15": 966.06,
        "2026-08-16": 858.60,
        "2026-08-17": 744.53,
        "2026-08-18": 729.38,
        "2026-08-19": 789.09,
        "2026-08-20": 825.03,
        "2026-08-21": 740.43,
        "2026-08-22": 927.04,
        "2026-08-24": 728.22,
        "2026-08-25": 881.35,
        "2026-08-26": 900.11,
        "2026-08-27": 626.82,
    },
}


def push_cost(campaign_id, day, cost):
    start = day + " 00:00:00"
    end_dt = datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%d") + " 00:00:00"

    url = f"{KEITARO_BASE}/admin_api/v1/campaigns/{campaign_id}/update_costs"
    headers = {"Api-Key": ADMIN_API_KEY, "Content-Type": "application/json"}
    body = {
        "start_date": start,
        "end_date": end,
        "cost": cost,
        "currency": "USD",
        "timezone": "Europe/Minsk",
        "only_campaign_uniques": True,
        # без "filters" -> покрывает ВСЕ клики кампании этого дня
    }
    r = requests.post(url, headers=headers, json=body)
    ok = r.status_code == 200
    print(f"{'OK ' if ok else 'FAIL'} campaign={campaign_id} {day} cost={cost}  -> {r.status_code} {r.text[:200]}")
    return ok


def main():
    if not ADMIN_API_KEY:
        print("[ERROR] задай переменную окружения KEITARO_ADMIN_API_KEY")
        return

    for buyer, days in DAILY_SPEND.items():
        campaign_id = BUYER_TO_CAMPAIGN_ID.get(buyer)
        if not campaign_id:
            print(f"[SKIP] нет campaign_id для {buyer}")
            continue
        print(f"\n=== {buyer} (campaign_id={campaign_id}) ===")
        for day, cost in sorted(days.items()):
            push_cost(campaign_id, day, cost)


if __name__ == "__main__":
    main()
