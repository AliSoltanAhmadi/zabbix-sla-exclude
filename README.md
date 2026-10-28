# zabbix-sla-exclude

Python CLI for **Zabbix 6.4+**. Add one **Excluded downtime** window to **all SLAs** mapped to a short service name — typically monthly, overall, and quarterly — in a single run.

No pip packages required (Python 3.9+, stdlib only).

---

## Why this exists

In Zabbix you often keep **several SLA objects per service** (e.g. monthly / overall / quarterly). Planned maintenance must be excluded from **each** of them, or reporting stays unfair.

Doing that by hand in the UI is slow and easy to miss one SLA. This tool:

1. Maps a short name (e.g. `Shop`) → exact SLA names from `.env`
2. Fetches current excluded downtimes via API
3. Appends your new window to **every** mapped SLA
4. Writes back with `sla.update`

---

## How it works

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────────────┐
│  You input  │────►│  Resolve short   │────►│  sla.get + existing         │
│  short name │     │  name → SLA list │     │  excluded_downtimes         │
│  + window   │     │  (from .env)     │     └──────────────┬──────────────┘
└─────────────┘     └──────────────────┘                    │
                                                            ▼
                                               ┌─────────────────────────────┐
                                               │  Append new window          │
                                               │  (name, period_from/to)     │
                                               └──────────────┬──────────────┘
                                                              ▼
                                               ┌─────────────────────────────┐
                                               │  sla.update per SLA         │
                                               │  (full list — keep old +    │
                                               │   add new)                  │
                                               └─────────────────────────────┘
```

**Important Zabbix detail:** `sla.update` **replaces** the entire `excluded_downtimes` list. This script always reads existing entries first, then sends **old + new**, so nothing is dropped.

Interactive prompts mirror the Zabbix UI fields: **Name**, **Start time**, **Duration** (Days / Hours / Minutes).

---

## Create a Zabbix API token

1. Open Zabbix  
2. **Users → API tokens** (or user profile → **API tokens**)  
3. **Create API token** — use an Admin / Super admin that can edit SLAs  
4. Copy the token once; store it only in `.env` as `zabbix-token`  
5. Token must allow `sla.get` and `sla.update`

---

## Setup

```bash
cd /path/to/zabbix-sla-exclude
cp example.env .env
# Edit .env: zabbix-address, zabbix-token, timezone, services / sla.*
```

Never commit `.env` (see `.gitignore`).

---

## Usage

### Interactive

```bash
python3 add_exclude.py
```

| Prompt | Zabbix UI field | Example |
|--------|-----------------|---------|
| Service short name | *(script only)* | `Shop` |
| Name * | Name / short description | `ISP maintenance window` |
| Start time * | Start time | `2026-10-16 00:00` |
| Days / Hours / Minutes * | Duration | `0` / `1` / `0` |

Start time uses the `timezone` from `.env` (IANA name, e.g. `Asia/Tehran`; fallback `UTC` if unset).

### One-liner

```bash
python3 add_exclude.py \
  --service Shop \
  --name "ISP maintenance window" \
  --start "2026-10-16 00:00" \
  --days 0 --hours 1 --minutes 0
```

### Dry-run (no write)

```bash
python3 add_exclude.py --dry-run -s Shop \
  --name "test exclude" \
  --start "2026-10-16 00:00" \
  --hours 1
```

### List mapped short names

```bash
python3 add_exclude.py --list
```

---

## Configure services in `.env`

Services are **not hardcoded** — edit `.env`:

```env
services: Shop, Payments, API

sla.Shop: Shop Services SLA | Shop Services Overall SLA | Shop Services Overall Quarterly
sla.Payments: Payments Services SLA | Payments Services Overall SLA | Payments Services Overall Quarterly

alias.pay: Payments
```

- **Add:** append to `services:` and add a `sla.NewName:` line (exact Zabbix SLA names, `|`-separated)  
- **Remove:** delete from `services:` (and optionally remove `sla.*`)  
- **Aliases:** `alias.short: Canonical Name`

---

## Files

| File | Purpose |
|------|---------|
| `add_exclude.py` | CLI |
| `example.env` | Template for credentials + SLA map |
| `.gitignore` | Ignores `.env` |

---

## Requirements

- Python 3.9+
- Zabbix 6.4+ (SLA API with `excluded_downtimes`)
- Network access to your Zabbix URL
