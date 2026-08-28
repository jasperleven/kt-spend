#!/usr/bin/env python3
"""
auto_spend_sync.py (v2 - без ручного словаря баеров)

Отличие от предыдущей версии: BUYER_TO_CAMPAIGN_ID больше не хардкодится
в коде. Вместо этого перед каждым синком скрипт запрашивает у Keitaro
Admin API список всех существующих кампаний (GET /admin_api/v1/campaigns)
и сам строит маппинг "имя кампании (баер) -> campaign_id".

Значит: если завтра появится новый баер - единственное действие
человека - создать для него кампанию в Keitaro через обычный UI (как
делалось раньше для ABB/VAD/BNS/VLD - домен, лендинги, потоки).
Как только кампания существует в Keitaro, следующий же прогон cron
(раз в час) её автоматически подхватит по совпадению имени. Никакой
правки скрипта/словаря/файла руками больше не требуется.

Если тег баера в TikTok не совпадает ни с одной кампанией в Keitaro -
он просто пропускается (не создаётся автоматически - см. предыдущее
обсуждение: создание кампании без реального домена/лендинга/вебхука
у баера бесполезно, т.к. клики некуда вести).
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timedelta

TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_ACCESS_TOKEN_BC2 = os.environ.get("TIKTOK_ACCESS_TOKEN_BC2", "")

BUSINESS_CENTER_IDS = [
    "7632400042631888913",  # BC1
    "7410341052470607888",  # BC2 (ООО «Артагед»)
]

BC_TOKENS = {
    "7632400042631888913": TIKTOK_ACCESS_TOKEN,
    "7410341052470607888": TIKTOK_ACCESS_TOKEN_BC2,
}
ADVERTISER_TOKEN = {}

KEITARO_BASE_URL = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
KEITARO_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY", "")

TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"

KT_HEADERS = {"Api-Key": KEITARO_API_KEY, "Content-Type": "application/json"}


def get_keitaro_buyer_campaigns():
    """Тянет список всех кампаний из Keitaro и строит {имя.lower(): campaign_id}.
    Это заменяет ручной словарь BUYER_TO_CAMPAIGN_ID - маппинг всегда
    актуален на момент запуска, без правки кода."""
    url = f"{KEITARO_BASE_URL}/admin_api/v1/campaigns"
    r = requests.get(url, headers=KT_HEADERS, timeout=30)
    r.raise_for_status()
    campaigns = r.json()
    if isinstance(campaigns, dict) and "data" in campaigns:
        campaigns = campaigns["data"]

    mapping = {}
    for c in campaigns:
        name = (c.get("name") or "").strip()
        cid = c.get("id")
        if name and cid:
            mapping[name.lower()] = cid
    print(f"Найдено кампаний в Keitaro: {len(mapping)}")
    return mapping


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
                print(f"ОШИБКА получения кабинетов для BC {bc_id}: {data}")
                break
            items = data.get("data", {}).get("list", [])
            for item in items:
                adv_id = item.get("advertiser_id") or item.get("asset_id") or item.get("id")
                if adv_id:
                    advertiser_ids.append(adv_id)
                    ADVERTISER_TOKEN[adv_id] = token
            page_info = data.get("data", {}).get("page_info", {})
            total_page = page_info.get("total_page", 1)
            if page >= total_page:
                break
            page += 1
    print(f"Найдено кабинетов: {len(advertiser_ids)}")
    return advertiser_ids


def get_campaign_spend(advertiser_id, date_str):
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
    r = requests.get(url, headers=headers, params=params, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        print(f"ОШИБКА получения расхода для кабинета {advertiser_id}: {data}")
        return []
    return data.get("data", {}).get("list", [])


def extract_keyword_from_campaign_name(name):
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 3:
        return None
    link_part = parts[2]
    link_part = re.sub(r"^https?://", "", link_part)
    link_part = link_part.split("?")[0]
    segments = [s for s in link_part.strip("/").split("/") if s]
    if len(segments) < 2:
        return None
    return segments[1]


def parse_campaign_name(name):
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
        sys.exit("ERROR: TIKTOK_ACCESS_TOKEN не задан")
    if not KEITARO_API_KEY:
        sys.exit("ERROR: KEITARO_ADMIN_API_KEY не задан")

    today = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"=== Синхронизация расхода за {today} ===")

    buyer_campaign_map = get_keitaro_buyer_campaigns()
    advertiser_ids = get_advertiser_ids()

    totals = {}
    unmapped_offers = set()
    unmapped_buyers = set()

    for adv_id in advertiser_ids:
        rows = get_campaign_spend(adv_id, today)
        for row in rows:
            metrics = row.get("metrics", {})
            campaign_name = metrics.get("campaign_name", "")
            spend = float(metrics.get("spend", 0) or 0)
            if spend <= 0:
                continue

            _, buyer = parse_campaign_name(campaign_name)
            if not buyer:
                continue

            campaign_id = buyer_campaign_map.get(buyer.lower())
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
        campaign_id = buyer_campaign_map[buyer.lower()]
        print(f"\n--- {buyer} (campaign_id={campaign_id}) ---")
        send_update_costs_batch(campaign_id, keywords, today)

    if unmapped_offers:
        print(f"\n!!! Кампании без keyword в ссылке: {sorted(unmapped_offers)}")
    if unmapped_buyers:
        print(f"!!! Баеры без кампании в Keitaro (создай кампанию вручную в UI, если нужно их отслеживать): {sorted(unmapped_buyers)}")

    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
