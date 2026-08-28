#!/usr/bin/env python3
"""
Пушит в Keitaro реальный TikTok-расход за август для кампаний-баеров,
у которых часть трафика идёт с меткой "Keitaro" (без keyword/оффера).

БЕЗ разбивки по офферам — cost ставится на кампанию баера целиком
(only_campaign_uniques, без filters по keyword), по дням, т.к.
update_costs СТАВИТ сумму на диапазон дат, а не добавляет.

Источник дневных сумм — тот же, что подтверждён в чате:
BNS и VAD, 14-27 августа (данные с меткой Keitaro).

ВАЖНО: перед запуском впиши campaign_id для BNS и VAD ниже
(судя по скрину Кейтаро: BNS=10, VAD=5 — подтверди перед запуском).
"""

import os
import requests

KEITARO_BASE = "http://167.233.96.7"  # или https://trkads24.online
ADMIN_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY")  # взять из .env на сервере

BUYER_TO_CAMPAIGN_ID = {
    "BNS": 10,
    "VAD": 5,
}

# день -> сумма USD (посчитано из tt_links_2.xlsx, метка Keitaro)
DAILY_SPEND = {
    "BNS": {
        "2026-08-14": 2.35,
        "2026-08-15": 241.77,
        "2026-08-16": 138.57,
        "2026-08-17": 219.53,
        "2026-08-18": 282.12,
        "2026-08-19": 157.33,
        "2026-08-20": 213.89,
        "2026-08-21": 201.59,
        "2026-08-22": 180.39,
        "2026-08-24": 163.71,
        "2026-08-25": 368.97,
        "2026-08-26": 449.46,
        "2026-08-27": 429.33,
    },
    "VAD": {
        "2026-08-14": 370.41,
        "2026-08-15": 415.01,
        "2026-08-16": 293.20,
        "2026-08-17": 361.01,
        "2026-08-18": 273.81,
        "2026-08-19": 357.79,
        "2026-08-20": 413.79,
        "2026-08-21": 359.19,
        "2026-08-22": 336.13,
        "2026-08-24": 199.81,
        "2026-08-25": 469.51,
        "2026-08-26": 584.40,
        "2026-08-27": 381.82,
    },
}


def push_cost(campaign_id, day, cost):
    from datetime import datetime, timedelta

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
        # без "filters" -> cost ставится на всю кампанию, без разбивки по keyword
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
