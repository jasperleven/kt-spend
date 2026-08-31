#!/usr/bin/env python3
"""
auto_spend_sync.py (v4)

Для кампаний с тегом "Keitaro" вместо ссылки: keyword достаётся из
ad_name объявления внутри кампании (формат "номер_код_файл_метка"),
код автоматически транслитерируется кириллица->латиница и сопоставляется
с уже существующими keyword-потоками кампании в Keitaro по частичному
совпадению (kolh -> kolh5, nout -> nout, minitr -> minitr, motob -> motob).

Без ручного словаря "оффер -> keyword" - это чистая транслитерация
короткого технического кода, который сам баер уже использует в имени
объявления, а не перевод/угадывание по полному русскому названию оффера.
"""

import os
import re
import sys
import json
import fcntl
import requests
from datetime import datetime, timedelta

LOCK_FILE = "/tmp/auto_spend_sync.lock"

TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_MARKETING_TOKEN") or os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_ACCESS_TOKEN_BC2 = os.environ.get("TIKTOK_MARKETING_TOKEN_NASTYA") or os.environ.get("TIKTOK_ACCESS_TOKEN_BC2", "")

BUSINESS_CENTER_IDS = ["7632400042631888913", "7410341052470607888"]
BC_TOKENS = {
    "7632400042631888913": TIKTOK_ACCESS_TOKEN,
    "7410341052470607888": TIKTOK_ACCESS_TOKEN_BC2,
}
ADVERTISER_TOKEN = {}

KEITARO_BASE_URL = os.environ.get("KEITARO_BASE_URL", "http://167.233.96.7")
KEITARO_API_KEY = os.environ.get("KEITARO_ADMIN_API_KEY", "")
TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
KT_HEADERS = {"Api-Key": KEITARO_API_KEY, "Content-Type": "application/json"}

TRANSLIT_MAP = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z',
    'и':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
    'с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'c','ч':'ch','ш':'sh','щ':'sch',
    'ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
}


def translit(s):
    return ''.join(TRANSLIT_MAP.get(ch, ch) for ch in s.lower())


def get_keitaro_buyer_campaigns():
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


def get_keitaro_streams(campaign_id):
    """Тянет потоки кампании (отдельный endpoint - НЕ вложены в объект
    кампании) вместе с их keyword-фильтрами и названиями. Возвращает
    список (keyword, stream_name) пар для матчинга."""
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
    """streams: список (keyword, stream_name).
    1) прямое/частичное совпадение по keyword (kolh -> kolh5)
    2) fallback: оригинальный код (кириллица, напр. 'колх') как подстрока
       в названии потока (напр. "Электровелосипед Колхозник")."""
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
            items = data.get("data", {}).get("list", [])
            for item in items:
                adv_id = item.get("advertiser_id") or item.get("asset_id") or item.get("id")
                if adv_id:
                    advertiser_ids.append(adv_id)
                    ADVERTISER_TOKEN[adv_id] = token
            page_info = data.get("data", {}).get("page_info", {})
            if page >= page_info.get("total_page", 1):
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
        return []
    return data.get("data", {}).get("list", [])


def get_ad_code(advertiser_id, campaign_id_tt):
    token = ADVERTISER_TOKEN.get(advertiser_id, TIKTOK_ACCESS_TOKEN)
    headers = {"Access-Token": token, "Content-Type": "application/json"}
    url = f"{TIKTOK_API_BASE}/ad/get/"
    params = {
        "advertiser_id": advertiser_id,
        "campaign_ids": json.dumps([campaign_id_tt]),
        "fields": json.dumps(["ad_id", "ad_name"]),
        "page_size": 1,
    }
    r = requests.get(url, headers=headers, params=params, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        return None
    ads = data.get("data", {}).get("list", [])
    if not ads:
        return None
    ad_name = ads[0].get("ad_name", "")
    parts = ad_name.split("_")
    if len(parts) < 2:
        return None
    return parts[1].strip()


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


def is_keitaro_tag(name):
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 3:
        return False
    return parts[2].strip().lower() == "keitaro"


def send_update_costs_batch(campaign_id, keyword_costs, date_str):
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)
    end_str = end_dt.strftime("%Y-%m-%d")
    for keyword, cost in keyword_costs.items():
        payload = {
            "start_date": date_str, "end_date": end_str, "cost": round(cost, 2),
            "currency": "USD", "timezone": "Europe/Moscow",
            "only_campaign_uniques": False, "filters": {"keyword": keyword},
        }
        url = f"{KEITARO_BASE_URL}/admin_api/v1/campaigns/{campaign_id}/update_costs"
        r = requests.post(url, headers=KT_HEADERS, json=payload, timeout=30)
        print(f"  campaign {campaign_id} / {keyword} = {cost:.2f} USD -> HTTP {r.status_code} {r.text[:200]}")


