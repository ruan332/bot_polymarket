from __future__ import annotations

from core.schemas import PortfolioSummary
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.app_context import AppContext


class PortfolioService:
    def __init__(self, context: AppContext):
        self.context = context

    async def summary(self) -> PortfolioSummary:
        return await self.context.repository.get_portfolio_summary()
