#!/usr/bin/env python3
"""
verify_keitaro_tag_spend.py

Сверка: реальный TikTok-расход по кампаниям с тегом "Keitaro" за день
против того, что реально осело в Keitaro (keitaro_events, per keyword).

Не шлёт ничего никуда - только читает и сравнивает, чтобы можно было
быстро увидеть расхождения по дням.

Запуск:
  export $(cat /root/.keitaro_admin_api.env | xargs)
  python3 verify_keitaro_tag_spend.py [YYYY-MM-DD]   (по умолчанию - сегодня)
"""

import os
import re
import sys
import json
import subprocess
from datetime import datetime, timedelta

import requests

TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_ACCESS_TOKEN_BC2 = os.environ.get("TIKTOK_ACCESS_TOKEN_BC2", "")
BUSINESS_CENTER_IDS = ["7632400042631888913", "7410341052470607888"]
BC_TOKENS = {
    "7632400042631888913": TIKTOK_ACCESS_TOKEN,
    "7410341052470607888": TIKTOK_ACCESS_TOKEN_BC2,
}
ADVERTISER_TOKEN = {}
TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"

KEITARO_BASE_URL = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
KEITARO_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY", "")
KT_HEADERS = {"Api-Key": KEITARO_API_KEY, "Content-Type": "application/json"}

CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_KEITARO_PASSWORD", "1aaaf4c52ae84f9caea371ae66877b24")

BUYER_CAMPAIGN_IDS = {"BNS": 10, "VAD": 5, "KRL": 7}  # известные на сегодня; расширяется по мере надобности


def get_advertiser_ids():
    advertiser_ids = []
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
            for item in data.get("data", {}).get("list", []):
                adv_id = item.get("advertiser_id") or item.get("asset_id") or item.get("id")
                if adv_id:
                    advertiser_ids.append(adv_id)
                    ADVERTISER_TOKEN[adv_id] = token
            page_info = data.get("data", {}).get("page_info", {})
            if page >= page_info.get("total_page", 1):
                break
            page += 1
    return advertiser_ids


def get_campaign_spend(advertiser_id, date_str):
    url = f"{TIKTOK_API_BASE}/report/integrated/get/"
    params = {
        "advertiser_id": advertiser_id, "report_type": "BASIC",
        "dimensions": json.dumps(["campaign_id"]), "data_level": "AUCTION_CAMPAIGN",
        "start_date": date_str, "end_date": date_str,
        "metrics": json.dumps(["spend", "campaign_name"]), "page_size": 1000,
    }
    headers = {"Access-Token": ADVERTISER_TOKEN.get(advertiser_id, TIKTOK_ACCESS_TOKEN), "Content-Type": "application/json"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        return []
    return data.get("data", {}).get("list", [])


def is_keitaro_tag(name):
    parts = [p.strip() for p in name.split("|")]
    return len(parts) >= 3 and parts[2].strip().lower() == "keitaro"


def parse_buyer(name):
    parts = [p.strip() for p in name.split("|")]
    return parts[1] if len(parts) >= 2 else None


def tiktok_side_totals(date_str):
    """Реальный TikTok-расход по Keitaro-тег кампаниям, сгруппированный по баеру."""
    advertiser_ids = get_advertiser_ids()
    totals = {}
    for adv_id in advertiser_ids:
        rows = get_campaign_spend(adv_id, date_str)
        for row in rows:
            metrics = row.get("metrics", {})
            name = metrics.get("campaign_name", "")
            spend = float(metrics.get("spend", 0) or 0)
            if spend <= 0 or not is_keitaro_tag(name):
                continue
            buyer = parse_buyer(name) or "UNKNOWN"
            totals[buyer] = totals.get(buyer, 0) + spend
    return totals


def keitaro_side_total(campaign_id, date_str):
    """Реальный расход в Keitaro за день по кампании (FINAL, консистентно)."""
    query = (
        f"SELECT sum(cost) FROM keitaro.keitaro_events FINAL "
        f"WHERE campaign_id = {campaign_id} AND toDate(datetime) = '{date_str}'"
    )
    try:
        result = subprocess.run(
            ["podman", "exec", "-i", "clickhouse", "clickhouse-client",
             "--user", "keitaro", "--password", CLICKHOUSE_PASSWORD, "--query", query],
            capture_output=True, text=True, timeout=30,
        )
        val = result.stdout.strip()
        return float(val) if val else 0.0
    except Exception as e:
        print(f"  [ClickHouse error] {e}")
        return None


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"=== Сверка за {date_str} ===\n")

    tt_totals = tiktok_side_totals(date_str)

    print(f"{'Баер':10s} {'TikTok (Keitaro-тег)':>22s} {'Keitaro (кампания целиком)':>28s} {'Разница':>12s}")
    for buyer, tt_spend in tt_totals.items():
        campaign_id = BUYER_CAMPAIGN_IDS.get(buyer)
        if not campaign_id:
            print(f"{buyer:10s} {tt_spend:22.2f} {'нет campaign_id':>28s}")
            continue
        kt_spend = keitaro_side_total(campaign_id, date_str)
        if kt_spend is None:
            print(f"{buyer:10s} {tt_spend:22.2f} {'ошибка чтения':>28s}")
            continue
        diff = kt_spend - tt_spend
        print(f"{buyer:10s} {tt_spend:22.2f} {kt_spend:28.2f} {diff:12.2f}")

    print("\nПримечание: колонка Keitaro - это ВЕСЬ расход кампании за день")
    print("(обычные ссылки + Keitaro-тег вместе), не только тег - используйте")
    print("как индикатор, что цифра не нулевая/не отстаёт сильно, не как")
    print("точное совпадение 1-в-1.")


if __name__ == "__main__":
    main()
