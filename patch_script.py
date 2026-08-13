import re

path = "/root/auto_spend_sync.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Добавляем токен для BC2 и словарь BC -> токен
old_config = '''BUSINESS_CENTER_IDS = [
    "7632400042631888913",  # BC1
    "7410341052470607888",  # BC2 (ООО «Артагед»)
]'''

new_config = '''BUSINESS_CENTER_IDS = [
    "7632400042631888913",  # BC1
    "7410341052470607888",  # BC2 (ООО «Артагед»)
]

TIKTOK_ACCESS_TOKEN_BC2 = os.environ.get("TIKTOK_ACCESS_TOKEN_BC2", "")

BC_TOKENS = {
    "7632400042631888913": TIKTOK_ACCESS_TOKEN,
    "7410341052470607888": TIKTOK_ACCESS_TOKEN_BC2,
}

# Заполняется в get_advertiser_ids(): advertiser_id -> нужный токен для запроса его расхода
ADVERTISER_TOKEN = {}'''

assert old_config in content, "БЛОК 1 НЕ НАЙДЕН"
content = content.replace(old_config, new_config)

# 2. Переписываем get_advertiser_ids чтобы использовать токен своего BC и запоминать его на кабинет
old_func = '''def get_advertiser_ids():
    """Автоматически находит все рекламные кабинеты во всех Business Center (с пагинацией)."""
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
    return advertiser_ids'''

new_func = '''def get_advertiser_ids():
    """Автоматически находит все рекламные кабинеты во всех Business Center (с пагинацией).
    У каждого BC может быть свой токен (BC_TOKENS) - запоминаем, каким токеном
    нужно потом тянуть расход по каждому конкретному кабинету."""
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
    return advertiser_ids'''

assert old_func in content, "БЛОК 2 НЕ НАЙДЕН"
content = content.replace(old_func, new_func)

# 3. Правим get_today_campaign_spend чтобы использовала правильный токен для своего кабинета
old_spend_headers = '''    r = requests.get(url, headers=TT_HEADERS, params=params, timeout=30)'''
new_spend_headers = '''    headers = {"Access-Token": ADVERTISER_TOKEN.get(advertiser_id, TIKTOK_ACCESS_TOKEN), "Content-Type": "application/json"}
    r = requests.get(url, headers=headers, params=params, timeout=30)'''

count = content.count(old_spend_headers)
print(f"Найдено вхождений строки для замены в get_today_campaign_spend: {count}")
# Заменяем только ПЕРВОЕ вхождение (внутри get_today_campaign_spend) - остальные (если есть) не трогаем
content = content.replace(old_spend_headers, new_spend_headers, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Патч применён успешно.")
