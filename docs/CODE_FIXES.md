# Suggested code fixes before first commit

These are small fixes recommended before publishing the repository.

## 1. Rename the file

Rename:

```text
bot (28).py
```

To:

```text
bot.py
```

---

## 2. Add `notes` migration

The bot uses client notes. Make sure the `clients` table has a `notes` column.

Inside `db_init()`, add `notes TEXT` to the `clients` table:

```sql
CREATE TABLE IF NOT EXISTS clients (
    telegram_id  INTEGER PRIMARY KEY,
    client_type  TEXT NOT NULL DEFAULT 'client',
    tg_username  TEXT,
    lang         TEXT NOT NULL DEFAULT 'en',
    notes        TEXT,
    added_at     TEXT DEFAULT (datetime('now'))
);
```

Also add this migration for existing databases:

```python
try:
    c.execute("ALTER TABLE clients ADD COLUMN notes TEXT")
except Exception:
    pass
```

---

## 3. Add missing `get_client_payments()` helper

If `/history` calls `get_client_payments()`, define it near the other DB helper functions:

```python
def get_client_payments(tid: int, limit: int = 8):
    with db() as c:
        return c.execute(
            "SELECT * FROM payments WHERE telegram_id=? ORDER BY paid_at DESC LIMIT ?",
            (tid, limit)
        ).fetchall()
```

---

## 4. Make support username configurable

Replace:

```python
NOTIFY_USERNAME = "@genrdphelp"
```

With:

```python
NOTIFY_USERNAME = os.environ.get("NOTIFY_USERNAME", "@genrdphelp")
```

Then add `NOTIFY_USERNAME` to `.env.example`.

---

## 5. Optional: configurable Flask port

If the code currently hardcodes `8080`, use:

```python
PORT = int(os.environ.get("PORT", "8080"))
```

And start Flask with:

```python
app.run(host="0.0.0.0", port=PORT)
```
