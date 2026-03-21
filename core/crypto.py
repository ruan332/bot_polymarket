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


def detect_asset_symbol(question: str, description: str = "") -> str | None:
    text = f"{question} {description}".lower()
    matches: list[str] = []
    for symbol, aliases in ASSET_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias.lower())}\b", text):
                matches.append(symbol)
                break
    unique = sorted(set(matches))
    if len(unique) != 1:
        return None
    return unique[0]


def classify_crypto_market(question: str, description: str, config: CryptoSettings) -> CryptoMarketCandidate | None:
    return classify_crypto_market_with_reason(question, description, config).candidate


def classify_crypto_market_with_reason(
    question: str,
    description: str,
    config: CryptoSettings,
) -> CryptoClassificationResult:
    symbol = detect_asset_symbol(question, description)
    if symbol is None:
        return CryptoClassificationResult(candidate=None, rejection_reason="asset_not_detected")

    text = f"{question} {description}".lower()
    if config.direct_coin_only and any(keyword in text for keyword in INDIRECT_KEYWORDS):
        return CryptoClassificationResult(candidate=None, rejection_reason="indirect_keyword")
    if not any(keyword in text for keyword in DIRECT_MARKET_KEYWORDS):
        return CryptoClassificationResult(candidate=None, rejection_reason="missing_direct_trigger")
    if any(keyword in text for keyword in THEMATIC_EVENT_KEYWORDS):
        return CryptoClassificationResult(candidate=None, rejection_reason="thematic_horizon")
    if "before" in text and not CALENDAR_MARKER_PATTERN.search(text):
        return CryptoClassificationResult(candidate=None, rejection_reason="thematic_horizon")

    tier = "btc" if symbol == "BTC" else ("major" if symbol in {item.upper() for item in config.major_assets} else "small_cap")
    question_type = classify_question_type(question, description)
    thesis_tags = [symbol.lower(), tier, question_type]
    thesis_hash = stable_hash(f"{symbol}|{sanitize_text(question, 160)}|{question_type}", length=16)
    return CryptoClassificationResult(
        candidate=CryptoMarketCandidate(
            asset_symbol=symbol,
            asset_name=ASSET_NAMES.get(symbol, symbol),
            crypto_tier=tier,
            market_kind="direct_coin",
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
