"""Шаблоны текстовых сообщений бота (ТЗ раздел 12).

Базовый интерфейс Этапа 1: метрики, 7 Factor Scores, Final Score, Risk Flags с
причинами, история. Полный шаблон КП со статусом BUY/SELL и AI-объяснением —
подэтап 2.4. Каждое сообщение заканчивается дисклеймером (ТЗ раздел 0, 12).
"""
from __future__ import annotations

from collections.abc import Iterable

from app.db.models import Signal
from app.features.indicators import FeatureSet
from app.ingestion.schemas import Fundamentals
from app.scoring import ScoringConfig, compute_factor_scores, load_scoring_config

DISCLAIMER = "⚠️ Не является индивидуальной инвестиционной рекомендацией."

_FACTOR_TITLES = {
    "momentum": "Momentum",
    "growth": "Growth",
    "fundamentals": "Fundamentals",
    "relative_strength": "Rel.Strength",
    "volume": "Volume",
    "valuation": "Valuation",
    "catalysts": "Catalysts",
}

_FLAG_TITLES = {
    "high_volatility": "высокая волатильность",
    "event_risk": "близко отчётность",
    "gap_risk": "ценовой гэп",
    "overextension": "перегрет (отклонение от EMA)",
    "liquidity_risk": "низкая ликвидность",
    "missing_data": "неполные данные",
    "news_risk": "негативные новости",
}


def _fmt(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "н/д"
    return f"{value:.{digits}f}" if digits else f"{value:.0f}"


def _factor_line(signal: Signal) -> str:
    fs = signal.factor_scores
    parts = [f"{_FACTOR_TITLES[name]} {_fmt(getattr(fs, name))}" for name in _FACTOR_TITLES]
    return " · ".join(parts[:4]) + "\n" + " · ".join(parts[4:])


def render_main(signal: Signal) -> str:
    flags = signal.risk_flags or []
    score = f"{signal.final_score}/100" if signal.final_score is not None else "неполный расчёт"
    lines = [f"📊 <b>{signal.ticker}</b> — Final Score: <b>{score}</b>"]
    if signal.price is not None:
        lines.append(f"Цена: ${signal.price:.2f}")
    lines += ["", _factor_line(signal), ""]
    if flags:
        lines.append("⚠️ Risk: " + ", ".join(_FLAG_TITLES.get(f["flag"], f["flag"]) for f in flags))
    else:
        lines.append("✅ Risk: без флагов")
    if signal.final_score is None or any(f["flag"] == "missing_data" for f in flags):
        lines.append("❗ Расчёт неполный: часть данных недоступна (см. кнопку «Risk»).")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_details(signal: Signal, config: ScoringConfig | None = None) -> str:
    """Расшифровка 7 факторов: входные метрики → правило → оценка (из снимка)."""
    cfg = config or load_scoring_config()
    snap = signal.raw_input_snapshot
    features = FeatureSet.model_validate(snap["features"])
    fundamentals = Fundamentals.model_validate(snap["fundamentals"])
    scores = compute_factor_scores(
        features, fundamentals, cfg, news_sentiment=snap.get("news_sentiment")
    )
    lines = [f"🔍 <b>{signal.ticker}</b> — расшифровка факторов:"]
    for name, title in _FACTOR_TITLES.items():
        factor = getattr(scores, name)
        lines.append(f"\n<b>{title}: {_fmt(factor.score)}</b>")
        lines.append(factor.rule)
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_metrics(signal: Signal) -> str:
    snap = signal.raw_input_snapshot
    f = snap["features"]
    fund = snap["fundamentals"]
    lines = [f"📈 <b>{signal.ticker}</b> — исходные метрики:", "", "<b>Технические:</b>"]
    tech = [
        ("Цена", f.get("price"), 2), ("EMA20", f.get("ema20"), 2),
        ("EMA50", f.get("ema50"), 2), ("EMA200", f.get("ema200"), 2),
        ("RSI14", f.get("rsi14"), 1), ("MACD hist", f.get("macd_histogram"), 3),
        ("ATR%", _pct(f.get("atr_pct")), 1), ("Volume Ratio", f.get("volume_ratio20"), 2),
        ("Δ1д %", f.get("price_change_1d"), 2), ("Δ5д %", f.get("price_change_5d"), 2),
        ("Δ20д %", f.get("price_change_20d"), 2),
        ("Rel.Strength 63д %", f.get("relative_strength_63d"), 2),
        ("Gap %", f.get("gap_pct"), 2), ("Dist EMA20 %", f.get("distance_to_ema20"), 2),
    ]
    lines += [f"• {name}: {_fmt(val, d)}" for name, val, d in tech]
    lines += ["", "<b>Фундаментальные:</b>"]
    fnd = [
        ("Revenue Growth", _pct(fund.get("revenue_growth")), 1),
        ("EPS Growth", _pct(fund.get("eps_growth")), 1),
        ("EPS", fund.get("eps"), 2), ("Gross Margin %", _pct(fund.get("gross_margin")), 1),
        ("Debt/Equity", fund.get("debt_equity"), 2), ("P/E", fund.get("pe"), 1),
        ("Forward P/E", fund.get("forward_pe"), 1),
    ]
    lines += [f"• {name}: {_fmt(val, d)}" for name, val, d in fnd]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_risk(signal: Signal) -> str:
    flags = signal.risk_flags or []
    lines = [f"🛡 <b>{signal.ticker}</b> — Risk Filter:"]
    if not flags:
        lines.append("✅ Активных флагов риска нет.")
    else:
        for flag in flags:
            title = _FLAG_TITLES.get(flag["flag"], flag["flag"])
            lines.append(f"• <b>{title}</b>: {flag['reason']}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_history(ticker: str, signals: Iterable[Signal]) -> str:
    signals = list(signals)
    if not signals:
        return f"История по {ticker.upper()} пуста."
    lines = [f"🕓 <b>{ticker.upper()}</b> — последние сигналы:"]
    for s in signals:
        when = s.timestamp.strftime("%Y-%m-%d %H:%M")
        price = f"${s.price:.2f}" if s.price is not None else "н/д"
        score = s.final_score if s.final_score is not None else "н/д"
        lines.append(f"• {when} | {price} | Score {score} | {s.status}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _pct(fraction: float | None) -> float | None:
    """Доля (0.44) → проценты (44.0) для отображения."""
    return None if fraction is None else fraction * 100.0
