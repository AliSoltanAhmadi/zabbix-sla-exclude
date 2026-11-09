#!/usr/bin/env python3
"""
Textual TUI for Zabbix SLA excluded downtime.

Fields match the Zabbix UI: Name, Start time, Duration (Days/Hours/Minutes).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RichLog,
    Static,
)
from textual.widgets.option_list import Option

from add_exclude import (
    ENV_PATH,
    AppConfig,
    duration_to_end,
    load_config,
    parse_local_datetime,
    append_exclude,
)


class SlaExcludeApp(App[None]):
    TITLE = "SLA Excluded Downtime"
    SUB_TITLE = "Zabbix SLA exclude"
    CSS = """
    Screen {
        layout: vertical;
    }
    #body {
        height: 1fr;
        padding: 1 2;
    }
    #left {
        width: 32;
        min-width: 24;
        border: round $accent;
        padding: 0 1;
    }
    #right {
        width: 1fr;
        border: round $primary;
        padding: 0 1 1 1;
    }
    #preview {
        height: 7;
        border: round $surface;
        padding: 0 1;
        color: $text-muted;
    }
    #log {
        height: 1fr;
        border: round $surface;
        margin-top: 1;
    }
    .row {
        height: auto;
        margin: 0 0 1 0;
    }
    .field-label {
        width: 14;
        color: $text-muted;
    }
    #duration-row Input {
        width: 10;
        margin-right: 1;
    }
    #duration-row Label {
        width: auto;
        margin-right: 1;
        color: $text-muted;
    }
    #actions {
        height: auto;
        margin-top: 1;
    }
    #actions Button {
        margin-right: 1;
    }
    #status {
        height: 1;
        color: $accent;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "dry_run", "Dry-run"),
        ("a", "apply", "Apply"),
    ]

    def __init__(self, cfg: AppConfig, env_path: Path) -> None:
        self.cfg = cfg
        self.env_path = env_path
        self.api_url = f"{cfg.zabbix_address}/api_jsonrpc.php"
        self._selected: str | None = None
        self._service_by_option_id = {
            f"svc-{i}": name for i, name in enumerate(cfg.service_slas)
        }
        super().__init__()
        self.sub_title = f"Zabbix · {cfg.timezone_name}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Label("Services", classes="field-label")
                yield OptionList(
                    *[
                        Option(name, id=f"svc-{i}")
                        for i, name in enumerate(self.cfg.service_slas)
                    ],
                    id="services",
                )
            with VerticalScroll(id="right"):
                yield Static(
                    f"Zabbix: {self.cfg.zabbix_address}  ·  .env: {self.env_path}",
                    id="status",
                )
                yield Label("New excluded downtime (same fields as Zabbix UI)")
                with Horizontal(classes="row"):
                    yield Label("Name *", classes="field-label")
                    yield Input(placeholder="short description", id="name")
                with Horizontal(classes="row"):
                    yield Label("Start time *", classes="field-label")
                    yield Input(
                        value=datetime.now(self.cfg.tz).strftime("%Y-%m-%d %H:%M"),
                        placeholder="YYYY-MM-DD HH:MM",
                        id="start",
                    )
                with Horizontal(classes="row", id="duration-row"):
                    yield Label("Duration *", classes="field-label")
                    yield Label("Days")
                    yield Input(value="0", placeholder="0", id="days", type="integer")
                    yield Label("Hours")
                    yield Input(value="1", placeholder="0", id="hours", type="integer")
                    yield Label("Mins")
                    yield Input(value="0", placeholder="0", id="minutes", type="integer")
                yield Static("Select a service…", id="preview")
                with Horizontal(id="actions"):
                    yield Button("Dry-run", id="btn-dry", variant="warning")
                    yield Button("Add / Apply", id="btn-apply", variant="success")
                    yield Button("Quit", id="btn-quit", variant="default")
                yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write("[dim]Select a service, fill Name / Start / Duration, then Dry-run or Apply.[/]")
        services = self.query_one("#services", OptionList)
        if self.cfg.service_slas:
            services.highlighted = 0
            first = next(iter(self.cfg.service_slas))
            self._select_service(first)

    def _select_service(self, name: str) -> None:
        self._selected = name
        slas = self.cfg.service_slas.get(name, [])
        lines = [f"[b]{name}[/] → {len(slas)} SLA(s):"]
        for s in slas:
            lines.append(f"  • {s}")
        self.query_one("#preview", Static).update("\n".join(lines))

    def _service_from_event(self, event) -> str | None:
        opt = getattr(event, "option", None)
        if opt is None:
            return None
        opt_id = getattr(opt, "id", None)
        if opt_id and opt_id in self._service_by_option_id:
            return self._service_by_option_id[opt_id]
        # fallback: prompt text
        return str(getattr(opt, "prompt", "") or "") or None

    @on(OptionList.OptionHighlighted, "#services")
    def service_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        name = self._service_from_event(event)
        if name:
            self._select_service(name)

    @on(OptionList.OptionSelected, "#services")
    def service_selected(self, event: OptionList.OptionSelected) -> None:
        name = self._service_from_event(event)
        if name:
            self._select_service(name)

    @staticmethod
    def _parse_nonneg_int(raw: str, field: str) -> int:
        text = (raw or "").strip()
        if text == "":
            return 0
        # keep digits only (Textual integer inputs can be empty while typing)
        if not text.lstrip("-").isdigit():
            raise ValueError(f"{field} must be a whole number (got {raw!r}).")
        return int(text)

    def _read_form(self) -> tuple[str, datetime, datetime, str]:
        """Must run on the UI thread (not inside @work thread)."""
        if not self._selected:
            raise ValueError("Select a service from the list.")
        name = self.query_one("#name", Input).value.strip()
        if not name:
            raise ValueError("Name is required (short description).")
        start_raw = self.query_one("#start", Input).value.strip()
        start = parse_local_datetime(start_raw, self.cfg.tz)
        days = self._parse_nonneg_int(self.query_one("#days", Input).value, "Days")
        hours = self._parse_nonneg_int(self.query_one("#hours", Input).value, "Hours")
        minutes = self._parse_nonneg_int(self.query_one("#minutes", Input).value, "Minutes")
        if days < 0 or hours < 0 or minutes < 0:
            raise ValueError(
                f"Duration cannot be negative (got {days}d {hours}h {minutes}m)."
            )
        if hours > 23:
            raise ValueError(
                f"Hours must be 0-23 (got {hours}). Use Days for longer downtimes."
            )
        if minutes > 59:
            raise ValueError(
                f"Minutes must be 0-59 (got {minutes})."
            )
        end = duration_to_end(start, days, hours, minutes)
        return self._selected, start, end, name

    def _log_lines(self, lines: list[str], error: bool = False) -> None:
        log = self.query_one("#log", RichLog)
        log.clear()
        style = "red" if error else "green"
        for line in lines:
            if line.startswith("[ok]"):
                log.write(f"[green]{line}[/]")
            elif line.startswith("[skip]"):
                log.write(f"[yellow]{line}[/]")
            elif line.startswith("ERROR"):
                log.write(f"[red]{line}[/]")
            elif line.startswith("Dry-run") or line.startswith("Done"):
                log.write(f"[{style}]{line}[/]")
            else:
                log.write(line)

    def _start_action(self, dry_run: bool) -> None:
        """Validate on UI thread, then call Zabbix on a worker thread."""
        try:
            service, start, end, name = self._read_form()
        except ValueError as e:
            self._log_lines([f"ERROR: {e}"], True)
            self.notify(str(e), severity="error")
            return
        mode = "Dry-run" if dry_run else "Apply"
        self.notify(f"{mode} in progress…")
        self._run_action(dry_run, service, start, end, name)

    @work(exclusive=True, thread=True)
    def _run_action(
        self,
        dry_run: bool,
        service: str,
        start: datetime,
        end: datetime,
        name: str,
    ) -> None:
        try:
            lines = append_exclude(
                self.cfg,
                self.api_url,
                service,
                start,
                end,
                name,
                dry_run=dry_run,
                quiet=True,
            )
            self.call_from_thread(self._log_lines, lines, False)
            self.call_from_thread(
                self.notify,
                "Dry-run OK" if dry_run else "Applied to Zabbix",
                severity="information",
            )
        except Exception as e:
            self.call_from_thread(self._log_lines, [f"ERROR: {e}"], True)
            self.call_from_thread(self.notify, str(e), severity="error")

    @on(Button.Pressed, "#btn-dry")
    def on_dry(self) -> None:
        self._start_action(dry_run=True)

    @on(Button.Pressed, "#btn-apply")
    def on_apply(self) -> None:
        self._start_action(dry_run=False)

    @on(Button.Pressed, "#btn-quit")
    def on_quit_btn(self) -> None:
        self.exit()

    def action_dry_run(self) -> None:
        self._start_action(dry_run=True)

    def action_apply(self) -> None:
        self._start_action(dry_run=False)


def main() -> int:
    env_path = ENV_PATH
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        env_path = Path(sys.argv[1])
    try:
        cfg = load_config(env_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not cfg.zabbix_address or not cfg.zabbix_token or cfg.zabbix_token.startswith("your-"):
        print("ERROR: .env must define real zabbix-address and zabbix-token.", file=sys.stderr)
        return 1
    SlaExcludeApp(cfg, env_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
