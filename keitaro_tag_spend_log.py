#!/usr/bin/env python3
"""
keitaro_tag_spend_log.py

Отдельно (в обход update_costs, который не применяется в Keitaro из-за
бага на их стороне) собирает и копит расход по кампаниям, где в 3-м
сегменте названия стоит просто "Keitaro" (без ссылки/keyword) -
то есть тот трафик, который физически НЕ попадает в auto_spend_sync.py.

Просто дописывает построчно в CSV на сервере каждый час (через тот же
cron). Ничего никуда в Keitaro не шлёт - только копит для ручного
контроля/сверки, раз штатный API не работает для этого случая.

CSV: /root/keitaro_tag_spend.csv
Колонки: date,buyer,spend_usd

Запуск (добавить в тот же час, что и auto_spend_sync.py, или отдельным
cron-заданием):
  python3 keitaro_tag_spend_log.py
"""

import os
import csv
import json
from datetime import datetime

import requests

TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_MARKETING_TOKEN") or os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_ACCESS_TOKEN_BC2 = os.environ.get("TIKTOK_MARKETING_TOKEN_NASTYA") or os.environ.get("TIKTOK_ACCESS_TOKEN_BC2", "")

BUSINESS_CENTER_IDS = [
    "7632400042631888913",
    "7410341052470607888",
]
BC_TOKENS = {
    "7632400042631888913": TIKTOK_ACCESS_TOKEN,
    "7410341052470607888": TIKTOK_ACCESS_TOKEN_BC2,
}
ADVERTISER_TOKEN = {}

TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
OUTPUT_CSV = "/root/keitaro_tag_spend.csv"

KEITARO_BASE_URL = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
KEITARO_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY", "")
BUYER_CAMPAIGN_IDS = {"BNS": 10, "VAD": 5, "KRL": 7}


def update_campaign_notes(campaign_id, text):
    """Перезаписывает поле 'Заметки' (notes) кампании через Admin API -
    это обычное текстовое поле, не связанное с кликами, поэтому пишем
    туда точное число напрямую, без всякого деления по кликам."""
    url = f"{KEITARO_BASE_URL}/admin_api/v1/campaigns/{campaign_id}"
    headers = {"Api-Key": KEITARO_API_KEY, "Content-Type": "application/json"}
    r = requests.put(url, headers=headers, json={"notes": text}, timeout=30)
    print(f"  campaign {campaign_id} notes -> HTTP {r.status_code} {r.text[:200]}")


def get_advertiser_ids():
    """Возвращает уникальные advertiser_id (без дублей - один и тот же
    кабинет может быть виден одновременно в нескольких Business Center,
    иначе расход по нему задвоится)."""
    adv_to_token = {}
    for bc_id in BUSINESS_CENTER_IDS:
        token = BC_TOKENS.get(bc_id, TIKTOK_ACCESS_TOKEN)
        headers = {"Access-Token": token, "Content-Type": "application/json"}
        page = 1
        while True:
            url = f"{TIKTOK_API_BASE}/bc/asset/get/"
            params = {"bc_id": bc_id, "asset_type": "ADVERTISER", "page": page, "page_size": 50}
            r = requests.get(url, headers=headers, params=params, timeout=30)
            data = r.json()
            if data.get("code") != 0:
                break
            items = data.get("data", {}).get("list", [])
            for item in items:
                adv_id = item.get("advertiser_id") or item.get("asset_id") or item.get("id")
                if adv_id and adv_id not in adv_to_token:
                    adv_to_token[adv_id] = token
            page_info = data.get("data", {}).get("page_info", {})
            if page >= page_info.get("total_page", 1):
                break
            page += 1
    ADVERTISER_TOKEN.update(adv_to_token)
    return list(adv_to_token.keys())


