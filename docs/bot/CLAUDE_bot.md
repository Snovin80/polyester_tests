# Polyester — контекст проекта

Читается в начале каждой сессии, поэтому КОРОТКО (цель ≤ 200 строк).
Все подробности — история отчётов, детали проверок, старые срезы — в
`docs/journal.md` (архив прежнего CLAUDE.md). Искать там grep'ом по слову,
целиком не читать. Новое подробное — дописывать в журнал, сюда — только
итог в одну-две строки.

## ГЛАВНОЕ

- **Фокус с 27.09 (решение автора): тестирование биржи Polyester** —
  находить и проверять ошибки API и сайта, отчёты команде (расчёт на
  признание к мейннету). Бот отложен. Инструменты: `research/crash_test.py`,
  `research/cancel_test.py`, баг-тесты в `research/`.
- **Сетка — нерабочая стратегия, в работе не учитываем** (1420 дней:
  SOL −70 %, ETH −38 %, BTC −8 %). Код `core/*` — инфраструктура (клиент,
  данные, исполнение, риск). Советы через призму сетки не давать.
  Стратегия ищется заново на истории (раздел «Исследования»).
- Автор — Snovin, не программист: по-русски, коротко, команды PowerShell
  целиком. Перед любой правкой спрашивать; вопрос — не задание чинить.
- Скрипт ежедневной активности — отдельный проект `Desktop/polyester_activity`
  (свой CLAUDE.md, venv, git). Здесь его не трогать, его тейки не снимать.

## Устройство

- `core/` — `config.py` (все числа + машина состояний), `api_client.py`
  (REST, подпись Ed25519, ретраи, поправка часов), `market_data.py` (свечи,
  сделки, пульс; стакана/спреда в коде НЕТ — решение автора), `indicators.py`,
  `regime.py`, `grid_engine.py`, `order_manager.py`, `risk.py`,
  `process_manager.py`, `scanner.py`, `client_factory.py` (rest|sdk).
- `bot.py` + `webapp/index.html` — Telegram Mini App.
- `sdk_lab/` — версия на Python SDK (свой venv 3.14). Python SDK НЕ
  тестировать, пока не выйдет версия новее 0.1.0a55 (цены ×1000 меньше).
- `research/` — история и полигон (ниже), баг-тесты, `crash_test.py`,
  `cancel_test.py`.
- `core_backup_old/`, `bot.backup.py` — старые копии, не трогать.

## Грабли API (проверено живьём)

- Все числа в REST — десятичные строки (и в /v1/triggers). `str(0.00003)`
  = "3e-05" — API не берёт, отсюда `format_decimal()`.
- `"timeframe"`, не `"interval"`; `"marketIoc"`, не `"market"`; `CANCELED`
  с одной L; `get_order()` оборачивает в `"order"`.
- `clientOrderId` ≤ 36 символов; повтор даже отклонённого → CONFLICT.
- Post-only при пересечении: create принимает, ордер сразу REJECTED
  `POST_ONLY_CROSS` (наш `is_post_only_cross()` поэтому не срабатывает — баг).
- `GET /v1/orders/open`: symbol строкой (массив → 400).
- `batch`: тело `{"requestId", "items": [тело ордера, ...]}` (наш клиент
  шлёт `"orders"` — баг).
- Триггеры: `POST /v1/triggers {"trigger": {...}}`, GET/DELETE по id,
  `/events`, `/pause`, `/resume` (resume требует symbol, pause его не
  принимает). SELL-триггер без монет на балансе → INSUFFICIENT_FUNDS (с
  27.09). BUY stop/take требует лимитный child. Трейлинг только SELL.
  Dead-man и cancel-all привязанные TP/SL НЕ снимают — только DELETE.
- Статус пары приходит "ENABLED" (раньше с префиксом PAIR_STATUS_), бот
  сравнивает `endswith`.
- Свечи без сделок — нулевой объём; бывают выбросы на порядки
  (`filter_anomalous_candles`).
- Балансы: `trading = available + reserved`. Резерв лимитки BUY берётся по
  0.1 % даже на VIP 1 (мейкер 0.08 %) — запас, не баг.
