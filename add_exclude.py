#!/usr/bin/env python3
"""
Append excluded downtime to all mapped SLAs for a short service name (e.g. Shop).

Zabbix 6.4: sla.update replaces excluded_downtimes entirely, so this script
fetches existing entries first, then appends the new window.

Service short names and SLA mappings are loaded from .env (see example.env).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
# Fallback if .env has no timezone: line. Prefer setting timezone: in .env
# Example: timezone: Asia/Tehran
DEFAULT_TZ = "UTC"


@dataclass
class AppConfig:
    zabbix_address: str
    zabbix_token: str
    timezone: str = DEFAULT_TZ
    service_slas: dict[str, list[str]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _split_kv(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if ":" in line:
        k, v = line.split(":", 1)
    elif "=" in line:
        k, v = line.split("=", 1)
    else:
        return None
    return k.strip(), v.strip()


def load_config(path: Path) -> AppConfig:
    """Load Zabbix credentials + service→SLA map from .env."""
    if not path.exists():
        raise FileNotFoundError(
            f".env not found: {path}\nCopy example.env to .env and set zabbix-address / zabbix-token."
        )

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    flat: dict[str, str] = {}
    sla_raw: dict[str, str] = {}
    alias_raw: dict[str, str] = {}
    services_order: list[str] = []

    for line in raw_lines:
        parsed = _split_kv(line)
        if not parsed:
            continue
        key, value = parsed
        key_l = key.lower()

        if key_l == "services":
            services_order = [p.strip() for p in value.split(",") if p.strip()]
            continue
        if key_l.startswith("sla."):
            short = key[4:].strip()  # preserve case after sla.
            if short:
                sla_raw[short] = value
            continue
        if key_l.startswith("alias."):
            alias_name = key[6:].strip().lower()
            if alias_name:
                alias_raw[alias_name] = value.strip()
            continue

        flat[key_l] = value

    zabbix_address = (flat.get("zabbix-address") or "").rstrip("/")
    zabbix_token = flat.get("zabbix-token") or ""
    timezone = flat.get("timezone") or DEFAULT_TZ
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise ValueError(
            f"Invalid timezone '{timezone}' in .env. "
            "Use an IANA name, e.g. Asia/Tehran, UTC, Europe/London."
        ) from e

    # Build service map: prefer order from `services:`; include any extra sla.* too
    service_slas: dict[str, list[str]] = {}
    # Index sla_raw by normalized short name for lookup
    sla_by_norm = {_norm_name(k).lower(): (k, v) for k, v in sla_raw.items()}

    skipped: list[str] = []
    if services_order:
        for short in services_order:
            hit = sla_by_norm.get(_norm_name(short).lower())
            if not hit:
                # Commented-out or missing sla.* → treat as removed
                skipped.append(short)
                continue
            _, raw = hit
            names = [n.strip() for n in raw.split("|") if n.strip()]
            if not names:
                skipped.append(short)
                continue
            service_slas[short] = names

    # Also pick up any sla.* not listed in services: (so adding sla.test is enough)
    already = {_norm_name(k).lower() for k in service_slas}
    for short, raw in sla_raw.items():
        if _norm_name(short).lower() in already:
            continue
        names = [n.strip() for n in raw.split("|") if n.strip()]
        if names:
            service_slas[short] = names

    if skipped:
        print(
            "NOTE: skipped (no sla.<Name> in .env — commented or missing): "
            + ", ".join(skipped),
            file=sys.stderr,
        )

    if not service_slas:
        raise ValueError(
            ".env has no active services.\n"
            "Keep at least one entry in services: and a matching sla.<Name> line.\n"
            "See example.env."
        )

    # Aliases: auto lowercase short name + optional alias.* from .env
    aliases: dict[str, str] = {}
    for short in service_slas:
        aliases[short.lower()] = short
    for alias_key, target in alias_raw.items():
        # Resolve target to canonical short name
        resolved = None
        if target in service_slas:
            resolved = target
        else:
            for short in service_slas:
                if short.lower() == target.lower() or _norm_name(short).lower() == _norm_name(target).lower():
                    resolved = short
                    break
        if not resolved:
            raise ValueError(
                f"alias.{alias_key} points to unknown service '{target}'. "
                f"Known: {', '.join(service_slas)}"
            )
        aliases[alias_key.lower()] = resolved

    return AppConfig(
        zabbix_address=zabbix_address,
        zabbix_token=zabbix_token,
        timezone=timezone,
        service_slas=service_slas,
        aliases=aliases,
    )


def zabbix_call(url: str, token: str, method: str, params, req_id: int = 1):
    body = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "auth": token, "id": req_id}
    ).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} calling Zabbix API: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Zabbix API at {url}: {e.reason}") from e
    if "error" in data:
        err = data["error"]
        raise RuntimeError(
            f"Zabbix API {method}: {err.get('message')} — {err.get('data')}"
        )
    return data["result"]


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS in the configured timezone."""
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            naive = datetime.strptime(value, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError(
        f"Invalid datetime '{value}'. Use: YYYY-MM-DD HH:MM  (timezone from .env)"
    )


def resolve_service(cfg: AppConfig, name: str) -> str:
    key = name.strip()
    if key in cfg.service_slas:
        return key
    alias = cfg.aliases.get(key.lower())
    if alias:
        return alias
    for short in cfg.service_slas:
        if short.lower() == key.lower():
            return short
    raise ValueError(
        f"Unknown service '{name}'. Use one of: {', '.join(cfg.service_slas.keys())}\n"
        f"(edit services / sla.* in .env to add or remove)"
    )


def list_services(cfg: AppConfig) -> None:
    print("Short name → SLA names applied (from .env):")
    for short, names in cfg.service_slas.items():
        print(f"\n  {short}")
        for n in names:
            print(f"    - {n}")


def _norm_name(value: str) -> str:
    return " ".join(value.split())


def find_slas(api_url: str, token: str, sla_names: list[str]) -> list[dict]:
    """Resolve SLA names to objects with existing excluded_downtimes."""
    all_slas = zabbix_call(
        api_url,
        token,
        "sla.get",
        {
            "output": ["slaid", "name", "status"],
            "selectExcludedDowntimes": ["name", "period_from", "period_to"],
        },
    )
    by_exact = {s["name"]: s for s in all_slas}
    by_norm = {_norm_name(s["name"]): s for s in all_slas}

    found: list[dict] = []
    missing: list[str] = []
    for name in sla_names:
        sla = by_exact.get(name) or by_norm.get(_norm_name(name))
        if not sla:
            missing.append(name)
            continue
        found.append(sla)
    if missing:
        hint = "\n".join(f"  - {s['name']}" for s in all_slas)
        raise RuntimeError(
            "SLA not found in Zabbix (check exact names in .env):\n  - "
            + "\n  - ".join(missing)
            + "\n\nSLAs currently in Zabbix:\n"
            + hint
        )
    return found


def downtime_payload(name: str, period_from: int, period_to: int) -> dict:
    return {
        "name": name,
        "period_from": str(period_from),
        "period_to": str(period_to),
    }


def existing_as_payload(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in entries or []:
        out.append(
            {
                "name": e["name"],
                "period_from": str(e["period_from"]),
                "period_to": str(e["period_to"]),
            }
        )
    return out


def append_exclude(
    cfg: AppConfig,
    api_url: str,
    service: str,
    start: datetime,
    end: datetime,
    explanation: str,
    dry_run: bool = False,
) -> None:
    if end <= start:
        raise ValueError("End time must be after start time.")

    period_from = int(start.timestamp())
    period_to = int(end.timestamp())
    sla_names = cfg.service_slas[service]
    slas = find_slas(api_url, cfg.zabbix_token, sla_names)

    new_entry = downtime_payload(explanation, period_from, period_to)

    print(f"Service short name : {service}")
    print(f"Name               : {explanation}")
    print(f"Start time         : {start.strftime('%Y-%m-%d %H:%M')}  ({period_from})")
    print(
        f"Duration           : {format_duration(period_to - period_from)}  "
        f"(until {end.strftime('%Y-%m-%d %H:%M')})"
    )
    print()

    for sla in slas:
        existing = existing_as_payload(sla.get("excluded_downtimes") or [])
        if any(
            e["name"] == new_entry["name"]
            and e["period_from"] == new_entry["period_from"]
            and e["period_to"] == new_entry["period_to"]
            for e in existing
        ):
            print(
                f"[skip] {sla['name']} (slaid={sla['slaid']}) — "
                "identical exclude already present"
            )
            continue

        merged = existing + [new_entry]
        print(
            f"[ok]   {sla['name']} (slaid={sla['slaid']}) — "
            f"keeping {len(existing)} existing + 1 new"
        )
        if dry_run:
            continue

        zabbix_call(
            api_url,
            cfg.zabbix_token,
            "sla.update",
            {
                "slaid": sla["slaid"],
                "excluded_downtimes": merged,
            },
        )

    if dry_run:
        print("\nDry-run only — nothing was written to Zabbix.")
    else:
        print("\nDone. Open SLA → Excluded downtimes in Zabbix to verify.")


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def prompt(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{msg}{suffix}: ").strip()
    if not value and default is not None:
        return default
    return value


def prompt_int(msg: str, default: int = 0, minimum: int = 0, maximum: int | None = None) -> int:
    while True:
        raw = prompt(msg, default=str(default))
        try:
            value = int(raw)
        except ValueError:
            print(f"  Please enter a number (got '{raw}').")
            continue
        if value < minimum:
            print(f"  Must be >= {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"  Must be <= {maximum}.")
            continue
        return value


def duration_to_end(start: datetime, days: int, hours: int, minutes: int) -> datetime:
    total_seconds = days * 86400 + hours * 3600 + minutes * 60
    if total_seconds <= 0:
        raise ValueError("Duration must be greater than 0 (set Days / Hours / Minutes).")
    return start + timedelta(seconds=total_seconds)


def interactive(cfg: AppConfig) -> tuple[str, datetime, datetime, str]:
    """Prompt fields like Zabbix UI: Name, Start time, Duration (Days/Hours/Minutes)."""
    print("=" * 56)
    print("  New excluded downtime  (same fields as Zabbix UI)")
    print(f"  Start time timezone: {cfg.timezone}  |  YYYY-MM-DD HH:MM")
    print("=" * 56)
    print("Services:", ", ".join(cfg.service_slas.keys()))
    print()

    service = resolve_service(cfg, prompt("Service short name (e.g. Shop)"))
    print()
    print("--- New excluded downtime ---")
    name = prompt("Name *  (short description)")
    if not name:
        raise ValueError("Name is required (short description of why this downtime).")

    start = parse_local_datetime(prompt("Start time *  (YYYY-MM-DD HH:MM)"), cfg.tz)
    print("Duration *")
    days = prompt_int("  Days", default=0, minimum=0)
    hours = prompt_int("  Hours", default=1, minimum=0, maximum=23)
    minutes = prompt_int("  Minutes", default=0, minimum=0, maximum=59)
    end = duration_to_end(start, days, hours, minutes)

    print()
    print("Will Add to these SLAs:")
    for sla_name in cfg.service_slas[service]:
        print(f"  - {sla_name}")
    print(f"  Name     : {name}")
    print(f"  Start    : {start.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Duration : {days}d {hours}h {minutes}m  (until {end.strftime('%Y-%m-%d %H:%M')})")
    confirm = prompt("Add? (yes/no)", default="yes").lower()
    if confirm not in ("y", "yes"):
        raise ValueError("Cancelled — nothing was written.")

    return service, start, end, name


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Add excluded downtime to all mapped SLAs for a service (Zabbix 6.4)."
    )
    p.add_argument("--list", action="store_true", help="List short names and mapped SLAs")
    p.add_argument("--service", "-s", help="Short name, e.g. Shop")
    p.add_argument("--name", "-n", help="Name field (short description) — same as Zabbix UI")
    p.add_argument("--start", help="Start time (timezone from .env): YYYY-MM-DD HH:MM")
    p.add_argument("--days", type=int, default=None, help="Duration days (Zabbix UI)")
    p.add_argument("--hours", type=int, default=None, help="Duration hours 0-23 (Zabbix UI)")
    p.add_argument("--minutes", type=int, default=None, help="Duration minutes 0-59 (Zabbix UI)")
    p.add_argument("--end", help="Optional instead of duration: end time YYYY-MM-DD HH:MM")
    p.add_argument("--dry-run", action="store_true", help="Show what would be updated, do not write")
    p.add_argument("--env", type=Path, default=ENV_PATH, help="Path to .env")
    return p


def resolve_from_args(
    cfg: AppConfig, args: argparse.Namespace
) -> tuple[str, datetime, datetime, str] | None:
    """Return (service, start, end, name) if CLI args are complete; else None → interactive."""
    if not (args.service and args.start and args.name):
        return None

    service = resolve_service(cfg, args.service)
    start = parse_local_datetime(args.start, cfg.tz)
    name = args.name.strip()
    if not name:
        raise ValueError("Name (--name) is required.")

    has_duration = any(v is not None for v in (args.days, args.hours, args.minutes))
    if args.end and has_duration:
        raise ValueError("Use either --end OR --days/--hours/--minutes, not both.")
    if args.end:
        end = parse_local_datetime(args.end, cfg.tz)
    elif has_duration:
        days = args.days if args.days is not None else 0
        hours = args.hours if args.hours is not None else 0
        minutes = args.minutes if args.minutes is not None else 0
        if hours > 23 or minutes > 59 or days < 0 or hours < 0 or minutes < 0:
            raise ValueError("Duration: days>=0, hours 0-23, minutes 0-59.")
        end = duration_to_end(start, days, hours, minutes)
    else:
        return None

    return service, start, end, name


def main() -> int:
    args = build_parser().parse_args()

    try:
        cfg = load_config(args.env)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.list:
        list_services(cfg)
        return 0

    if not cfg.zabbix_address or not cfg.zabbix_token or cfg.zabbix_token.startswith("your-"):
        print(
            "ERROR: .env must define real zabbix-address and zabbix-token.\n"
            "See README.md for how to create an API token.",
            file=sys.stderr,
        )
        return 1

    api_url = f"{cfg.zabbix_address}/api_jsonrpc.php"

    try:
        parsed = resolve_from_args(cfg, args)
        if parsed is None:
            service, start, end, explanation = interactive(cfg)
        else:
            service, start, end, explanation = parsed

        append_exclude(
            cfg,
            api_url,
            service,
            start,
            end,
            explanation,
            dry_run=args.dry_run,
        )
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
