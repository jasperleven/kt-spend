#!/usr/bin/env python3
"""
verify_keyword_combined.py

Сверка того, что РЕАЛЬНО отправляет auto_spend_sync.py сейчас: расход
объединён по (баер, keyword) - обычные ссылки + Keitaro-тег вместе -
и шлётся одним update_costs. Сверяем именно эту комбинированную сумму
с реальным sum(cost) в Keitaro по тому же keyword (без изоляции тега).

Запуск:
  export $(cat /root/.keitaro_admin_api.env | xargs)
  export $(cat /root/tiktok_bot/.env | xargs)
  python3 verify_keyword_combined.py [YYYY-MM-DD]
"""

import os
import re
import sys
import json
import subprocess
from datetime import datetime

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

BUYER_CAMPAIGN_IDS = {"BNS": 10, "VAD": 5, "KRL": 7}

TRANSLIT_MAP = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z',
    'и':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
    'с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'c','ч':'ch','ш':'sh','щ':'sch',
    'ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
}


def translit(s):
    return ''.join(TRANSLIT_MAP.get(ch, ch) for ch in s.lower())


def get_keitaro_streams(campaign_id):
    url = f"{KEITARO_BASE_URL}/admin_api/v1/campaigns/{campaign_id}/streams"
    r = requests.get(url, headers=KT_HEADERS, timeout=30)
    if r.status_code != 200:
        return []
    streams = r.json()
    if isinstance(streams, dict) and "data" in streams:
        streams = streams["data"]
    result = []
    for s in streams:
        stream_name = (s.get("name") or "").lower()
        for f in s.get("filters", []):
            if f.get("name") != "keyword":
                continue
            payload = f.get("payload", [])
            kws = payload if isinstance(payload, list) else [payload]
            for kw in kws:
                if kw:
                    result.append((str(kw).lower(), stream_name))
    return result


def match_keyword_full(code, translit_code, streams):
    for kw, _ in streams:
        if translit_code == kw or translit_code in kw or kw in translit_code:
            return kw
    for kw, stream_name in streams:
        if code in stream_name:
            return kw
    return None


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


def get_ad_code(advertiser_id, campaign_id_tt):
    token = ADVERTISER_TOKEN.get(advertiser_id, TIKTOK_ACCESS_TOKEN)
    headers = {"Access-Token": token, "Content-Type": "application/json"}
    url = f"{TIKTOK_API_BASE}/ad/get/"
    params = {
        "advertiser_id": advertiser_id, "campaign_ids": json.dumps([campaign_id_tt]),
        "fields": json.dumps(["ad_id", "ad_name"]), "page_size": 1,
    }
    r = requests.get(url, headers=headers, params=params, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        return None
    ads = data.get("data", {}).get("list", [])
    if not ads:
        return None
    parts = ads[0].get("ad_name", "").split("_")
    return parts[1].strip() if len(parts) >= 2 else None


def extract_keyword_from_campaign_name(name):
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 3:
        return None
    link_part = re.sub(r"^https?://", "", parts[2]).split("?")[0]
    segments = [s for s in link_part.strip("/").split("/") if s]
    return segments[1] if len(segments) >= 2 else None


def parse_campaign_name(name):
    parts = [p.strip() for p in name.split("|")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (None, None)


def is_keitaro_tag(name):
    parts = [p.strip() for p in name.split("|")]
    return len(parts) >= 3 and parts[2].strip().lower() == "keitaro"


def keitaro_keyword_cost(campaign_id, keyword, date_str):
    query = (
        f"SELECT sum(cost), count(*) FROM keitaro.keitaro_events FINAL "
        f"WHERE campaign_id = {campaign_id} AND keyword = '{keyword}' "
        f"AND toDate(datetime) = '{date_str}'"
    )
    try:
        result = subprocess.run(
            ["podman", "exec", "-i", "clickhouse", "clickhouse-client",
             "--user", "keitaro", "--password", CLICKHOUSE_PASSWORD, "--query", query],
            capture_output=True, text=True, timeout=30,
        )
        parts = result.stdout.strip().split("\t")
        cost = float(parts[0]) if parts and parts[0] else 0.0
        clicks = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        return cost, clicks
    except Exception:
        return None, None


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"=== Сверка combined-keyword (то, что реально шлётся) за {date_str} ===\n")

    advertiser_ids = get_advertiser_ids()
    campaign_streams_cache = {}
    totals = {}  # (buyer, keyword) -> tiktok spend (combined regular + tag)

    for adv_id in advertiser_ids:
        rows = get_campaign_spend(adv_id, date_str)
        for row in rows:
            metrics = row.get("metrics", {})
            campaign_name = metrics.get("campaign_name", "")
            campaign_id_tt = row.get("dimensions", {}).get("campaign_id")
            spend = float(metrics.get("spend", 0) or 0)
            if spend <= 0:
                continue

            _, buyer = parse_campaign_name(campaign_name)
            if not buyer:
                continue
            campaign_id_kt = BUYER_CAMPAIGN_IDS.get(buyer)
            if not campaign_id_kt:
                continue

            if is_keitaro_tag(campaign_name):
                code = get_ad_code(adv_id, campaign_id_tt) if campaign_id_tt else None
                if not code:
                    continue
                tr = translit(code)
                if campaign_id_kt not in campaign_streams_cache:
                    campaign_streams_cache[campaign_id_kt] = get_keitaro_streams(campaign_id_kt)
                keyword = match_keyword_full(code.lower(), tr, campaign_streams_cache[campaign_id_kt])
                if not keyword:
                    continue
            else:
                keyword = extract_keyword_from_campaign_name(campaign_name)
                if not keyword:
                    continue

            key = (buyer, campaign_id_kt, keyword)
            totals[key] = totals.get(key, 0) + spend

    print(f"{'Баер':8s} {'Keyword':10s} {'TikTok (combined)':>18s} {'Keitaro':>10s} {'Клики':>7s} {'Разница':>10s}")
    total_diff = 0
    for (buyer, campaign_id, keyword), tt_spend in sorted(totals.items()):
        kt_spend, clicks = keitaro_keyword_cost(campaign_id, keyword, date_str)
        if kt_spend is None:
            print(f"{buyer:8s} {keyword:10s} {tt_spend:18.2f} {'ошибка':>10s}")
            continue
        diff = kt_spend - tt_spend
        total_diff += abs(diff)
        flag = "  <-- РАСХОЖДЕНИЕ" if abs(diff) > 0.5 else "  OK"
        print(f"{buyer:8s} {keyword:10s} {tt_spend:18.2f} {kt_spend:10.2f} {clicks:7d} {diff:10.2f}{flag}")

    print(f"\nСуммарное абсолютное расхождение: {total_diff:.2f}")


if __name__ == "__main__":
    main()