- Аккаунт с 27.09 — VIP 1: мейкер 0.08 %, тейкер 0.135 %. Ставки брать из
  `/v1/spot/fee-rates`, не хардкодить.
- Стакан у цены тонкий: рыночный ордер ~400 USDT сдвигает цену ~1 %.
- Daily claim и управление ключами — только сессией сайта, не API-ключом.
- MCP polyester: с 28.09 рыночные данные и search_docs не работают (403,
  в тикете); цены 25.09 были ×1000 — для данных не использовать. Справочник REST: `testnet.polyester.com/docs/api-docs/rest/<METHOD>/<путь>`
  (WebFetch падает — curl + вырезать теги).
- Контракт API подробно — `docs/journal.md`, раздел «Контракт API».

## Окружение

- `.env`: `POLYESTER_BASE_URL=https://api.testnet.polyester.com`, ключи.
  Альфа `api-devnet.polyester.ai` — устарела (в доках SDK она по умолчанию).
- Средства на funding, на trading переводятся вручную.
- Python бота: `.\venv\Scripts\python.exe` (3.12, 32 бит). В Git Bash
  перед тестами `export PYTHONIOENCODING=utf-8`.
- `research/venv` и `sdk_lab/venv` — Python 3.14, 64 бит.

## Экономия токенов (жёстко)

- Правки точечные, файлы целиком не перечитывать, нужен кусок — grep.
- Разведку по API не вести по одному вызову — скриптом, коротким выводом.
- Отвечать коротко; крупные куски — по одному за сессию.
- Новая несвязанная задача — лучше новая сессия. САМОМУ ПРЕДУПРЕЖДАТЬ
  автора одной строкой «пора открыть новую сессию», когда сессия идёт
  больше дня, сменилась тема или в ней уже было много крупных кусков.
  Перед этим — записать всё важное в CLAUDE.md / журнал / память.

## Правила работы

- После правок `core/`: `selftest_offline.py` и `selftest_execution.py`,
  оба до строки «ВСЕ ... ПРОВЕРКИ ПРОШЛИ». После правок полигона —
  `research/selftest_engine.py`. Новая логика — новый пункт в selftest.
- git: коммит после каждой завершённой правки, по-русски. GitHub
  `Snovin80/polyester_bot` (origin/main) — для облачных сессий; пушить
  только с согласия автора. `.env`, `data/`, `venv/`, `*.sqlite3`,
  `polyester-recovery-codes.txt` — в `.gitignore`.
- Не хардкодить URL и символы. Риск-лимиты — только в `config.py`.
- Крупную работу можно отдавать облачной сессии (claude.ai/code, окружение
  `binance` с доступом к data.binance.vision), здесь — проверять результат.
  У облака свои бонусные кредиты ($100, до 05.11.2026), сначала тратятся
  они, а не лимит тарифа. Облако видит только GitHub — перед задачей пуш.

## Команды

| Зачем | Команда |
|---|---|
| Тесты ядра | `.\venv\Scripts\python.exe selftest_offline.py` и `selftest_execution.py` |
| Краш-тест API (44 проверки) | `.\venv\Scripts\python.exe research\crash_test.py` |
| Массовая отмена: поставить / проверить / убрать | `.\venv\Scripts\python.exe research\cancel_test.py setup` / `check` / `cleanup` |
| Сверка контракта API | `.\venv\Scripts\python.exe daily_probe.py` |
| Тест полигона | `research\venv\Scripts\python.exe research\selftest_engine.py` |
| Старая сетка на полигоне | `research\venv\Scripts\python.exe research\check_grid_old.py SOLUSDT 1420` |
| Бот (туннель ngrok первым, в другом окне) | `.\venv\Scripts\python.exe bot.py` |

Перед `crash_test`/`cancel_test` остановить скрипт активности.

## Исследования

- Хранилище: минутки Binance 12 пар за 6 лет, `data/market/store/` (не в
  git), чтение `research/store.py`. Скачать/собрать:
  `download_binance.py` (~12 мин), `build_store.py` (~3 мин).