def send_update_costs_by_subid1(campaign_id, tiktok_campaign_name, cost, date_str):
    """Для Keitaro-тег кампаний: фильтруем НЕ по keyword (общий на несколько
    TikTok-кампаний), а по sub_id_1 = точное имя TikTok-кампании. Это
    изолирует cost строго на клики ЭТОЙ кампании, без утечки на соседние
    кампании с тем же оффером - математически sum(cost) после распределения
    равен присланной сумме без потерь (в отличие от keyword-фильтра)."""
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)
    end_str = end_dt.strftime("%Y-%m-%d")
    payload = {
        "start_date": date_str, "end_date": end_str, "cost": round(cost, 2),
        "currency": "USD", "timezone": "Europe/Moscow",
        "only_campaign_uniques": False,
        "filters": {"sub_id_1": tiktok_campaign_name.strip()},
    }
    url = f"{KEITARO_BASE_URL}/admin_api/v1/campaigns/{campaign_id}/update_costs"
    r = requests.post(url, headers=KT_HEADERS, json=payload, timeout=30)
    print(f"  campaign {campaign_id} / sub_id_1='{tiktok_campaign_name[:40]}...' = {cost:.2f} USD -> HTTP {r.status_code} {r.text[:200]}")


def main():
    # Защита от параллельного запуска: если cron и ручной запуск пересекутся
    # по времени, более старый (меньший по накопленной сумме) запрос может
    # долететь ПОСЛЕ более нового и перезаписать его меньшим значением
    # (update_costs делает SET, а не ADD) - лок гарантирует, что одновременно
    # выполняется только один экземпляр скрипта.
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Другой экземпляр auto_spend_sync.py уже выполняется - выходим, чтобы не создавать гонку записи.")

    if not TIKTOK_ACCESS_TOKEN:
        sys.exit("ERROR: TIKTOK_ACCESS_TOKEN не задан")
    if not KEITARO_API_KEY:
        sys.exit("ERROR: KEITARO_ADMIN_API_KEY не задан")

    today = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"=== Синхронизация расхода за {today} ===")

    buyer_campaign_map = get_keitaro_buyer_campaigns()
    advertiser_ids = get_advertiser_ids()

    campaign_streams_cache = {}
    totals = {}
    keitaro_tag_pushes = []  # (campaign_id_kt, campaign_name, spend) - изолированно, по sub_id_1
    unmapped_offers = set()
    unmapped_buyers = set()
    keitaro_tag_no_match = set()

    for adv_id in advertiser_ids:
        rows = get_campaign_spend(adv_id, today)
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

            campaign_id_kt = buyer_campaign_map.get(buyer.lower())
            if not campaign_id_kt:
                unmapped_buyers.add(buyer)
                continue

            if is_keitaro_tag(campaign_name):
                # Считаем и шлём ТОЛЬКО Keitaro-тег кампании отдельным
                # запросом по sub_id_1 (точное имя TikTok-кампании) -
                # не мешаем с обычными ссылочными кампаниями того же
                # оффера/keyword, чтобы их cost не пересекался.
                keitaro_tag_pushes.append((campaign_id_kt, buyer, campaign_name, spend))
            else:
                keyword = extract_keyword_from_campaign_name(campaign_name)
                if not keyword:
                    unmapped_offers.add(campaign_name)
                    continue
                totals.setdefault(buyer, {}).setdefault(keyword, 0)
                totals[buyer][keyword] += spend

    print(f"\nСобрано расхода (обычные ссылки) по {len(totals)} баерам")
    for buyer, keywords in totals.items():
        campaign_id = buyer_campaign_map[buyer.lower()]
        print(f"\n--- {buyer} (campaign_id={campaign_id}) ---")
        send_update_costs_batch(campaign_id, keywords, today)

    if keitaro_tag_pushes:
        grouped = {}
        for campaign_id_kt, buyer, campaign_name, spend in keitaro_tag_pushes:
            key = (campaign_id_kt, campaign_name.strip())
            grouped[key] = grouped.get(key, 0) + spend

        print(f"\nОтправка Keitaro-тег кампаний по sub_id_1 (изолированно, {len(grouped)} уникальных имён):")
        for (campaign_id_kt, campaign_name), spend in grouped.items():
            send_update_costs_by_subid1(campaign_id_kt, campaign_name, spend, today)

    if unmapped_offers:
        print(f"\n!!! Кампании без keyword в ссылке: {sorted(unmapped_offers)}")
    if unmapped_buyers:
        print(f"!!! Баеры без кампании в Keitaro: {sorted(unmapped_buyers)}")

    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
