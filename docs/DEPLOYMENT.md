# Deployment Guide

## 1. Local check

Before deploying, run:

```bash
python -m py_compile bot.py
pip install -r requirements.txt
python bot.py
```

Stop the bot with `CTRL+C`.

---

## 2. Cloudflare Tunnel

Use a public HTTPS hostname for payment callbacks.

```bash
cloudflared tunnel login
cloudflared tunnel create genrdp-bot
cloudflared tunnel route dns genrdp-bot bot.yourdomain.com
cloudflared tunnel run genrdp-bot
```

The hostname must match:

```env
BASE_URL=https://bot.yourdomain.com
```

---

## 3. Windows auto-start

Run PowerShell as Administrator and adapt the paths:

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\path\to\genrdp-renewal-bot\venv\Scripts\python.exe" `
    -Argument "C:\path\to\genrdp-renewal-bot\bot.py" `
    -WorkingDirectory "C:\path\to\genrdp-renewal-bot"

$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "GenRDP Bot" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -User "SYSTEM" `
    -Force
```

Check status:

```powershell
Get-ScheduledTask -TaskName "GenRDP Bot"
```

---

## 4. Docker deployment

```bash
cp .env.example .env
nano .env

docker compose up -d --build
docker compose logs -f
```

The database remains in:

```text
./data/genrdp.db
```

Back it up regularly.

---

## 5. Database backup

Example Linux backup:

```bash
mkdir -p backups
sqlite3 data/genrdp.db ".backup 'backups/genrdp-$(date +%F-%H%M).db'"
```

Do not commit backups to GitHub.
