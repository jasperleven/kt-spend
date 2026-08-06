#!/usr/bin/env python3
"""
auto_spend_sync.py

Полностью автоматический пайплайн:
  TikTok Business API (сам находит все рекламные кабинеты в BC)
    -> тянет сегодняшний расход по кампаниям
    -> парсит название кампании "Оффер | БАЕР | ссылка | дата"
    -> сопоставляет Оффер -> keyword и БАЕР -> Keitaro campaign_id
    -> шлёт update_costs в Keitaro

Запускать через cron, например каждый час:
  0 * * * * /usr/bin/python3 /root/auto_spend_sync.py >> /root/auto_spend_sync.log 2>&1

Ничего руками запускать не нужно после установки в cron.
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timedelta

# ==== Настройки ====

TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")  # выдашь и положишь в env
TIKTOK_APP_ID = os.environ.get("TIKTOK_APP_ID", "")
TIKTOK_SECRET = os.environ.get("TIKTOK_SECRET", "")

BUSINESS_CENTER_IDS = [
    "7632400042631888913",  # BC1
    "7410341052470607888",  # BC2 (ООО «Артагед»)
]

KEITARO_BASE_URL = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
KEITARO_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY", "")

TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"

# БАЕР -> Keitaro campaign_id. Добавлять строку при онбординге нового баера.
BUYER_TO_CAMPAIGN_ID = {
    "KRL": 7,
    "KRL Y": 12,
    "ABB": 8,
    "VAD": 5,
    "BNS": 10,
    "VLD": 13,
    "ART": 9,
}

# Название оффера больше не мапим руками — keyword достаём напрямую из
# трекинговой ссылки объявления (там уже есть ?keyword=xxx, т.к. так строятся все ссылки).

TT_HEADERS = {"Access-Token": TIKTOK_ACCESS_TOKEN, "Content-Type": "application/json"}
KT_HEADERS = {"Api-Key": KEITARO_API_KEY, "Content-Type": "application/json"}

BUYER_TO_CAMPAIGN_ID_LOWER = {k.lower(): v for k, v in BUYER_TO_CAMPAIGN_ID.items()}


def get_advertiser_ids():
    """Автоматически находит все рекламные кабинеты во всех Business Center."""
    advertiser_ids = []
    for bc_id in BUSINESS_CENTER_IDS:
        page = 1
        while True:
            url = f"{TIKTOK_API_BASE}/bc/asset/get/"
            params = {"bc_id": bc_id, "asset_type": "ADVERTISER", "page": page, "page_size": 50}
            r = requests.get(url, headers=TT_HEADERS, params=params, timeout=30)
            data = r.json()
            if data.get("code") != 0:
                print(f"ОШИБКА получения кабинетов для BC {bc_id}: {data}")
                break
            items = data.get("data", {}).get("list", [])
            for item in items:
                adv_id = item.get("advertiser_id") or item.get("asset_id") or item.get("id")
                if adv_id:
                    advertiser_ids.append(adv_id)
            page_info = data.get("data", {}).get("page_info", {})
            total_page = page_info.get("total_page", 1)
            if page >= total_page:
                break
            page += 1
    print(f"Найдено кабинетов: {len(advertiser_ids)}")
    return advertiser_ids


def get_today_campaign_spend(advertiser_id, date_str):
    """Тянет расход по кампаниям за день. Название кампании уже содержит ссылку
    с keyword в пути (напр. 'Оффер | БАЕР | domain/keyword | cab...')."""
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
    r = requests.get(url, headers=TT_HEADERS, params=params, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        print(f"ОШИБКА получения расхода для кабинета {advertiser_id}: {data}")
        return []
    return data.get("data", {}).get("list", [])


def extract_keyword_from_campaign_name(name):
    """'Оффер | БАЕР | domain/keyword | ...' -> keyword (последний сегмент пути в 3-й части)."""
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 3:
        return None
    link_part = parts[2]
    # уберём протокол если есть, возьмём путь после домена
    link_part = re.sub(r"^https?://", "", link_part)
    # уберём query string если есть
    link_part = link_part.split("?")[0]
    segments = [s for s in link_part.strip("/").split("/") if s]
    if len(segments) < 2:
        return None  # это просто домен без пути, или "cab N" без URL
    # segments[0] — домен, segments[1] — первый сегмент пути = keyword
    # (напр. tech-house.of.by/holodilniki/ucenka -> keyword = holodilniki)
    keyword = segments[1]
    return keyword


def parse_campaign_name(name):
    """'Оффер | БАЕР | ссылка | дата' -> (offer, buyer) или (None, None)."""
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


def send_update_costs_batch(campaign_id, keyword_costs, date_str):
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)
    end_str = end_dt.strftime("%Y-%m-%d")

    for keyword, cost in keyword_costs.items():
        payload = {
            "start_date": date_str,
            "end_date": end_str,
            "cost": round(cost, 2),
            "currency": "USD",
            "timezone": "Europe/Moscow",
            "only_campaign_uniques": False,
            "filters": {"keyword": keyword},
        }
        url = f"{KEITARO_BASE_URL}/admin_api/v1/campaigns/{campaign_id}/update_costs"
        r = requests.post(url, headers=KT_HEADERS, json=payload, timeout=30)
        print(f"  campaign {campaign_id} / {keyword} = {cost:.2f} USD -> HTTP {r.status_code} {r.text[:200]}")


def main():
    if not TIKTOK_ACCESS_TOKEN:
        sys.exit("ERROR: TIKTOK_ACCESS_TOKEN не задан (переменная окружения)")
    if not KEITARO_API_KEY:
        sys.exit("ERROR: KEITARO_ADMIN_API_KEY не задан (переменная окружения)")

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== Синхронизация расхода за {today} ===")

    advertiser_ids = get_advertiser_ids()

    # buyer -> keyword -> сумма
    totals = {}
    unmapped_offers = set()
    unmapped_buyers = set()

    for adv_id in advertiser_ids:
        rows = get_today_campaign_spend(adv_id, today)
        for row in rows:
            metrics = row.get("metrics", {})
            campaign_name = metrics.get("campaign_name", "")
            spend = float(metrics.get("spend", 0) or 0)
            if spend <= 0:
                continue

            _, buyer = parse_campaign_name(campaign_name)
            if not buyer:
                continue

            campaign_id = BUYER_TO_CAMPAIGN_ID_LOWER.get(buyer.lower())
            if not campaign_id:
                unmapped_buyers.add(buyer)
                continue

            keyword = extract_keyword_from_campaign_name(campaign_name)
            if not keyword:
                unmapped_offers.add(campaign_name)
                continue

            totals.setdefault(buyer, {}).setdefault(keyword, 0)
            totals[buyer][keyword] += spend

    print(f"\nСобрано расхода по {len(totals)} баерам")
    for buyer, keywords in totals.items():
        campaign_id = BUYER_TO_CAMPAIGN_ID_LOWER[buyer.lower()]
        print(f"\n--- {buyer} (campaign_id={campaign_id}) ---")
        send_update_costs_batch(campaign_id, keywords, today)

    if unmapped_offers:
        print(f"\n!!! Неизвестные офферы (добавь в OFFER_TO_KEYWORD): {sorted(unmapped_offers)}")
    if unmapped_buyers:
        print(f"!!! Неизвестные баеры (добавь в BUYER_TO_CAMPAIGN_ID): {sorted(unmapped_buyers)}")

    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