- Полигон `research/engine.py` (26.09): лимитки мейкером, рынок тейкером +
  проскальзывание 0.4 %, minNotional 5, резерв как на бирже, проверка денег;
  `Costs.fill` touch/through (tick пока фиксированный 0.01 — для дешёвых
  монет задавать свой). Итог, просадка, сделки, фазы рынка. Проверен:
  повторил старую сетку до копейки.
- СЛЕДУЮЩИЙ ШАГ: новые стратегии на полигоне (лучше облачной сессией).

СЛЕДУЮЩИЙ ШАГ ТЕСТИРОВАНИЯ (после 28.09): граничные значения в триггерах,
replace-batch. Перепроверка отправленного 28.09: `research/mcp_probe.py`,
`research/bugtest7_modify.py`, `research/bugtest7_evidence.py`.
С 29.09 replace-batch и триггеры — в облаке: репо `Snovin80/polyester_tests`
(`Desktop/polyester_tests`, свой CLAUDE.md с заданием; ключи — в переменных
окружения облака). Здесь — проверять результат.

## Отчёты команде Polyester

Правила — в памяти (`polyester-text-checklist`, `report-style-polyester`):
каждое утверждение проверено живьём сейчас + доки + обратная проверка + сырой
ответ биржи; строка «Проверено»; одна версия; правки — всегда полным текстом;
«отправлено» — только по слову автора; в Discord никаких ссылок. В отчёт идут
только ошибки API/кода, не состояние площадки. Полная история — журнал.

Ждём от команды (после их правки — перепроверить):
- 503 на `GET /v1/orders/open` (с 26.09 ~18:00 UTC) — тикет, «update you
  when resolved». Проверка: `crash_test.py`.
- Параллельная отмена привязанных TP/SL: 502 Cloudflare (нога остаётся) и
  503 при успехе; на сайте Cancel All TP/SL снимает не всё — подтвердили
  27.09. Проверка: `cancel_test.py` + параллельный DELETE.
- Пауза привязанного TP/SL → 404 TRIGGER_NOT_FOUND (GET видит RUNNING;
  в справочнике у ноги есть статус PAUSED) — «checking».
- Отчёт 24.09 п.2–5 (справочник триггеров scaled вместо decimal, resume
  «symbol_id», childOrderId "1", 401/403 у «только сессия») — investigating.
- 23.09: пример GET /v1/spot/trades не проходит валидацию; terminalAt
  привязанных ног 1970-01-01 — подтверждены, в тикетах.
- Python SDK цены ×1000 меньше — ждать новую версию SDK.
- Доки Python SDK: закреплена a36 и адрес альфы по умолчанию — «on it».

- 28.09 (отправлено 10:08, MetaCitizen: «reproduced… update you soon»):
  MCP рыночные инструменты «HTTP 403», search_docs пустой, tsSec в мс;
  modify — newPrice не «scaled 1e9», чужой symbol (modify UNKNOWN_SYMBOL /
  cancel снимает), повтор requestId. Проверка: `research/bugtest7_modify.py`.

- 28.09 23:29 — Polyester Scan (облачная сессия, репо polyester_scan):
  Flows API без requestFee / tx доставки / потерянные шаги, сайт Scan
  (ссылки, Export, заглушки, счётчик). 29.09 Sunny P: несколько тикетов
  подтверждены (какие — не сказано). Текст —
  `research/report_2026-09-28_scan.md`.

Сняты как не баги (28.09): DOGE объём в свечах (marketDataVolumeScale=1, по
докам); open свечи = close прошлой минуты — мелочь.

## Наши баги (чинить с разрешения автора)

- `is_post_only_cross()` не срабатывает (post-only приходит REJECTED).
- `batch_create_orders` шлёт `"orders"` вместо `"items"`.
- `daily_probe.py` шумит: ждёт префикс PAIR_STATUS_.
- `flatten_market` при 5xx-then-CONFLICT пишет ложный FAILED.

## За автором

- Создать на сайте API-ключ «только чтение» — проверить права ключа.
- Ввод/вывод USDT через Zipper (Sepolia): деньги двигает автор, я слежу.
