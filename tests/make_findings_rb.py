"""Собирает часть 1 (replace-batch) docs/findings_2026-09-29.md из сырого лога:
тела ответов вставляются дословно, время UTC и HTTP-код — из лога."""
import json
RAW = "docs/raw_replace_batch_2026-09-29.jsonl"
rows = {}
for l in open(RAW, encoding="utf-8"):
    e = json.loads(l)
    rows.setdefault(e["label"], e)  # первый вызов с такой меткой


def raw(label, note=""):
    e = rows[label]
    req = json.dumps(e["request"], ensure_ascii=False)
    if len(req) > 400:
        req = req[:400] + "…(обрезано, полный запрос в raw-логе)"
    body = e["response_raw"]
    if len(body) > 1200:
        body = body[:1200] + "…(обрезано, полный ответ в raw-логе)"
    return (f"`{e['method']} {e['path']}` — {e['utc']} — HTTP {e['http']} — метка «{label}»{note}\n\n"
            f"Запрос: `{req}`\n\nОтвет:\n```\n{body}\n```\n")


def state(label):
    return raw(label)


out = []
w = out.append
w("""# Findings 2026-09-29

Аккаунт общий; ордера — ETH-USDT, BUY 0.005 по 1350 (≈50 % ниже рынка), post-only. Все созданные тестом ордера в конце проверены по id: открытых нет.
Справочник (дословный текст) — `docs/reference/POST_orders_replace-batch.txt`, скачан 29.09 08:44 UTC.
Сырые ответы всех вызовов — `docs/raw_replace_batch_2026-09-29.jsonl` (время UTC, HTTP, тело запроса и ответа). Скрипт — `tests/bugtest1_replace_batch.py`.

## Часть 1. POST /v1/orders/replace-batch

### 1.1 newPriceTicks: десятичная цена или тики

В доках: пример `"newPriceTicks": "0.005"` (строка), тип у `items[]` на странице не описан — только пример.

На деле: принимается десятичная цена строкой. «1340.00» → цена ордера 1340; «1320» → цена 1320 (при шаге цены 0.01 «тики» дали бы 13.20). Число JSON вместо строки → 400.
Не баг: пример справочника соответствует поведению. Замечание только про имя поля: «Ticks», а значение — цена.

""")
w(raw("A1 newPriceTicks string '1340.00'"))
w(state("A1 get succ"))
w(raw("A3 newPriceTicks string '1320' (целое без точки)"))
w(state("A3 get succ"))
w(raw("A2 newPriceTicks JSON number 133000"))
w("""### 1.2 actionTaken AMENDED

В доках (replace-batch, results[].actionTaken): «REPLACED admits a successor. AMENDED cancels the original order's remaining quantity without a successor; keep tracking old_order_id until its terminal state is confirmed. Unspecified for rejected items.»
В доках modify: `finalOrderId` — «Final active order ID; same as old_order_id for amendments.»

На деле в replace-batch при уменьшении qty (5000→4500, →4000) и при увеличении (→6000) всегда `REPLACED`: новый id, старый ордер CANCELED с terminalReason ORDER_REPLACED, преемник WORKING с новым qty. `AMENDED` в replace-batch не пришёл ни разу за все вызовы.
Для сравнения `modify` с тем же уменьшением qty (5000→4500) возвращает `AMENDED`, `finalOrderId` = `oldOrderId`, ордер остаётся тем же и WORKING.
Не проверено: случай «cancel без преемника» из описания AMENDED в replace-batch (нужен частично исполненный ордер; на общем аккаунте не ставил).
Вопрос: is this by design? Одно и то же слово AMENDED в доках означает разное (modify — правка того же ордера; replace-batch — отмена остатка без преемника), а replace-batch при уменьшении qty не делает ни того, ни другого.

""")
w(raw("B1 qty вниз 5000->4500, цена не передана"))
w(state("B1 get old")); w(state("B1 get succ"))
w(raw("B3 qty вверх ->6000"))
w(raw("D4 modify qty вниз 5000->4500"))
w(state("D4 get old"))
w("""### 1.3 Границы

| Случай | На деле |
|---|---|
| пустой `items` и отсутствующий `items` | 400 VALIDATION_ERROR «items: must contain at least 1 item(s)» |
| 50 элементов | 200, 50 результатов (последний itemIndex 49) |
| 51 элемент | 400 BATCH_TOO_LARGE «Batch size exceeds the maximum allowed (50 items).» |
| qty 0 и qty −1000 | 400 VALIDATION_ERROR «items[0].new_qty_scaled: must be greater than 0» |
| пункт только с orderId (нет изменений) | 400 VALIDATION_ERROR «items[0]: at least one patch field must be set» |
| пункт с теми же ценой и qty (1350 / 5000) | 200, REPLACED, новый id, старый CANCELED ORDER_REPLACED |
| один ордер дважды в запросе | 400 VALIDATION_ERROR «each item target must be unique within a batch»; ордер не тронут |
| несуществующий orderId | 200, status REJECTED, item code ORDER_UNKNOWN, oldOrderId «1», replacementOrderId «1» |
| повтор requestId, тот же body | 200, ответ побайтно тот же (тот же batchRequestId и replacementOrderId), второго replace нет |
| повтор requestId, другой body | 409 CONFLICT_IDEMPOTENCY_KEY_REUSE |
| цена 0 и цена −1 | 400 VALIDATION_ERROR «items[0].new_price_ticks: must be greater than 0» |
| цена 1340.001 (лишний знак) | 200, REJECTED, PRICE_TICK_SIZE, ордер не тронут |
| qty 50 (0.00005 ETH) | 200, REJECTED, MIN_QTY, ордер не тронут |
| qty 1000 (0.001 ETH × 1350) | 200, REJECTED, MIN_NOTIONAL, ордер не тронут |
| чужой orderId | не проверял: безопасного чужого ордера нет (аккаунт общий с ботом, чужие ордера трогать нельзя) |

Не баги: пустой items, 50/51, qty 0, дубль, повтор requestId — всё соответствует доксам или им не противоречит (коды BATCH_TOO_LARGE и CONFLICT_IDEMPOTENCY_KEY_REUSE в списке кодов справочника есть).
Замечание к докам: в таблице «Possible errors» только 401, 403, 400, 404, 503 — статуса 409 (повтор requestId с другим body) там нет.

""")
w(raw("C1 пустой items")); w(raw("C4 51 элемент (несуществующие id)"))
w(raw("C5 qty 0")); w(raw("C7 пункт без изменений (только orderId)"))
w(raw("C8 пункт с теми же цена/qty (1350 / 5000)"))
w(raw("C9 один ордер дважды (qty 4000 и 3000)"))
w(raw("C10 несуществующий orderId"))
w(raw("C11a requestId первый раз")); w(raw("C11b тот же requestId, тот же body"))
w(raw("C11c тот же requestId, другой body (qty 3000)"))
w(raw("C12 цена 0")); w(raw("C14 лишний знак цены 1340.001"))
w(raw("C15 qty ниже минимума (50 = 0.00005 ETH)"))
w(raw("C16 notional ниже минимума (qty 1000 = 0.001 ETH * 1350)"))
w("""### 1.4 Пункт без изменений меняет id

Запрос с теми же ценой и qty, что у ордера (C8), не отклоняется, а выполняется как REPLACED: ордер получает новый id, старый отменяется. Пункт вообще без полей (C7) — 400.
Вопрос: is this by design? (замена «вхолостую» меняет id ордера.) Влияние на место в очереди не проверял.

""")
w("""### 1.5 Приём ≠ результат: преемник может быть отклонён после ADMITTED

В доках: «Replace up to 50 orders … return an admission receipt for status and private realtime reconciliation» — то есть ответ это квитанция о приёме.
На деле: замена post-only ордера на цену выше лучшего ask возвращает 200, ADMITTED, REPLACED и replacementOrderId; затем преемник получает REJECTED с terminalReason POST_ONLY_CROSS, а исходный ордер остаётся WORKING.
Не баг: соответствует описанию «admission receipt». Записано, чтобы не путать с успехом замены.

""")
w(raw("E1 цена 3000 (выше лучшего ask, post-only пересечёт)"))
w(state("E1 get old")); w(state("E1 get succ"))
w("""### 1.6 Поля ответа против справочника

| Поле | В доках | На деле |
|---|---|---|
| acceptedTs | string, пример «2025-01-01T00:00:00Z» | объект `{"seconds":…,"nanos":…}` во всех ответах |
| acceptedTsNs, batchRequestId, status, results[] | есть | есть; status ADMITTED, REJECTED и PARTIALLY_ADMITTED — все три получены |
| acceptedCount, rejectedCount | integer | нет в ответе, когда значение 0 (пример: 50 отклонённых — acceptedCount отсутствует; успешный — rejectedCount отсутствует) |
| results[].itemIndex | «Zero-based index in the request» | у пункта с индексом 0 поля нет; у пункта 1 — `"itemIndex":1` |
| results[].oldOrderId | «Original order targeted by the replacement» | для несуществующего id — «1» (нулевой id в base58), а не переданный id |
| results[].replacementOrderId | «Zero for cancel-only outcomes or rejection before assignment» | при отказе — «1» (то же нулевое значение в base58) |
| results[].clientOrderId | «Client order ID assigned to the successor when available» | есть только если передан newClientOrderId; иначе нет |
| results[].code, error.code | есть | оба есть при отказе (`code` и `error.code`), `error` содержит только `code` |
| results[].actionTaken | AMENDED / REPLACED, «Unspecified for rejected items» | у отклонённых — «UNSPECIFIED»; AMENDED не получен |

Расхождение с доками: acceptedTs — строка в доках, объект на деле.
Вопрос: is this by design? Нулевые значения (itemIndex 0, acceptedCount 0, rejectedCount 0) пропускаются в JSON; в справочнике поля не помечены необязательными, а oldOrderId «1» для неизвестного id не позволяет сопоставить отказ с переданным orderId иначе как по позиции.
Не баги: replacementOrderId «1» при отказе — нулевой id в base58, в доках «Zero…»; clientOrderId отсутствует без newClientOrderId — в доках «when available».

""")
w(raw("D1 два реальных пункта (цена 1340 и qty 4500)"))
w(raw("D2 смешанный: реальный + несуществующий"))
w(raw("D3 newClientOrderId rbt-newcid-0da96d75") if "D3 newClientOrderId rbt-newcid-0da96d75" in rows else "")
w("""## Часть 2. POST /v1/triggers

Не начата.
""")
open("docs/findings_2026-09-29.md", "w", encoding="utf-8").write("\n".join(out))
print("ok", len("\n".join(out)))
