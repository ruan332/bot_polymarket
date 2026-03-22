from __future__ import annotations

import re
from dataclasses import dataclass

from core.config import CryptoSettings
from core.utils import sanitize_text, stable_hash


ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ethereum"),
    "SOL": ("sol", "solana"),
    "XRP": ("xrp", "ripple"),
    "DOGE": ("doge", "dogecoin"),
    "ADA": ("ada", "cardano"),
    "AVAX": ("avax", "avalanche"),
    "LINK": ("link", "chainlink"),
    "DOT": ("dot", "polkadot"),
    "LTC": ("ltc", "litecoin"),
    "BNB": ("bnb", "binance coin"),
    "SUI": ("sui",),
    "APT": ("apt", "aptos"),
    "ARB": ("arb", "arbitrum"),
    "OP": ("op", "optimism"),
    "ATOM": ("atom", "cosmos"),
    "NEAR": ("near",),
    "PEPE": ("pepe",),
    "TRX": ("trx", "tron"),
    "UNI": ("uni", "uniswap"),
}

ASSET_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "XRP",
    "DOGE": "Dogecoin",
    "ADA": "Cardano",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "DOT": "Polkadot",
    "LTC": "Litecoin",
    "BNB": "BNB",
    "SUI": "Sui",
    "APT": "Aptos",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "ATOM": "Cosmos",
    "NEAR": "Near",
    "PEPE": "Pepe",
    "TRX": "Tron",
    "UNI": "Uniswap",
}

INDIRECT_KEYWORDS = {
    "etf",
    "sec",
    "regulation",
    "regulatory",
    "listing",
    "listed",
    "hack",
    "exploit",
    "airdrop",
    "adoption",
    "treasury",
    "reserve",
}
CRYPTO_BROAD_KEYWORDS = {
    "crypto",
    "cryptocurrency",
    "cryptocurrencies",
    "digital asset",
    "digital assets",
    "blockchain",
    "defi",
    "stablecoin",
    "stablecoins",
    "altcoin",
    "altcoins",
    "memecoin",
    "memecoins",
    "token",
    "tokens",
    "web3",
}
WEAK_CONTEXT_KEYWORDS = {
    "stock",
    "stocks",
    "tesla",
    "trump",
    "president",
    "fed",
    "cpi",
    "movie",
    "game",
    "sports",
}
THEMATIC_EVENT_KEYWORDS = {
    "gta",
    "grand theft auto",
    "super bowl",
    "world cup",
    "olympics",
    "oscar",
    "election",
    "inauguration",
    "wwdc",
}
SYNTHETIC_ASSET_SYMBOL = "CRYPTO"
SYNTHETIC_ASSET_NAME = "Crypto"
DIRECT_MARKET_KEYWORDS = {
    "above",
    "below",
    "between",
    "price",
    "trading at",
    "close above",
    "close below",
    "hit",
    "reach",
}
CALENDAR_MARKER_PATTERN = re.compile(
    r"\b("
    r"today|tonight|tomorrow|this week|this month|this quarter|this year|"
    r"end of (the )?(day|week|month|quarter|year)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
    r"aug|august|sep|sept|september|oct|october|nov|november|dec|december|"
    r"q[1-4]|20\d{2}|\d{1,2}/\d{1,2}"
    r")\b"
)


@dataclass
class CryptoMarketCandidate:
    asset_symbol: str
    asset_name: str
    crypto_tier: str
    market_kind: str
    question_type: str
    thesis_tags: list[str]
    thesis_hash: str


@dataclass
class CryptoClassificationResult:
    candidate: CryptoMarketCandidate | None
    rejection_reason: str | None = None


def detect_asset_symbols(question: str, description: str = "") -> list[str]:
    text = f"{question} {description}".lower()
    matches: list[str] = []
    for symbol, aliases in ASSET_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias.lower())}\b", text):
                matches.append(symbol)
                break
    return sorted(set(matches))


def detect_asset_symbol(question: str, description: str = "") -> str | None:
    unique = detect_asset_symbols(question, description)
    if len(unique) == 1:
        return unique[0]
    return None


def classify_crypto_market(question: str, description: str, config: CryptoSettings) -> CryptoMarketCandidate | None:
    return classify_crypto_market_with_reason(question, description, config).candidate


def classify_crypto_market_with_reason(
    question: str,
    description: str,
    config: CryptoSettings,
) -> CryptoClassificationResult:
    symbols = detect_asset_symbols(question, description)
    text = f"{question} {description}".lower()
    if any(keyword in text for keyword in THEMATIC_EVENT_KEYWORDS):
        return CryptoClassificationResult(candidate=None, rejection_reason="thematic_horizon")
    if "before" in text and not CALENDAR_MARKER_PATTERN.search(text):
        return CryptoClassificationResult(candidate=None, rejection_reason="thematic_horizon")
    has_direct_trigger = any(keyword in text for keyword in DIRECT_MARKET_KEYWORDS)
    has_indirect_trigger = any(keyword in text for keyword in INDIRECT_KEYWORDS)
    has_broad_crypto_keyword = any(keyword in text for keyword in CRYPTO_BROAD_KEYWORDS)
    has_crypto_anchor = bool(symbols) or has_broad_crypto_keyword
    if not has_crypto_anchor:
        return CryptoClassificationResult(candidate=None, rejection_reason="missing_crypto_anchor")
    if not has_direct_trigger and not has_indirect_trigger and not has_broad_crypto_keyword:
        return CryptoClassificationResult(candidate=None, rejection_reason="weak_crypto_signal")

    symbol: str
    asset_name: str
    market_kind: str
    if len(symbols) == 1 and has_direct_trigger and not has_indirect_trigger:
        symbol = symbols[0]
        asset_name = ASSET_NAMES.get(symbol, symbol)
        market_kind = "direct_coin"
    else:
        if not has_indirect_trigger and len(symbols) <= 1 and any(keyword in text for keyword in WEAK_CONTEXT_KEYWORDS):
            return CryptoClassificationResult(candidate=None, rejection_reason="weak_crypto_signal")
        market_kind = "indirect_crypto"
        if config.direct_coin_only:
            return CryptoClassificationResult(candidate=None, rejection_reason="indirect_market_disabled")
        if len(symbols) == 1:
            symbol = symbols[0]
            asset_name = ASSET_NAMES.get(symbol, symbol)
        else:
            symbol = SYNTHETIC_ASSET_SYMBOL
            asset_name = SYNTHETIC_ASSET_NAME

    tier = "btc" if symbol == "BTC" else ("major" if symbol in {item.upper() for item in config.major_assets} else "small_cap")
    question_type = classify_question_type(question, description)
    thesis_tags = [symbol.lower(), tier, question_type, market_kind]
    thesis_hash = stable_hash(f"{symbol}|{market_kind}|{sanitize_text(question, 160)}|{question_type}", length=16)
    return CryptoClassificationResult(
        candidate=CryptoMarketCandidate(
            asset_symbol=symbol,
            asset_name=asset_name,
            crypto_tier=tier,
            market_kind=market_kind,
            question_type=question_type,
            thesis_tags=thesis_tags,
            thesis_hash=thesis_hash,
        )
    )


def classify_question_type(question: str, description: str = "") -> str:
    text = f"{question} {description}".lower()
    if "between" in text or "range" in text:
        return "range"
    if "above" in text or "over" in text or "reach" in text or "hit" in text:
        return "upside_target"
    if "below" in text or "under" in text:
        return "downside_target"
    return "direction"
