"""Живой read-only smoke-тест Этапа 1 без вывода секретов.

Запуск из корня проекта после заполнения .env:
    python scripts/stage1_acceptance_smoke.py AAPL
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from app.bot.templates import render_main
from app.config import get_settings
from app.db.models import Base
from app.db.repository import get_signal, recompute_from_snapshot, replay_signal
from app.db.session import create_engine, create_session_factory
from app.ingestion import IngestionService, build_source_router
from app.monitoring.source_health import SourceHealthMonitor
from app.pipeline import Pipeline


async def run(ticker: str) -> None:
    settings = get_settings()
    problems = settings.missing_required("smoke")
    if problems:
        raise SystemExit("Конфигурация неполная:\n- " + "\n- ".join(problems))

    monitor = SourceHealthMonitor()
    router = build_source_router(settings, health_recorder=monitor)
    with TemporaryDirectory(prefix="switch-trading-smoke-") as temp_dir:
        db_path = Path(temp_dir) / "smoke.db"
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        session_factory = create_session_factory(engine)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            ingestion = IngestionService(router)
            market = await ingestion.get_market_data(ticker)
            signal_id = await Pipeline(ingestion, session_factory).run(ticker)
            async with session_factory() as session:
                signal = await get_signal(session, signal_id)
            if signal is None:
                raise RuntimeError("сигнал не сохранён")
            recomputed = recompute_from_snapshot(signal)
            snapshot = signal.raw_input_snapshot or {}
            catalysts = snapshot.get("catalysts") or {}
            print(f"ticker={ticker}")
            print(
                f"price_source={market.price_history.source} bars={len(market.price_history.bars)}"
            )
            print(
                f"fundamentals_source={market.fundamentals.source} "
                f"incomplete={market.fundamentals.is_incomplete}"
            )
            print(
                f"news={catalysts.get('news_count', 0)} "
                f"filings={catalysts.get('filing_count', 0)} "
                f"catalyst_sources={','.join(catalysts.get('sources') or []) or 'none'}"
            )
            print(
                f"signal_id={signal_id} final_score={signal.final_score} "
                f"formula={signal.formula_version} "
                f"risk_flags={','.join(f['flag'] for f in signal.risk_flags) or 'none'}"
            )
            print(f"data_mode={(snapshot.get('sources') or {}).get('mode')}")
            print(f"reproducible={recomputed.final_score == signal.final_score}")
            replay = replay_signal(signal)
            print(
                "replay_from_raw_inputs="
                f"{replay.final.final_score == signal.final_score} "
                f"(bars={len(snapshot['raw_inputs']['price_history']['bars'])}, "
                f"news={len(snapshot['raw_inputs']['news'])})"
            )
            print(f"telegram_render_ok={bool(render_main(signal))}")
            print(f"monitor_sources={','.join(sorted(monitor.snapshot()))}")
        finally:
            await router.aclose()
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", nargs="?", default="AAPL")
    args = parser.parse_args()
    ticker = args.ticker.strip().upper()
    if not ticker.isalpha() or len(ticker) > 5:
        raise SystemExit("тикер должен содержать 1–5 латинских букв")
    asyncio.run(run(ticker))


if __name__ == "__main__":
    main()
