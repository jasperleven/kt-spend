import re

path = "/root/auto_spend_sync.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Добавляем словарь "Оффер -> keyword" (нужно заполнить реальными значениями!)
old_marker = '''# Название оффера больше не мапим руками - keyword достаём напрямую из
# трекинговой ссылки объявления (там уже есть ?keyword=xxx, т.к. так строятся все ссылки).'''

new_marker = '''# Оффер -> keyword. Обрабатываем ТОЛЬКО кампании, где в 3-м сегменте названия
# (там, где раньше была ссылка) стоит слово "Keitaro" - остальные пропускаем.
# Ключ словаря - точное название оффера (1-й сегмент названия кампании), без учёта регистра.
OFFER_TO_KEYWORD = {
    # "Электровелосипед": "vel",
    # заполнить реальными значениями по каждому баеру
}
OFFER_TO_KEYWORD_LOWER = {k.lower(): v for k, v in OFFER_TO_KEYWORD.items()}'''

assert old_marker in content, "БЛОК 1 (маркер-комментарий) НЕ НАЙДЕН"
content = content.replace(old_marker, new_marker)

# 2. Переписываем extract_keyword_from_campaign_name: фильтр по "Keitaro" + маппинг по офферу
old_func = '''def extract_keyword_from_campaign_name(name):
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
    # первый сегмент - домен, берём последний оставшийся сегмент как keyword
    keyword = segments[1]
    # отфильтруем явно не-keyword значения (например "ucenka" как под-путь холодильников -
    # тогда keyword может быть предпоследним сегментом; берём последний как основной случай)
    return keyword'''

new_func = '''def extract_keyword_from_campaign_name(name):
    """'Оффер | БАЕР | Keitaro | ...' -> keyword по словарю OFFER_TO_KEYWORD.

    Обрабатываем ТОЛЬКО кампании, где 3-й сегмент содержит слово "Keitaro"
    (значит трафик реально идёт через трекинговую ссылку Keitaro).
    Кампании без этого маркера пропускаются полностью - для них возвращаем None,
    и main() их просто игнорирует (не считает как "неизвестный оффер").
    """
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 3:
        return None
    link_part = parts[2]
    if "keitaro" not in link_part.lower():
        return None  # нет маркера Keitaro - не наша кампания, пропускаем молча
    offer = parts[0].strip()
    return OFFER_TO_KEYWORD_LOWER.get(offer.lower())'''

assert old_func in content, "БЛОК 2 (функция extract_keyword) НЕ НАЙДЕН"
content = content.replace(old_func, new_func)

# 3. В main(): кампании без маркера Keitaro (keyword is None, но это НЕ из-за отсутствия в
#    словаре) не должны засорять "unmapped_offers". Отличаем два случая: нет маркера vs
#    есть маркер но оффер не в словаре.
old_main_block = '''            keyword = extract_keyword_from_campaign_name(campaign_name)
            if not keyword:
                unmapped_offers.add(campaign_name)
                continue'''

new_main_block = '''            parts_check = [p.strip() for p in campaign_name.split("|")]
            has_keitaro_marker = len(parts_check) >= 3 and "keitaro" in parts_check[2].lower()
            if not has_keitaro_marker:
                continue  # кампания не ведёт трафик через Keitaro - пропускаем молча, без предупреждения
            keyword = extract_keyword_from_campaign_name(campaign_name)
            if not keyword:
                unmapped_offers.add(campaign_name)
                continue'''

assert old_main_block in content, "БЛОК 3 (main loop) НЕ НАЙДЕН"
content = content.replace(old_main_block, new_main_block)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Патч (1/2) применён успешно.")

# 4. Добавляем возможность указать конкретную дату параметром командной строки -
#    для пересчёта прошлых дней с новой (исправленной) логикой фильтрации по Keitaro.
old_main_date = '''def main():
    if not TIKTOK_ACCESS_TOKEN:
        sys.exit("ERROR: TIKTOK_ACCESS_TOKEN не задан (переменная окружения)")
    if not KEITARO_API_KEY:
        sys.exit("ERROR: KEITARO_ADMIN_API_KEY не задан (переменная окружения)")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== Синхронизация расхода за {today} ===")'''

new_main_date = '''def main():
    if not TIKTOK_ACCESS_TOKEN:
        sys.exit("ERROR: TIKTOK_ACCESS_TOKEN не задан (переменная окружения)")
    if not KEITARO_API_KEY:
        sys.exit("ERROR: KEITARO_ADMIN_API_KEY не задан (переменная окружения)")
    # Можно передать конкретную дату параметром, чтобы пересчитать прошлый день
    # с исправленной логикой: python3 auto_spend_sync.py 2026-08-20
    # Без параметра - берётся сегодня (обычный режим для cron).
    today = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"=== Синхронизация расхода за {today} ===")'''

with open(path, "r", encoding="utf-8") as f:
    content2 = f.read()

assert old_main_date in content2, "БЛОК 4 (main date) НЕ НАЙДЕН"
content2 = content2.replace(old_main_date, new_main_date)

with open(path, "w", encoding="utf-8") as f:
    f.write(content2)

print("Патч (2/2) применён успешно - теперь можно передавать дату параметром для backfill.")
print("!!! ВАЖНО: сейчас OFFER_TO_KEYWORD пустой - заполните его реальными парами")
print("    \x27Название оффера\x27: \x27keyword\x27 для каждого баера, иначе все кампании")
print("    с меткой Keitaro попадут в \x27Неизвестные офферы\x27 и расход не отправится.")
