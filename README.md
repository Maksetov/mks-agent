# Agent Max — Multi Level MKS channel agent

Daily: 08:00 draft → your DM (Approve / Edit / New version / Reject) → 20:00 publish if approved.
Unapproved = skipped. Nothing reaches the channel without your ✅.

## Layout
config/calendar.yaml   weekdays, formats, times, limits (edit freely, then restart)
config/voice.md        MKS voice + output rules (the generator's system prompt)
app/generator.py       picks a format, generates, retries with checker feedback
app/checker.py         hard rules in code + blind-solve + editor review
app/media.py           TTS audio, meme rendering
app/render.py          DM previews, channel publishing
app/bot.py             commands, buttons, daily jobs
media/templates/       meme template images (file name = template name)
data/                  SQLite db + generated media (created on first run)

## Install (Ubuntu droplet)
apt install -y python3-venv
cd /root/mks-agent && python3 -m venv venv && venv/bin/pip install -r requirements.txt
bash setup.sh   # asks for token, admin id, OpenAI key
cp deploy/mks-agent.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now mks-agent
journalctl -u mks-agent -f               # live logs (Ctrl+C to exit)

## Commands (DM @mksagentbot)
/check  /today  /tomorrow  /status  /cancel