def get_campaign_spend(advertiser_id, date_str, max_retries=3):
    url = f"{TIKTOK_API_BASE}/report/integrated/get/"
    params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
        "dimensions": json.dumps(["campaign_id"]),
        "data_level": "AUCTION_CAMPAIGN",
        "start_date": date_str,
        "end_date": date_str,
        "metrics": json.dumps(["spend", "campaign_name"]),
        "page_size": 1000,
    }
    headers = {"Access-Token": ADVERTISER_TOKEN.get(advertiser_id, TIKTOK_ACCESS_TOKEN), "Content-Type": "application/json"}
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            data = r.json()
            if data.get("code") != 0:
                last_error = f"API code {data.get('code')}: {data.get('message')}"
                continue  # retry
            return data.get("data", {}).get("list", [])
        except Exception as e:
            last_error = str(e)
            continue  # retry
    print(f"  [ОШИБКА] advertiser_id={advertiser_id}, date={date_str}: {last_error} - ПРОПУЩЕН после {max_retries} попыток!")
    return None  # None = точно не удалось (не путать с [] = удалось, но нет данных)


def is_keitaro_tag_campaign(name):
    """3-й сегмент = именно 'Keitaro' (без ссылки), не путать с кампаниями,
    где ссылка есть и Keitaro просто дописан отдельным полем."""
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 3:
        return False
    return parts[2].strip().lower() == "keitaro"


def parse_buyer(name):
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 2:
        return None
    return parts[1]


def main():
    # По умолчанию - вчерашний день (уже полностью закрытый, TikTok-цифры
    # финальные). Можно передать конкретную дату параметром для бэкфила.
    import sys
    from datetime import timedelta
    if len(sys.argv) > 1:
        today = sys.argv[1]
    else:
        today = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    advertiser_ids = get_advertiser_ids()
    print(f"Найдено кабинетов: {len(advertiser_ids)}")

    totals = {}
    failed_advertisers = []
    for adv_id in advertiser_ids:
        rows = get_campaign_spend(adv_id, today)
        if rows is None:
            failed_advertisers.append(adv_id)
            continue
        for row in rows:
            metrics = row.get("metrics", {})
            name = metrics.get("campaign_name", "")
            spend = float(metrics.get("spend", 0) or 0)
            if spend <= 0:
                continue
            if not is_keitaro_tag_campaign(name):
                continue
            buyer = parse_buyer(name) or "UNKNOWN"
            totals[buyer] = totals.get(buyer, 0) + spend

    if failed_advertisers:
        print(f"\n!!! ВНИМАНИЕ: {len(failed_advertisers)} кабинетов не удалось опросить (данные могут быть занижены): {failed_advertisers}")
        print("!!! Рекомендуется перезапустить скрипт заново, чтобы получить полные данные.\n")

    file_exists = os.path.isfile(OUTPUT_CSV)
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "buyer", "spend_usd"])
        for buyer, spend in totals.items():
            writer.writerow([today, buyer, round(spend, 2)])

    print(f"=== {today} ===")
    for buyer, spend in totals.items():
        print(f"  {buyer}: {spend:.2f} USD")
    print(f"Записано в {OUTPUT_CSV}")

    # В "Заметки" пишем не только сегодняшний день, а последние 30 дней
    # из CSV - список по датам, чтобы можно было визуально прочитать
    # расход за любой период (неделю, месяц) прямо в Keitaro, без
    # интерактивного фильтра (notes - обычное текстовое поле).
    print("\nОбновление поля 'Заметки' в Keitaro (последние 30 дней):")
    history = {}  # buyer -> {date: spend}
    with open(OUTPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            history.setdefault(row["buyer"], {})[row["date"]] = float(row["spend_usd"])

    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    for buyer in history:
        campaign_id = BUYER_CAMPAIGN_IDS.get(buyer)
        if not campaign_id:
            continue
        days = sorted((d for d in history[buyer] if d >= cutoff), reverse=True)
        lines = [f"Реальный расход TikTok по тегу Keitaro (последние 30 дней):"]
        for d in days:
            lines.append(f"  {d}: {history[buyer][d]:.2f} USD")
        text = "\n".join(lines)
        update_campaign_notes(campaign_id, text)


if __name__ == "__main__":
    main()
