# polyester_tests — тесты API биржи Polyester (облачная сессия)

Читается в начале каждой сессии. Основной проект автора — локальный
`polyester_bot` (не здесь). Здесь только тесты API тестнета Polyester для
отчётов команде биржи (расчёт на признание к мейннету).

## Кто и как

- Автор — Snovin, не программист. Отвечать по-русски, коротко, без жаргона.
- Ключи — в переменных окружения облака: `POLYESTER_BASE_URL`
  (`https://api.testnet.polyester.com`), `POLYESTER_API_KEY_ID`,
  `POLYESTER_API_PRIVATE_KEY`. В код и в git не писать никогда. `.env` в
  `.gitignore`.
- Клиент: `api_client.py` (REST, подпись Ed25519). `PolyesterClient()` берёт
  ключи из окружения. Сырой вызов: `c._request("POST", path, body=...)`.
  Ошибка API — `PolyesterApiError`. Зависимости: `pip install -r requirements.txt`.
- Аккаунт общий с ботом и скриптом активности автора. Снимать ТОЛЬКО свои
  ордера/триггеры по id, которые создал тест. `cancel-all` и массовую отмену
  не вызывать. Ордера — маленькие (~5–10 USDT), лимитки далеко от рынка
  (50 % ниже), чтобы не исполнились. В `finally` — уборка своего.
- Скрипты тестов — `tests/bugtestN_*.py`, итоги и сырые ответы — `docs/`.
  Коммит после каждого законченного шага, по-русски.
- Документация биржи — `docs/polyester_docs/` (весь сайт docs: api-docs,
  developer-docs, sdk, user-docs; текст + примеры дословно, в шапке адрес и
  время скачивания). Из облака сайт закрыт Cloudflare, поэтому качает
  локальная сессия автора; не хватает страницы или устарела — сказать автору.
- Доки бота (контекст, история проверок и ВСЕХ отправленных отчётов) —
  `docs/bot/journal.md` и `docs/bot/CLAUDE_bot.md`. Журнал большой: искать
  grep'ом, целиком не читать. Перед отчётом — grep по «ОТПРАВЛЕНО».

## Задание (29.09)

1. `POST /v1/orders/replace-batch` против справочника
   (`testnet.polyester.com/docs/api-docs/rest/POST/v1/orders/replace-batch`).
   Тело по справочнику: `{symbol, requestId, subaccountId?, items[{orderId,
   newPriceTicks, newQtyScaled, newClientOrderId, newAttachedRisk}]}`, до 50.
   Что проверить:
   - Поле `newPriceTicks`: в примере справочника обычная цена "0.005", а не
     число тиков. Что принимает API на деле: десятичную цену или тики?
   - `actionTaken AMENDED` в справочнике: «cancels the original order's
     remaining quantity without a successor». В `modify` AMENDED значит
     правку того же ордера (id тот же, qty меньше). Что делает replace-batch
     на деле при уменьшении qty?
   - Границы: пустой `items`, 51 элемент, qty 0, пункт без изменений, один
     ордер дважды в запросе, чужой/несуществующий orderId, повтор requestId.
   - Поля ответа против справочника (`results[]`: itemIndex, oldOrderId,
     replacementOrderId, status, actionTaken, code, error).
2. Граничные значения в триггерах (`POST /v1/triggers`): цены 0, отрицательные,
   больше/меньше рынка, неверный шаг, лишние знаки, пустые/лишние поля,
   трейлинг-дистанция на границах. Коды ошибок — против справочника
   (TRIGGER_PRICE_INVALID, TRAILING_DISTANCE_INVALID и т.д.). Все созданные
   триггеры — DELETE по id в конце.

Итог — `docs/findings_ГГГГ-ММ-ДД.md`: по каждому пункту что в доках, что на
деле, сырой ответ (тело JSON дословно + время UTC + HTTP-код). Не баг — так и
написать.

## Отчёты команде (правила автора)

- Каждое утверждение: живой вызов сейчас + страница справочника + обратная
  проверка («нет поля» — попробовать передать) + сырой ответ биржи.
  Формулировка не шире проверенного. Слабое — выкинуть.
- Текст — английский, под ним полный русский перевод. Строка «Проверено:» —
  чем подтверждён каждый пункт. Одна окончательная версия; правка — всегда
  полный текст целиком.
