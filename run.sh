#!/bin/bash
# Kit Creator Network Scraper — cron wrapper
set -a
source /etc/environment
set +a

cd /root/clawd/kit-creator-network-scraper
python3 scraper.py >> /var/log/kit-scraper.log 2>&1
