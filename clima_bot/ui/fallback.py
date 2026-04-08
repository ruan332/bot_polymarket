from __future__ import annotations

import asyncio

from clima_bot.runtime.engine import ClimaBotEngine


class ClimaBotFallbackUI:
    def __init__(self, engine: ClimaBotEngine) -> None:
        self.engine = engine

    async def run(self) -> None:
        while True:
            print("\n" + "=" * 72)
            print("CLIMA BOT :: WEATHER COPYTRADE TERMINAL")
            print("=" * 72)
            print("1) Analisar Perfis")
            print("2) Configurar Carteiras")
            print("3) Monitor e Logs")
            print("0) Sair")
            choice = input("\nEscolha: ").strip()
            if choice == "1":
                await self._analysis_menu()
            elif choice == "2":
                await self._wallet_menu()
            elif choice == "3":
                await self._monitor_menu()
            elif choice == "0":
                return
            else:
                print("Opcao invalida.")

    async def _analysis_menu(self) -> None:
        result = await self.engine.run_analysis()
        print(f"\nRun: {result['run']['run_id']}")
        print(result["run"]["summary"])
        print("-" * 72)
        for candidate in result["candidates"][:10]:
            metrics = candidate["metrics"]
            print(
                f"{candidate['rank']:>2} | {candidate['user_name']:<18} | {candidate['proxy_wallet']:<12} "
                f"| score={candidate['score']:.2f} | pnl30d={float(metrics.get('pnl_30d', 0.0)):.2f} "
                f"| win={float(metrics.get('win_rate', 0.0)):.2%} | dd={float(metrics.get('max_drawdown', 0.0)):.2%}"
            )
        approve = input("\nDigite wallets separadas por virgula para aprovar, ou Enter para voltar: ").strip()
        if approve:
            wallets = [item.strip() for item in approve.split(",") if item.strip()]
            approved = self.engine.approve_wallets(wallets)
            print(f"{len(approved)} carteira(s) aprovadas.")

    async def _wallet_menu(self) -> None:
        while True:
            wallets = self.engine.repository.list_wallets()
            print("\nCarteiras rastreadas:")
            if not wallets:
                print("  nenhuma")
            for wallet in wallets:
                print(
                    f"  {wallet['proxy_wallet']} | {wallet['user_name']} | "
                    f"score={float(wallet.get('score') or 0):.2f} | active={wallet['active']} | paused={wallet['paused']}"
                )
            print("\n[a] adicionar  [p] pausar/retomar  [r] remover  [c] config  [e] reimport env  [v] voltar")
            choice = input("Escolha: ").strip().lower()
            if choice == "a":
                wallet = input("Wallet: ").strip()
                if wallet:
                    self.engine.add_wallet(wallet)
            elif choice == "p":
                wallet = input("Wallet: ").strip()
                current = self.engine.repository.get_wallet(wallet)
                if current is not None:
                    self.engine.pause_wallet(wallet, not bool(current.get("paused")))
            elif choice == "r":
                wallet = input("Wallet: ").strip()
                if wallet:
                    self.engine.remove_wallet(wallet)
            elif choice == "c":
                fraction = input(f"copy fraction [{self.engine.settings.clima_bot_copy_trade_fraction}]: ").strip()
                min_notional = input(f"min notional [{self.engine.settings.clima_bot_min_notional_usd}]: ").strip()
                max_notional = input(f"max notional [{self.engine.settings.clima_bot_max_notional_usd}]: ").strip()
                poll = input(f"poll seconds [{self.engine.settings.clima_bot_poll_interval_seconds}]: ").strip()
                updates = {}
                if fraction:
                    updates["CLIMA_BOT_COPY_TRADE_FRACTION"] = fraction
                if min_notional:
                    updates["CLIMA_BOT_MIN_NOTIONAL_USD"] = min_notional
                if max_notional:
                    updates["CLIMA_BOT_MAX_NOTIONAL_USD"] = max_notional
                if poll:
                    updates["CLIMA_BOT_POLL_INTERVAL_SECONDS"] = poll
                if updates:
                    self.engine.update_runtime_settings(updates)
            elif choice == "e":
                result = self.engine.bootstrap_env(overwrite=True)
                print(result)
            elif choice == "v":
                return
            else:
                print("Opcao invalida.")

    async def _monitor_menu(self) -> None:
        while True:
            snapshot = await self.engine.dashboard_snapshot()
            perf = snapshot["performance"]
            print("\n" + "-" * 72)
            print(
                f"modo={snapshot['mode']} | bankroll={snapshot['bankroll_base_usd']:.2f} | "
                f"per_trade_limit={snapshot['per_trade_limit_usd']:.2f} | active_wallets={perf['active_wallets']} | "
                f"open_positions={perf['open_positions']}"
            )
            print("-" * 72)
            for wallet in snapshot["wallets"]:
                row = perf["snapshots"].get(wallet["proxy_wallet"], {})
                print(
                    f"{wallet['proxy_wallet']} | orders={int(row.get('orders', 0))} | exec={float(row.get('execution_rate', 0.0)):.2%} "
                    f"| win={float(row.get('win_rate', 0.0)):.2%} | pnl={float(row.get('realized_pnl_window', 0.0)):.2f} "
                    f"| dd={float(row.get('max_drawdown', 0.0)):.2%}"
                )
            print("\nLogs recentes:")
            for item in snapshot["logs"][-12:]:
                print(f"{item['created_at']} [{item['level']}] {item['tag']} | {item['title']} | {item['detail']}")
            print("\n[s] sync agora  [a] auto loop  [v] voltar")
            choice = input("Escolha: ").strip().lower()
            if choice == "s":
                result = await self.engine.sync_once()
                print(result["sync"])
            elif choice == "a":
                cycles = input("Quantos ciclos? [5]: ").strip() or "5"
                delay = input(f"Delay em segundos? [{self.engine.settings.clima_bot_poll_interval_seconds}]: ").strip()
                total = max(int(cycles), 1)
                seconds = int(delay) if delay else int(self.engine.settings.clima_bot_poll_interval_seconds)
                for index in range(total):
                    result = await self.engine.sync_once()
                    print(f"ciclo {index + 1}/{total}: {result['sync']}")
                    if index + 1 < total:
                        await asyncio.sleep(seconds)
            elif choice == "v":
                return
            else:
                print("Opcao invalida.")
