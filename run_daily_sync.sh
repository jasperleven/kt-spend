#!/bin/bash
# Запускается раз в день в 6:00 - берёт ВЧЕРАШНИЙ, уже полностью
# закрытый день, чтобы TikTok-цифры точно устоялись.
YESTERDAY=$(date -u -d "yesterday" +%Y-%m-%d)
cd /root
export $(cat /root/.keitaro_admin_api.env | xargs)
export $(cat /root/tiktok_bot/.env | xargs)
python3 /root/auto_spend_sync.py "$YESTERDAY" >> /root/auto_spend_sync_daily.log 2>&1
python3 /root/keitaro_tag_spend_log.py "$YESTERDAY" >> /root/keitaro_tag_spend.log 2>&1
