# GenRDP Renewal Bot

Private Telegram bot for managing GenRDP proxy subscription renewals.

It supports client renewals via Stripe, PayPal and CoinGate, multi-language menus, reseller expiry handling, iProxy API integration, admin management, payment history and automated connection monitoring.

> **Private internal project. Do not publish credentials, database files or customer data.**

---

## Features

### Client side

- `/start` main menu with assigned proxy list
- Renew a single proxy via Card / Alipay / Google Pay, PayPal or Crypto
- Renew all expiring proxies in one aggregated payment
- Change proxy password directly from Telegram
- `/history` with active proxies, expiry dates and recent payments
- Expiry reminders
- Languages: English, Italian, Chinese and Russian

### Admin side

- Guided `/admin` panel with inline buttons
- Add, remove and move proxies between clients
- Client types: `client` and `reseller`
- Per-proxy price override
- Client notes
- Manual renewal
- Broadcast/newsletter to clients and resellers
- Revenue dashboard
- Reseller expiry overview

### Monitoring

- Periodic iProxy connection checks
- Automatic refresh/toggle attempts for offline connections
- Daily Telegram report
- Payment confirmations to support/admin channel

---

## Recommended repository structure

```text
genrdp-renewal-bot/
├── bot.py
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── data/
│   └── .gitkeep
├── docs/
│   ├── DEPLOYMENT.md
│   └── CODE_FIXES.md
├── scripts/
│   ├── run_dev.sh
│   └── windows_start.ps1
```
---

## Setup

### 1. Clone repository

```bash
git clone https://github.com/YOUR_USERNAME/genrdp-renewal-bot.git
cd genrdp-renewal-bot
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Then edit `.env` with real credentials.

Required variables:

```env
TELEGRAM_TOKEN=
ADMIN_IDS=
NOTIFY_USERNAME=@genrdphelp
IPROXY_API_KEY=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
PAYPAL_CLIENT_ID=
PAYPAL_CLIENT_SECRET=
PAYPAL_MODE=sandbox
COINGATE_API_KEY=
COINGATE_MODE=sandbox
BASE_URL=https://bot.yourdomain.com
DB_PATH=data/genrdp.db
PORT=8080
```

### 4. Run locally

```bash
python bot.py
```

The bot uses Telegram polling and exposes Flask webhook routes for payment callbacks.

---

## Payment webhooks

### Stripe

Create a webhook endpoint in Stripe Dashboard:

```text
https://bot.yourdomain.com/stripe-webhook
```

Required event:

```text
checkout.session.completed
```

### PayPal

PayPal approval and return URLs are generated dynamically by the bot.

### CoinGate

Create a webhook/callback endpoint:

```text
https://bot.yourdomain.com/coingate-webhook
```

---

## Docker

Copy `.env.example` to `.env`, fill real values, then run:

```bash
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f
```

Stop:

```bash
docker compose down
```

The SQLite database is persisted in `./data`.

---

## Security checklist before pushing to GitHub

- Repository should be **private**
- `.env` must not be committed
- `data/data.db` must not be committed
- No real Telegram, Stripe, PayPal, CoinGate or iProxy keys in code
- No screenshots containing customer data or tokens
- Rotate any key that was accidentally committed

---

## License

Private internal project — not for redistribution.
