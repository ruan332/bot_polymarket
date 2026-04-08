from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static, TabbedContent, TabPane

from clima_bot.runtime.engine import ClimaBotEngine


class ClimaBotApp(App[None]):
    CSS = """
    Screen {
        background: #021312;
        color: #9efbf2;
    }
    #hero {
        color: #26f0d0;
        text-style: bold;
        padding: 1 2;
        border: wide #0ea5a0;
        background: #041b1a;
    }
    DataTable {
        height: 1fr;
        border: solid #0b5a56;
        background: #031110;
    }
    RichLog {
        height: 16;
        border: solid #0b5a56;
        background: #020c0b;
    }
    """

    BINDINGS = [
        ("a", "run_analysis", "Analisar"),
        ("s", "sync_once", "Sync"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, engine: ClimaBotEngine) -> None:
        super().__init__()
        self.engine = engine
        self.auto_refresh = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("CLIMA BOT\nSPORT LATENCY ARBITRAGE ENGINE - WEATHER COPYTRADE LIVE", id="hero")
        with TabbedContent():
            with TabPane("1 Analisar Perfis"):
                yield Button("Rodar análise", id="run-analysis")
                yield Button("Aprovar destaque", id="approve-highlight")
                yield DataTable(id="analysis-table")
            with TabPane("2 Configurar Carteiras"):
                yield Input(placeholder="0xwallet", id="wallet-input")
                yield Button("Adicionar", id="add-wallet")
                yield Button("Pausar/Retomar", id="toggle-wallet")
                yield Button("Remover", id="remove-wallet")
                yield Input(value=str(self.engine.settings.clima_bot_copy_trade_fraction), id="fraction-input")
                yield Input(value=str(self.engine.settings.clima_bot_min_notional_usd), id="min-input")
                yield Input(value=str(self.engine.settings.clima_bot_max_notional_usd), id="max-input")
                yield Input(value=str(self.engine.settings.clima_bot_poll_interval_seconds), id="poll-input")
                yield Button("Salvar config", id="save-config")
                yield Button("Reimportar .env", id="reimport-env")
                yield DataTable(id="wallet-table")
            with TabPane("3 Monitor e Logs"):
                yield Button("Sync agora", id="sync-now")
                yield Button("Auto refresh on/off", id="toggle-auto")
                yield Static(id="monitor-summary")
                yield DataTable(id="performance-table")
                yield RichLog(id="log-panel", wrap=True, highlight=True, markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#analysis-table", DataTable).cursor_type = "row"
        self.query_one("#wallet-table", DataTable).cursor_type = "row"
        self.query_one("#performance-table", DataTable).cursor_type = "row"
        self.set_interval(max(self.engine.settings.clima_bot_poll_interval_seconds, 5), self._background_tick)
        await self.refresh_all()

    async def _background_tick(self) -> None:
        if self.auto_refresh:
            await self.engine.sync_once()
            await self.refresh_all()

    async def action_run_analysis(self) -> None:
        await self.engine.run_analysis()
        await self.refresh_all()

    async def action_sync_once(self) -> None:
        await self.engine.sync_once()
        await self.refresh_all()

    async def action_refresh(self) -> None:
        await self.refresh_all()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "run-analysis":
            await self.action_run_analysis()
        elif button_id == "approve-highlight":
            analysis_table = self.query_one("#analysis-table", DataTable)
            if analysis_table.cursor_row is not None and analysis_table.row_count > analysis_table.cursor_row:
                wallet = str(analysis_table.get_row_at(analysis_table.cursor_row)[1])
                self.engine.approve_wallets([wallet])
                await self.refresh_all()
        elif button_id == "add-wallet":
            wallet = self.query_one("#wallet-input", Input).value.strip()
            if wallet:
                self.engine.add_wallet(wallet)
                self.query_one("#wallet-input", Input).value = ""
                await self.refresh_all()
        elif button_id == "toggle-wallet":
            wallet_table = self.query_one("#wallet-table", DataTable)
            if wallet_table.cursor_row is not None and wallet_table.row_count > wallet_table.cursor_row:
                row = wallet_table.get_row_at(wallet_table.cursor_row)
                paused = str(row[4]).lower() == "true"
                self.engine.pause_wallet(str(row[0]), not paused)
                await self.refresh_all()
        elif button_id == "remove-wallet":
            wallet_table = self.query_one("#wallet-table", DataTable)
            if wallet_table.cursor_row is not None and wallet_table.row_count > wallet_table.cursor_row:
                row = wallet_table.get_row_at(wallet_table.cursor_row)
                self.engine.remove_wallet(str(row[0]))
                await self.refresh_all()
        elif button_id == "save-config":
            self.engine.update_runtime_settings(
                {
                    "CLIMA_BOT_COPY_TRADE_FRACTION": self.query_one("#fraction-input", Input).value.strip(),
                    "CLIMA_BOT_MIN_NOTIONAL_USD": self.query_one("#min-input", Input).value.strip(),
                    "CLIMA_BOT_MAX_NOTIONAL_USD": self.query_one("#max-input", Input).value.strip(),
                    "CLIMA_BOT_POLL_INTERVAL_SECONDS": self.query_one("#poll-input", Input).value.strip(),
                }
            )
            await self.refresh_all()
        elif button_id == "reimport-env":
            self.engine.bootstrap_env(overwrite=True)
            await self.refresh_all()
        elif button_id == "sync-now":
            await self.action_sync_once()
        elif button_id == "toggle-auto":
            self.auto_refresh = not self.auto_refresh
            await self.refresh_all()

    async def refresh_all(self) -> None:
        snapshot = await self.engine.dashboard_snapshot()
        self._render_analysis(snapshot)
        self._render_wallets(snapshot)
        self._render_monitor(snapshot)

    def _render_analysis(self, snapshot: dict) -> None:
        table = self.query_one("#analysis-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Rank", "Wallet", "User", "Score", "PnL 30d", "Win Rate", "PF", "DD", "Status")
        tracked = {item["proxy_wallet"] for item in snapshot["wallets"]}
        for candidate in snapshot["analysis"]["candidates"]:
            metrics = candidate["metrics"]
            status = "copying" if candidate["proxy_wallet"] in tracked else ("eligible" if candidate["passed"] else candidate["reject_reason"])
            table.add_row(
                str(candidate["rank"]),
                candidate["proxy_wallet"],
                candidate["user_name"],
                f"{candidate['score']:.2f}",
                f"{float(metrics.get('pnl_30d', 0.0)):.2f}",
                f"{float(metrics.get('win_rate', 0.0)):.2%}",
                f"{float(metrics.get('profit_factor', 0.0)):.2f}",
                f"{float(metrics.get('max_drawdown', 0.0)):.2%}",
                status,
            )

    def _render_wallets(self, snapshot: dict) -> None:
        table = self.query_one("#wallet-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Wallet", "User", "Score", "Active", "Paused", "PnL 30d", "PF")
        for wallet in snapshot["wallets"]:
            metrics = wallet["metrics"]
            table.add_row(
                wallet["proxy_wallet"],
                wallet["user_name"],
                f"{float(wallet.get('score') or 0):.2f}",
                str(bool(wallet.get("active"))),
                str(bool(wallet.get("paused"))),
                f"{float(metrics.get('pnl_30d', 0.0)):.2f}",
                f"{float(metrics.get('profit_factor', 0.0)):.2f}",
            )

    def _render_monitor(self, snapshot: dict) -> None:
        summary = self.query_one("#monitor-summary", Static)
        perf_table = self.query_one("#performance-table", DataTable)
        log_panel = self.query_one("#log-panel", RichLog)
        perf_table.clear(columns=True)
        perf_table.add_columns("Wallet", "Orders", "Exec", "Win", "PnL", "DD", "Open", "Last")
        performance_snapshots = snapshot["performance"]["snapshots"]
        for wallet in snapshot["wallets"]:
            perf = performance_snapshots.get(wallet["proxy_wallet"], {})
            perf_table.add_row(
                wallet["proxy_wallet"],
                str(int(perf.get("orders", 0))),
                f"{float(perf.get('execution_rate', 0.0)):.2%}",
                f"{float(perf.get('win_rate', 0.0)):.2%}",
                f"{float(perf.get('realized_pnl_window', 0.0)):.2f}",
                f"{float(perf.get('max_drawdown', 0.0)):.2%}",
                str(int(perf.get("open_positions", 0))),
                str(perf.get("last_order_at") or "-"),
            )
        summary.update(
            "\n".join(
                [
                    f"MODE: {snapshot['mode'].upper()} | STATUS: {snapshot['engine']['status_text']} | AUTO: {self.auto_refresh}",
                    f"BANKROLL: {snapshot['bankroll_base_usd']:.2f} | PER_TRADE_LIMIT: {snapshot['per_trade_limit_usd']:.2f}",
                    f"ACTIVE_WALLETS: {snapshot['performance']['active_wallets']} | OPEN_POSITIONS: {snapshot['performance']['open_positions']}",
                    f"COPY_FRACTION: {float(snapshot['copy_trade_fraction']):.2%} | RANGE: {float(snapshot['min_notional_usd']):.2f}..{float(snapshot['max_notional_usd']):.2f}",
                ]
            )
        )
        log_panel.clear()
        for item in snapshot["logs"][-150:]:
            line = f"{item['created_at']} [{item['level'].upper()}] {item['tag']} | {item['title']}"
            if item["detail"]:
                line += f" | {item['detail']}"
            if item["meta"]:
                line += f" | {item['meta']}"
            log_panel.write(line)
