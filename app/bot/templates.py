"""Шаблоны текстовых сообщений бота (ТЗ раздел 12).

Базовый интерфейс Этапа 1: метрики, 7 Factor Scores, Final Score, Risk Flags с
причинами, история. Полный шаблон КП со статусом BUY/SELL и AI-объяснением —
подэтап 2.4. Каждое сообщение заканчивается дисклеймером (ТЗ раздел 0, 12).

Бот отправляет сообщения в режиме parse_mode=HTML, поэтому любой текст не из
шаблона (заголовки новостей, названия источников, причины флагов с «<», профиль
компании) проходит через ``_e`` (html.escape).
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.models import Signal
from app.db.repository import factor_scores_from_snapshot, stored_scoring_config
from app.scoring import ScoringConfig

_NEW_YORK = ZoneInfo("America/New_York")
# Котировка старше этого срока (выходные + праздник) помечается как устаревшая.
QUOTE_STALE_AFTER = timedelta(days=4)

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


def _e(value: object) -> str:
    """Экранирование внешнего текста для Telegram HTML."""
    return html.escape(str(value), quote=False)


def _fmt(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "н/д"
    return f"{value:.{digits}f}" if digits else f"{value:.0f}"


def _factor_line(signal: Signal) -> str:
    fs = signal.factor_scores
    parts = [f"{_FACTOR_TITLES[name]} {_fmt(getattr(fs, name))}" for name in _FACTOR_TITLES]
    return " · ".join(parts[:4]) + "\n" + " · ".join(parts[4:])


def _source_line(signal: Signal) -> str:
    sources = (signal.raw_input_snapshot or {}).get("sources", {})
    names: set[str] = set()
    for value in sources.values():
        if isinstance(value, dict):
            if value.get("sources"):
                names.update(str(item) for item in value["sources"] if item)
            elif value.get("source") and value.get("mode") not in ("unavailable", "no_data"):
                names.add(str(value["source"]))
        elif isinstance(value, list):  # записи до v1.3
            names.update(str(item) for item in value if item)
    return _e(", ".join(sorted(names)) or "н/д")


_DATASET_TITLES = {
    "price_history": "цены",
    "benchmark": "бенчмарк SPY",
    "fundamentals": "отчётность",
    "earnings": "дата отчётности",
    "news": "новости",
    "filings": "SEC EDGAR",
    "quote": "котировка",
    "profile": "профиль",
}


def _mode_line(signal: Signal) -> str | None:
    sources = (signal.raw_input_snapshot or {}).get("sources", {})
    mode = sources.get("mode")
    if mode is None:
        return None
    if mode == "primary":
        return "Режим данных: основные источники"
    reserve, missing = [], []
    for kind, title in _DATASET_TITLES.items():
        entry = sources.get(kind)
        if not isinstance(entry, dict):
            continue
        if entry.get("mode") == "reserve":
            used = entry.get("sources") or [entry.get("source")]
            reserve.append(f"{title} — {', '.join(str(u) for u in used)}")
        elif entry.get("mode") in ("unavailable", "no_data"):
            missing.append(title + (" (нет данных)" if entry["mode"] == "no_data" else ""))
    if sources.get("news_mode") == "reserve":  # записи до v1.3
        reserve.append("новости — " + ", ".join(sources.get("news") or []))
    parts = []
    if reserve:
        parts.append("резерв: " + "; ".join(reserve))
    if missing:
        parts.append("недоступно: " + ", ".join(missing))
    title = {
        "reserve": "⚠️ Режим данных: РЕЗЕРВНЫЙ",
        "degraded": "⚠️ Режим данных: ЧАСТИЧНЫЙ",
        "unavailable": "⛔ Режим данных: НЕДОСТАТОЧНО ДАННЫХ",
    }.get(mode, f"Режим данных: {mode}")
    return title + (" (" + _e(" · ".join(parts)) + ")" if parts else "")


def _raw_inputs(signal: Signal) -> dict:
    return (signal.raw_input_snapshot or {}).get("raw_inputs") or {}


def _price_lines(signal: Signal) -> list[str]:
    """Текущая котировка и последняя дневная свеча — раздельно, с датами."""
    raw = _raw_inputs(signal)
    quote = raw.get("quote")
    bars = ((raw.get("price_history") or {}).get("bars")) or []
    eod = f"${bars[-1][4]:.2f} ({bars[-1][0]})" if bars else None
    lines: list[str] = []
    if quote and quote.get("price") is not None:
        when = ""
        if quote.get("quote_time"):
            moment = datetime.fromisoformat(quote["quote_time"])
            when = f", {moment.astimezone(_NEW_YORK):%Y-%m-%d %H:%M} ET"
            calc_time = signal.timestamp
            if calc_time.tzinfo is None:
                calc_time = calc_time.replace(tzinfo=UTC)
            if calc_time - moment > QUOTE_STALE_AFTER:
                when += " ⚠️ котировка устарела"
        lines.append(f"Цена: ${quote['price']:.2f} (котировка {_e(quote.get('source'))}{when})")
        if eod:
            lines.append(f"Закрытие EOD: {eod} — по нему считаются индикаторы")
    elif eod:
        lines.append(f"Цена: {eod} — закрытие EOD, текущая котировка недоступна")
    elif signal.price is not None:
        lines.append(f"Цена: ${signal.price:.2f}")
    return lines


def _profile_line(signal: Signal) -> str | None:
    profile = _raw_inputs(signal).get("profile")
    if not profile:
        return None
    parts = [
        profile.get("company_name"),
        profile.get("exchange"),
        profile.get("sector"),
    ]
    text = " · ".join(str(p) for p in parts if p)
    return f"🏢 {_e(text)}" if text else None


_CONFIDENCE_TITLES = {
    "high": "высокая",
    "reduced": "пониженная",
    "insufficient": "недостаточно данных",
}


def _data_quality(signal: Signal) -> dict | None:
    return (signal.raw_input_snapshot or {}).get("data_quality")


def _confidence(signal: Signal) -> str:
    """high | reduced | insufficient; для записей до v1.2 — по флагу missing_data."""
    quality = _data_quality(signal)
    if quality:
        return quality["confidence"]
    if signal.final_score is None:
        return "insufficient"
    flags = signal.risk_flags or []
    return "reduced" if any(f["flag"] == "missing_data" for f in flags) else "high"


def _score_text(signal: Signal) -> str:
    confidence = _confidence(signal)
    if signal.final_score is None:
        return "не рассчитан (недостаточно данных)"
    title = _CONFIDENCE_TITLES[confidence]
    return f"{signal.final_score}/100 · достоверность: {title}"


def render_main(signal: Signal) -> str:
    flags = signal.risk_flags or []
    confidence = _confidence(signal)
    if signal.final_score is None:
        score = "<b>не рассчитан</b> — недостаточно данных"
    else:
        score = (
            f"<b>{signal.final_score}/100</b> · достоверность: "
            f"{_CONFIDENCE_TITLES[confidence]}"
        )
    timestamp = signal.timestamp.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 <b>{_e(signal.ticker)}</b> — Final Score: {score}"]
    profile_line = _profile_line(signal)
    if profile_line:
        lines.append(profile_line)
    lines.append(f"Расчёт: {timestamp} · формула {_e(signal.formula_version)}")
    lines.append(f"Источники: {_source_line(signal)}")
    mode_line = _mode_line(signal)
    if mode_line:
        lines.append(mode_line)
    lines += _price_lines(signal)
    lines += ["", _factor_line(signal), ""]
    if flags:
        lines.append(
            "⚠️ Risk: " + _e(", ".join(_FLAG_TITLES.get(f["flag"], f["flag"]) for f in flags))
        )
    else:
        lines.append("✅ Risk: без флагов")
    quality = _data_quality(signal) or {}
    if signal.final_score is None:
        lines.append("❗ Final Score не выдан. Критичные причины:")
        for reason in quality.get("critical") or ["часть данных недоступна (кнопка «Risk»)"]:
            lines.append(f"  • {_e(reason)}")
    elif confidence == "reduced":
        lines.append("❗ Достоверность пониженная, причины:")
        for reason in quality.get("reduced") or ["часть данных недоступна (кнопка «Risk»)"]:
            lines.append(f"  • {_e(reason)}")
    for warning in _raw_inputs(signal).get("warnings") or []:
        lines.append(f"ℹ️ {_e(warning)}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


_EVIDENCE_KIND = {
    "sec_event": "SEC-событие",
    "sec_report": "SEC-отчётность",
    "news": "новость",
    "filing": "SEC",
}


def render_details(signal: Signal, config: ScoringConfig | None = None) -> str:
    """Расшифровка 7 факторов: входные метрики → правило → оценка (из снимка)."""
    cfg = config or stored_scoring_config(signal)
    snap = signal.raw_input_snapshot
    scores = factor_scores_from_snapshot(snap, cfg)
    weights = cfg.final_score_weights.as_dict()
    lines = [f"🔍 <b>{_e(signal.ticker)}</b> — расшифровка факторов (формула {_e(cfg.version)}):"]
    for name, title in _FACTOR_TITLES.items():
        factor = getattr(scores, name)
        lines.append(f"\n<b>{title}: {_fmt(factor.score)}</b> · вес {weights[name]:.0%}")
        lines.append(_e(factor.rule))
        if name == "catalysts":
            ctx = snap.get("catalysts") or {}
            availability = []
            if ctx.get("news_available") is False:
                availability.append("новости недоступны")
            if ctx.get("filings_available") is False:
                availability.append("SEC EDGAR недоступен")
            lines.append(
                "Источники: "
                + _e(", ".join(ctx.get("sources") or []) or "н/д")
                + f"; новостей: {ctx.get('news_count', 0)}"
                + f"; SEC-событий за окно: {ctx.get('sec_event_count', 0)}"
                + f"; свежих 10-K/10-Q: {ctx.get('recent_report_count', 0)}"
                + (f" ({_e(', '.join(availability))})" if availability else "")
            )
            for evidence in (ctx.get("evidence") or [])[:6]:
                when = evidence.get("date") or "дата н/д"
                kind = _EVIDENCE_KIND.get(evidence.get("kind"), evidence.get("kind"))
                score = evidence.get("score")
                mark = "" if score is None else f" [{score:+.0f}"
                if mark and evidence.get("weight") is not None:
                    mark += f", вес {evidence['weight']:.2f}"
                mark += "]" if mark else ""
                lines.append(
                    f"• {_e(when[:10])} · {_e(kind)} · {_e(evidence.get('source', 'н/д'))} · "
                    f"{_e(evidence.get('title', 'н/д'))}{mark}"
                )
    final_rule = (snap.get("calculation") or {}).get("final_rule")
    if final_rule:
        lines += ["", f"<b>Final Score</b> = {_e(final_rule)}"]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_metrics(signal: Signal) -> str:
    snap = signal.raw_input_snapshot
    f = snap["features"]
    fund = snap["fundamentals"]
    lines = [f"📈 <b>{_e(signal.ticker)}</b> — исходные метрики:", "", "<b>Технические:</b>"]
    tech = [
        ("Закрытие EOD", f.get("price"), 2),
        ("EMA20", f.get("ema20"), 2),
        ("EMA50", f.get("ema50"), 2),
        ("EMA200", f.get("ema200"), 2),
        ("RSI14", f.get("rsi14"), 1),
        ("MACD hist", f.get("macd_histogram"), 3),
        ("ATR%", _pct(f.get("atr_pct")), 1),
        ("Volume Ratio", f.get("volume_ratio20"), 2),
        ("Δ1д %", f.get("price_change_1d"), 2),
        ("Δ5д %", f.get("price_change_5d"), 2),
        ("Δ20д %", f.get("price_change_20d"), 2),
        ("Rel.Strength 63д %", f.get("relative_strength_63d"), 2),
        ("Gap %", f.get("gap_pct"), 2),
        ("Dist EMA20 %", f.get("distance_to_ema20"), 2),
    ]
    lines += [f"• {name}: {_fmt(val, d)}" for name, val, d in tech]
    lines += ["", "<b>Фундаментальные:</b>"]
    fnd = [
        ("Revenue Growth", _pct(fund.get("revenue_growth")), 1),
        ("EPS Growth", _pct(fund.get("eps_growth")), 1),
        ("EPS", fund.get("eps"), 2),
        ("Gross Margin %", _pct(fund.get("gross_margin")), 1),
        ("Debt/Equity", fund.get("debt_equity"), 2),
        ("P/E", fund.get("pe"), 1),
        ("Forward P/E", fund.get("forward_pe"), 1),
    ]
    lines += [f"• {name}: {_fmt(val, d)}" for name, val, d in fnd]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_risk(signal: Signal) -> str:
    flags = signal.risk_flags or []
    lines = [
        f"🛡 <b>{_e(signal.ticker)}</b> — Risk Filter",
        f"Достоверность расчёта: {_CONFIDENCE_TITLES[_confidence(signal)]}",
    ]
    if not flags:
        lines.append("✅ Активных флагов риска нет.")
    else:
        for flag in flags:
            title = _FLAG_TITLES.get(flag["flag"], flag["flag"])
            lines.append(f"• <b>{_e(title)}</b>: {_e(flag['reason'])}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_history(ticker: str, signals: Iterable[Signal]) -> str:
    signals = list(signals)
    if not signals:
        return f"История по {_e(ticker.upper())} пуста."
    lines = [f"🕓 <b>{_e(ticker.upper())}</b> — последние сигналы:"]
    for s in signals:
        when = s.timestamp.strftime("%Y-%m-%d %H:%M")
        price = f"${s.price:.2f}" if s.price is not None else "н/д"
        score = _score_text(s)
        # Торговый статус формирует Rule Engine (Этап 2); до него в истории
        # показываются только Score и риск, без BUY/WATCH/SELL.
        lines.append(f"• {when} UTC | {price} | Score {score} | {_risk_summary(s)}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _risk_summary(signal: Signal) -> str:
    flags = signal.risk_flags or []
    if not flags:
        return "Risk: без флагов"
    return "Risk: " + _e(", ".join(_FLAG_TITLES.get(f["flag"], f["flag"]) for f in flags))


def _pct(fraction: float | None) -> float | None:
    """Доля (0.44) → проценты (44.0) для отображения."""
    return None if fraction is None else fraction * 100.0
