#!/bin/bash
# One-time installer for Agent Max. Run: bash /root/mks-agent/setup.sh
set -e
cd /root/mks-agent
echo "== Installing Python tools (1-2 min)..."
apt-get install -y python3-venv >/dev/null
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo ""
  echo "== Paste each value and press Enter."
  read -p "1) Agent bot token (from BotFather): " TOKEN
  read -p "2) Your Telegram user ID (numbers only): " ADMIN
  read -sp "3) OpenAI API key (hidden while you paste): " KEY; echo
  cat > .env <<ENV
AGENT_BOT_TOKEN=$TOKEN
ADMIN_USER_ID=$ADMIN
CHANNEL_ID=-1003787853665
OPENAI_API_KEY=$KEY
GEN_MODEL=gpt-4.1
CHECK_MODEL=gpt-4.1
TTS_MODEL=gpt-4o-mini-tts
TZ=Asia/Tashkent
ENV
  chmod 600 .env
fi

cp deploy/mks-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mks-agent
sleep 8
echo ""
if systemctl is-active --quiet mks-agent; then
  echo "✅ Agent Max is running. Open @mksagentbot in Telegram and send /check"
else
  echo "❌ Something failed. Last log lines:"
  journalctl -u mks-agent -n 20 --no-pager
fi
