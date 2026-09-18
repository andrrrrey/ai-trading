# Подэтап 1.1 — артефакт проверки DoD

Дата: 2026-09-18T16:43:12Z

## pytest (контрактные тесты клиентов и source_router)
```
........................                                                 [100%]
24 passed in 3.54s
```

## ruff (линтер)
```
All checks passed!
```

## FastAPI /health
```
GET /health -> 200 {'status': 'ok', 'version': '0.1.0'}
```

## Статус DoD 1.1
- [x] Скаффолдинг репозитория (структура ТЗ раздел 4).
- [x] Клиенты FMP / SEC EDGAR / Alpaca / Finnhub + source_router.
- [x] Каждый клиент проставляет source и fetched_at в DTO.
- [x] Переключение FMP→Alpaca (цены) и FMP→Finnhub (новости) при сбое — тесты test_source_router.py.
- [x] docker-compose.yml (app + postgres), Dockerfile — приложение поднимается, /health отвечает.
- [ ] ОТЛОЖЕНО до ключей (Stage 0): реальный прогон по AAPL и живое переключение источников на настоящем API.
