"""Воспроизведение сохранённого расчёта по записи истории (чек-лист 1.7.9, 1.10.3–4).

Берёт сигнал из БД, заново считает всю цепочку из сохранённых исходных данных
(OHLCV тикера и SPY, fundamentals, earnings, новости, filings) по той же версии
формул и печатает сверку «сохранено ↔ пересчитано».

Запуск (на сервере, из контейнера):
    docker compose run --rm app python scripts/replay_signal.py 42
"""

from __future__ import annotations

import argparse
import asyncio

from app.db.repository import get_signal, replay_signal, stored_scoring_config
from app.db.session import create_engine, create_session_factory
from app.scoring.factor_scores import FACTOR_NAMES


async def run(signal_id: int) -> bool:
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            signal = await get_signal(session, signal_id)
    finally:
        await engine.dispose()
    if signal is None:
        raise SystemExit(f"сигнал {signal_id} не найден")

    snap = signal.raw_input_snapshot or {}
    raw = snap.get("raw_inputs") or {}
    cfg = stored_scoring_config(signal)
    replay = replay_signal(signal, cfg)
    weights = cfg.final_score_weights.as_dict()

    print(f"signal_id={signal.id} ticker={signal.ticker} timestamp={signal.timestamp}")
    print(f"formula_version={signal.formula_version} calc_date={raw.get('calc_date')}")
    price = raw.get("price_history") or {}
    bench = raw.get("benchmark") or {}
    print(
        f"inputs: price_bars={len(price.get('bars', []))} ({price.get('source')}), "
        f"benchmark_bars={len(bench.get('bars', []))} ({bench.get('source')}), "
        f"news={len(raw.get('news') or [])}, filings={len(raw.get('filings') or [])}"
    )
    print(f"sources: {snap.get('sources')}")
    print()
    print(f"{'factor':<18}{'weight':>8}{'stored':>8}{'replay':>8}")
    ok = True
    for name in FACTOR_NAMES:
        stored = getattr(signal.factor_scores, name)
        again = replay.final.factor_scores[name]
        ok &= stored == again
        print(f"{name:<18}{weights[name]:>8.2f}{stored!s:>8}{again!s:>8}")
    ok &= replay.final.final_score == signal.final_score
    print(f"{'FINAL SCORE':<18}{'':>8}{signal.final_score!s:>8}{replay.final.final_score!s:>8}")
    stored_flags = [f["flag"] for f in signal.risk_flags or []]
    replay_flags = replay.risk.flag_names()
    ok &= stored_flags == replay_flags
    print(f"risk_flags stored={stored_flags} replay={replay_flags}")
    for flag in signal.risk_flags or []:
        print(f"  - {flag['flag']}: {flag['reason']}")
    print()
    print("РЕЗУЛЬТАТ: совпадает" if ok else "РЕЗУЛЬТАТ: РАСХОЖДЕНИЕ")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("signal_id", type=int)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.signal_id)) else 1)


if __name__ == "__main__":
    main()