- Стиль: без шапок, представлений и «I tested…»; сразу факт: эндпоинт, что в
  доках, что на деле, код ошибки. Короткие абзацы, без нумерации, без
  обратных кавычек, без ручных переносов строк (копируется в Telegram).
  Спорное — вопросом «is this by design?».
- В Discord — никаких ссылок (автомодерация банит). Отправляет только автор;
  «отправлено» писать только по его слову.
- В отчёт — только ошибки API/доков, не состояние площадки (ликвидность и т.п.).
- Уже отправлено — не дублировать (список ниже).

## Грабли API (проверено живьём)

- Все числа в REST — десятичные строки (и в /v1/triggers). `str(0.00003)`
  = "3e-05" — API не берёт, форматировать через Decimal.
- `"timeframe"`, не `"interval"`; `"marketIoc"`, не `"market"`; `CANCELED`
  с одной L; `get_order()` оборачивает в `"order"`.
- `clientOrderId` ≤ 36 символов; повтор даже отклонённого → CONFLICT.
- Post-only при пересечении: create принимает, ордер сразу REJECTED
  `POST_ONLY_CROSS`.
- `GET /v1/orders/open`: symbol строкой (массив → 400).
- `batch`: тело `{"requestId", "items": [тело ордера, ...]}`. В
  `api_client.batch_create_orders` баг — шлёт `"orders"`; вызывать сырым
  `_request`.
- `modify`: `symbol`, `requestId`, `orderId`, `newQtyScaled` (ЦЕЛОЕ в
  масштабе пары, ETH: 0.005 = 5000), `newPrice` десятичной строкой. qty вниз →
  AMENDED (id тот же), цена/qty вверх → REPLACED (новый id в `finalOrderId`).
- Триггеры: `POST /v1/triggers {"trigger": {...}}`, GET/DELETE по id,
  `/events`, `/pause`, `/resume` (resume требует symbol, pause его не
  принимает). SELL-триггер без монет на балансе → INSUFFICIENT_FUNDS. BUY
  stop/take требует лимитный child. Трейлинг только SELL. Dead-man и
  cancel-all привязанные TP/SL НЕ снимают — только DELETE.
- Статус пары приходит "ENABLED".
- Аккаунт VIP 1: мейкер 0.08 %, тейкер 0.135 % (`/v1/spot/fee-rates`).
- Стакан у цены тонкий: рыночный ордер ~400 USDT сдвигает цену ~1 %.
- Daily claim и управление ключами — только сессией сайта, не API-ключом.
- MCP polyester не использовать (рыночные данные 403). Справочник REST —
  `docs/polyester_docs/api-docs/rest__<METHOD>__v1__<путь через __>.txt`
  (напр. `rest__POST__v1__orders__replace-batch.txt`); пример тела
  запроса — в разделе «ПРИМЕРЫ» (curl с `--data '`).

## Уже отправлено команде (не дублировать; родственное — как дополнение)

- 503 на `GET /v1/orders/open` (с 26.09) — тикет, ждём.
- Параллельная отмена привязанных TP/SL: 502 Cloudflare (нога остаётся) и
  503 при успехе; на сайте Cancel All TP/SL снимает не всё — подтвердили.
- Пауза привязанного TP/SL → 404 TRIGGER_NOT_FOUND (GET видит RUNNING) —
  «checking».
- Справочник триггеров: цены «scaled» вместо decimal, resume «symbol_id»,
  childOrderId "1", 401/403 у операций «только сессия» — investigating.
- Пример GET /v1/spot/trades не проходит валидацию; terminalAt привязанных
  ног 1970-01-01 — подтверждены.
- Python SDK: цены ×1000 меньше; в доках закреплена a36 и адрес альфы.
- 28.09: MCP рыночные инструменты 403, search_docs пустой, tsSec в мс;
  modify — newPrice не «scaled 1e9», чужой symbol (modify UNKNOWN_SYMBOL,
  cancel снимает), повтор requestId — «reproduced».
- 28.09: Polyester Scan и Flows API (requestFee, tx доставки, шаги, сайт
  Scan) — 29.09 несколько тикетов подтверждены.
- Сняты как не баги: DOGE объём в свечах; open свечи = close прошлой минуты.
