#!/bin/bash
# Launch headless Chrome with remote debugging on 127.0.0.1:9222, point at HIS login.
# Dedicated profile; REA attaches passively. We do NOT expose the port beyond loopback.
CHROME=/home/ma-user/.cache/puppeteer/chrome/linux-155.0.8059.39/chrome-linux64/chrome
PROFILE=/tmp/rea-his-profile
rm -rf "$PROFILE"
mkdir -p "$PROFILE"
exec "$CHROME" \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --remote-debugging-port=9222 \
  --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$PROFILE" \
  --password-store=basic \
  --disable-dev-shm-usage \
  "https://his.igancao.cn/"
