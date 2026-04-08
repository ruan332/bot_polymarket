from __future__ import annotations

from types import SimpleNamespace

from core.config import load_crypto_config
from core.market_connector import MarketConnector

from clima_bot.config.settings import ClimaBotSettings


def build_market_context(settings: ClimaBotSettings) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            live_trading=settings.live_trading,
            polymarket_private_key=settings.polymarket_private_key,
            polymarket_api_key=settings.polymarket_api_key,
            polymarket_api_secret=settings.polymarket_api_secret,
            polymarket_api_passphrase=settings.polymarket_api_passphrase,
            polymarket_funder=settings.polymarket_funder,
            polymarket_signature_type=settings.polymarket_signature_type,
            polymarket_chain_id=settings.polymarket_chain_id,
            polymarket_live_min_usdc_balance=settings.polymarket_live_min_usdc_balance,
            polymarket_live_min_order_size=settings.polymarket_live_min_order_size,
            polymarket_gamma_url=settings.polymarket_gamma_url,
            polymarket_clob_url=settings.polymarket_clob_url,
            polymarket_data_api_url=settings.polymarket_data_api_url,
            polymarket_market_ws=settings.polymarket_market_ws,
        ),
        crypto_config=load_crypto_config(),
    )


class ClimaBotConnector(MarketConnector):
    def __init__(self, settings: ClimaBotSettings) -> None:
        super().__init__(build_market_context(settings))
