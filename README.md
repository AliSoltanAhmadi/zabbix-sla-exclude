# Zabbix SLA excluded downtime helper

Python CLI + TUI for **Zabbix 6.4+**. Enter a short service name, start time, duration, and a short description — the tool appends that **excluded downtime** to every SLA mapped to that short name (for example monthly / overall / quarterly).

## Create a Zabbix API token

1. Open your Zabbix UI  
2. Go to **Users → API tokens** (menu labels vary by version)  
3. **Create API token** for an **Admin** / **Super admin** that can edit SLAs  
4. Copy the token (shown once) into `.env` as `zabbix-token`

The token user must be allowed to call `sla.get` and `sla.update`.

## Setup

```bash
git clone https://github.com/AliSoltanAhmadi/zabbix-sla-exclude.git
cd zabbix-sla-exclude
cp example.env .env
# edit .env: zabbix-address, zabbix-token, timezone, services / sla.* lines
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # needed for TUI (Textual)
```

On Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

CLI one-liners work with system Python 3.9+ (stdlib only). The TUI needs the `.venv`.

## Usage

### TUI (default)

```bash
./run-tui.sh
# or
.venv/bin/python add_exclude.py
# or
.venv/bin/python tui.py
```

| Keys / controls | Action |
|-----------------|--------|
| Service list | pick a short name from `.env` |
| Name / Start / Days·Hours·Minutes | same fields as the Zabbix UI |
| `d` or **Dry-run** | preview, no write |
| `a` or **Add / Apply** | write to all mapped SLAs |
| `q` | quit |

Start times are interpreted in the `timezone` from `.env` (IANA name, e.g. `UTC` or `Asia/Tehran`).

### Classic prompts

```bash
python3 add_exclude.py --cli
```

### One-liner (non-interactive)

```bash
python3 add_exclude.py \
  --service AppOne \
  --name "planned network maintenance" \
  --start "2026-10-16 00:00" \
  --days 0 --hours 1 --minutes 0
```

### Dry-run (no write)

```bash
python3 add_exclude.py --dry-run -s AppOne \
  --name "test exclude" \
  --start "2026-10-16 00:00" \
  --hours 1
```

### List short names

```bash
python3 add_exclude.py --list
```

## Short name → SLAs (edit in `.env`)

Services are **not hardcoded** — configure them in `.env`:

```env
services: AppOne, AppTwo

sla.AppOne: AppOne Services SLA | AppOne Services Overall SLA | AppOne Services Overall Quarterly
sla.AppTwo: AppTwo Services SLA | AppTwo Services Overall SLA | AppTwo Services Overall Quarterly
```

- **Add a service:** append to `services:` and add a `sla.NewName:` line  
- **Remove a service:** comment out / delete its `sla.*` line (and remove it from `services:`)  
- Optional shortcuts: `alias.app1: AppOne`

List current mapping: `python3 add_exclude.py --list`

## How it works

1. Resolves short name → exact SLA names from `.env`  
2. `sla.get` + `selectExcludedDowntimes` for each SLA  
3. Appends your new window (`name`, `period_from`, `period_to`)  
4. `sla.update` with the **full** list (Zabbix replaces the whole list — existing entries are kept)

This matches the Zabbix UI field **Excluded downtimes** → Name / Start / Duration.
