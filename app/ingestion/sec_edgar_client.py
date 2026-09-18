"""Клиент SEC EDGAR (ТЗ раздел 6.2).

Без API-ключа, но обязателен заголовок User-Agent в каждом запросе. CIK по
тикеру берётся из company_tickers.json (загружается один раз и кэшируется).
Используется как дополнение к FMP (8-K как катализаторы, сверка fundamentals).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.ingestion.base_client import BaseSourceClient, HealthRecorder, SourceError
from app.ingestion.schemas import Filing

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _now() -> datetime:
    return datetime.now(UTC)


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class SECEdgarClient(BaseSourceClient):
    source = "sec_edgar"

    def __init__(
        self,
        *,
        user_agent: str,
        base_url: str = "https://data.sec.gov",
        timeout: float = 15.0,
        max_retries: int = 3,
        health_recorder: HealthRecorder | None = None,
    ):
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "SEC EDGAR требует корректный User-Agent вида "
                "'project-name contact@email' (ТЗ 6.2)"
            )
        super().__init__(
            source="sec_edgar",
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers={"User-Agent": user_agent, "Accept": "application/json"},
            health_recorder=health_recorder,
        )
        self._cik_map: dict[str, str] | None = None

    async def load_cik_map(self) -> dict[str, str]:
        """Загружает и кэширует справочник тикер → CIK (10-значный)."""
        if self._cik_map is None:
            data = await self._get_json(COMPANY_TICKERS_URL)
            mapping: dict[str, str] = {}
            rows = data.values() if isinstance(data, dict) else data
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ticker = str(row.get("ticker", "")).upper()
                cik = row.get("cik_str")
                if ticker and cik is not None:
                    mapping[ticker] = f"{int(cik):010d}"
            self._cik_map = mapping
        return self._cik_map

    async def get_cik(self, ticker: str) -> str:
        mapping = await self.load_cik_map()
        cik = mapping.get(ticker.upper())
        if cik is None:
            raise SourceError(
                f"sec_edgar: CIK для тикера {ticker} не найден", source=self.source
            )
        return cik

    async def get_recent_filings(
        self, ticker: str, forms: tuple[str, ...] = ("10-K", "10-Q", "8-K")
    ) -> list[Filing]:
        """Последние филинги компании указанных форм."""
        cik = await self.get_cik(ticker)
        data = await self._get_json(f"/submissions/CIK{cik}.json")
        recent = (data.get("filings", {}) or {}).get("recent", {}) or {}
        form_list = recent.get("form", []) or []
        dates = recent.get("filingDate", []) or []
        accessions = recent.get("accessionNumber", []) or []
        fetched = _now()
        result: list[Filing] = []
        for i, form in enumerate(form_list):
            if forms and form not in forms:
                continue
            result.append(
                Filing(
                    ticker=ticker.upper(),
                    form=form,
                    filed_date=_to_date(dates[i]) if i < len(dates) else None,
                    accession_number=accessions[i] if i < len(accessions) else None,
                    source=self.source,
                    fetched_at=fetched,
                )
            )
        return result

    async def get_company_concept(
        self, ticker: str, concept: str, taxonomy: str = "us-gaap"
    ) -> dict[str, Any]:
        """Отдельный XBRL-показатель компании (напр. Revenues)."""
        cik = await self.get_cik(ticker)
        return await self._get_json(
            f"/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
        )
