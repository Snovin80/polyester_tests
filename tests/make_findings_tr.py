"""Собирает часть 2 (POST /v1/triggers) и дописывает в docs/findings_2026-09-29.md.
Тела ответов — дословно из docs/raw_triggers_2026-09-29.jsonl."""
import json
RAW = "docs/raw_triggers_2026-09-29.jsonl"
rows = {}
for l in open(RAW, encoding="utf-8"):
    e = json.loads(l)
    rows.setdefault(e["label"], e)


def raw(label, req=True):
    e = rows[label]
    body = e["response_raw"]
    if body.lstrip().startswith("<"):
        t = body.split("<title>")[1].split("</title>")[0] if "<title>" in body else ""
        body = f"[HTML-страница Cloudflare, title: {t}] " + body[:0]
    if len(body) > 900:
        body = body[:900] + "…(обрезано, полный ответ в raw-логе)"
    rq = json.dumps(e["request"], ensure_ascii=False)
    if len(rq) > 500:
        rq = rq[:500] + "…(обрезано)"
    out = f"`{e['method']} {e['path']}` — {e['utc']} — HTTP {e['http']} — «{label}»\n\n"
    if req:
        out += f"Запрос: `{rq}`\n\n"
    return out + f"Ответ:\n```\n{body}\n```\n"


base = open("docs/findings_2026-09-29.md", encoding="utf-8").read()
base = base.split("## Часть 2.")[0].rstrip() + "\n\n"
o = []
w = o.append
w("""## Часть 2. POST /v1/triggers

Справочник (дословный текст) — `docs/reference/POST_triggers.txt`, `DELETE_triggers_trigger_id.txt`, скачаны 29.09 08:44 UTC. Сырые ответы всех вызовов (создание, GET, DELETE, события; около 450 записей) — `docs/raw_triggers_2026-09-29.jsonl`. Скрипт — `tests/bugtest2_triggers.py`.

Как тестировалось: пара AVAX-USDT (на счёте нет ETH, есть AVAX). Дочерний ордер — лимитка post-only далеко от рынка (SELL 22.8, BUY 5.7), трейлинг — с activationPrice далеко выше рынка. Каждый созданный триггер снимался DELETE по своему id. Итог: создано 48 триггеров, 46 CANCELED и 2 COMPLETED (сработали в блоке W, см. 2.2; их дочерние ордера отменены по id). Открытых триггеров и ордеров теста нет.
Что уже отправлено команде и здесь не дублируется: цены «scaled» вместо decimal в справочнике триггеров. Ниже это отмечено как «дополнение».

### 2.1 clientTriggerId: обязателен на деле, но не помечен обязательным; длина; повтор

В доках: `clientTriggerId` — «Client-provided trigger ID for idempotency.», среди полей `trigger` нет пометки required (required только у symbol и внутри стратегий). Пример: «9155001234567».

На деле:
- Без `clientTriggerId` — 400 VALIDATION_ERROR «trigger.client_trigger_id: must be at least 1 characters».
- Длина: 36 и 37 символов — 200; 64 — 200; 100, 128, 129, 200 и 255 — 503 UNAVAILABLE с текстом «read_decode: read tcp 172.17.0.4:…->10.20.10.151:9100: read: connection reset by peer» (воспроизведено 8 из 8 раз; в тексте ошибки виден внутренний адрес). Границу между 64 и 100 не искал.
- Повтор `clientTriggerId` с тем же телом — 200 и тот же triggerId (повторная выдача). Повтор с другим телом (другой qty или другая цена) — 502 Cloudflare (HTML «502: Bad gateway») 5 из 5 раз, без JSON. Код CONFLICT_DUPLICATE_CLIENT_TRIGGER_ID есть в списке кодов справочника (страница replace-batch), но ни разу не пришёл.
Вопросы: is this by design? (1) поле обязательное, а в справочнике не помечено; (2) длинный id даёт 503 вместо 400; (3) повтор с другим телом даёт 502 вместо 409/CONFLICT_DUPLICATE_CLIENT_TRIGGER_ID.

""")
w(raw("F6 без clientTriggerId"))
w(raw("F30 clientTriggerId 37 символов", req=False))
w(raw("R2 clientTriggerId 64 символов", req=False))
w(raw("R2 clientTriggerId 100 символов", req=False))
w(raw("R1.1 clientTriggerId 200 символов", req=False))
w(raw("R3.0 clientTriggerId повтор — первый"))
w(raw("F33b clientTriggerId повтор — второй (тот же body)", req=False))
w(raw("R3.1 тот же clientTriggerId, другой qty"))
w(raw("R3.4 тот же clientTriggerId, другая цена", req=False))
w("""### 2.2 Цена триггера «не с той стороны» рынка: принимается и срабатывает

В доках: в списке кодов ошибок (страница replace-batch) есть TRIGGER_PRICE_INVALID; на странице POST /v1/triggers в «Possible errors» только 401, 403, 400, 404, 503 и нет описания, когда какой код приходит.

На деле: SELL stop-loss выше рынка, SELL take-profit ниже рынка, BUY stop-loss ниже рынка и BUY take-profit выше рынка — все создаются (200) и остаются ARMED. Это 10 из 10 вариантов блока S: 4 штатных, 4 «с неверной стороны» и 2 с ценой ровно по лучшему bid/ask. Направление (BELOW/ABOVE) API вычисляет по типу триггера и стороне, а не по рынку. Два из них (W1 SELL stop-loss @13.707 и W2 BUY stop-loss @9.138 при рынке около 11.4) держал 30 с: оба стали COMPLETED в одну и ту же наносекунду (событие FIRED, firePx 11.422), то есть сработали при следующем изменении цены, и создали дочерние ордера (SELL 22.8 и BUY 5.7), которые я отменил по id. Код TRIGGER_PRICE_INVALID не пришёл ни в одном тесте: цена 0 и отрицательные дают VALIDATION_ERROR «trigger_price_ticks: must be greater than 0».
Не проверено: что было бы с рыночным дочерним ордером (marketIoc) — не рисковал продажей монет на общем аккаунте.
Вопрос: is this by design? Триггер, у которого условие уже выполнено на момент создания, принимается без ошибки и срабатывает сразу. Когда возвращается TRIGGER_PRICE_INVALID?
Не баг: цена 0 / отрицательная / нечисловая → 400 (VALIDATION_ERROR или BAD_REQUEST), ордера и триггеры не создаются.

""")
w(raw("W1 SELL stopLoss ВЫШЕ рынка @13.707 (держим 30 с)"))
w(raw("W1 SELL stopLoss ВЫШЕ рынка poll 9s", req=False))
w(raw("W1 SELL stopLoss ВЫШЕ рынка | DELETE", req=False))
w("""События сработавших триггеров (`GET /v1/triggers/{id}/events`), дословно из проверки 29.09 09:34 UTC (в raw-лог этот вызов не писался, в логе есть GET дочерних ордеров):

```
{"events":[{"childOrderId":"pHnUebWjj5","childSeq":2,"eventType":"FIRED","firePx":"11.422","subaccountId":"RCx3H2SjGz6","symbol":"AVAX-USDT","triggerId":"Zafbqjkj34P","triggerType":"STOP_LOSS","tsNs":"1790674391804064116"}]}
{"events":[{"childOrderId":"d9XUtJFSaCi","childSeq":2,"eventType":"FIRED","firePx":"11.422","subaccountId":"RCx3H2SjGz6","symbol":"AVAX-USDT","triggerId":"4b6gLadxpm3","triggerType":"STOP_LOSS","tsNs":"1790674391804064116"}]}
```

""")
w("""### 2.3 Цена: 0, отрицательная, шаг, лишние знаки, формат

В доках: `triggerPrice` — «Trigger threshold in quote units scaled by 1e9.», пример «0.005». (Расхождение «scaled» / decimal уже отправлено.)

На деле (шаг цены AVAX-USDT 0.001, цена принимается десятичной строкой):
| Значение | Ответ |
|---|---|
| «0», «-1», «-0.001», «» | 400 VALIDATION_ERROR «trigger.stop_loss.trigger_price_ticks: must be greater than 0» |
| «abc», « 10» (пробел), «1e1» | 400 BAD_REQUEST «invalid triggerPrice: invalid syntax» |
| «10.0005» (лишний знак сверх шага) | 400 PRICE_TICK_SIZE «trigger.strategy.stop_loss.trigger_price_ticks must align with tick size 1000000» |
| «10.000000001» (9 знаков) | 400 PRICE_TICK_SIZE (тот же текст) |
| «10.0000000001» (10 знаков) | 400 BAD_REQUEST «invalid triggerPrice: too many fractional digits» |
| «0.000000001» | 400 PRICE_TICK_SIZE |
| «99999999999999999999» | 400 BAD_REQUEST «invalid triggerPrice: overflow» |
| «10.0010» (лишний нуль, значение на шаге) | 200 |
| «.5», «10.», «+10» | 200 (принимаются, хотя «1e1» и « 10» — нет) |
| число JSON 10 вместо строки | 400 BAD_REQUEST «json: cannot unmarshal number into Go struct field … triggerPrice of type string» |
| цена child limitGtc «22.8005» | 400 PRICE_TICK_SIZE «…child.limit_gtc.price_ticks must align with tick size 1000000» |
| цена child «0», «-1» | 400 VALIDATION_ERROR «…child.limit_gtc.price_ticks: must be greater than 0» |

Не баги: отказы для 0, отрицательных, лишних знаков и нечисловых значений соответствуют ожиданиям.
Дополнение к уже отправленному про «scaled»: в тексте ошибки PRICE_TICK_SIZE поле называется `trigger_price_ticks`, а шаг выдан как «1000000» (0.001 × 1e9), тогда как поле в запросе называется `triggerPrice` и принимает десятичную цену.

""")
for lab in ["P1 цена 0", "P6 10.0005 (лишний знак сверх шага 0.001)", "P9 10.0000000001 (10 знаков)",
            "P12 '.5'", "P17 число JSON 10 вместо строки"]:
    w(raw(lab, req=False))
w("""### 2.4 Поля: пустые, лишние, неверные

| Случай | Ответ |
|---|---|
| тело `{}` | 400 VALIDATION_ERROR «trigger is required» |
| `trigger: {}` и без `symbol` | 400 UNKNOWN_SYMBOL «unknown trading pair» |
| без `qty`, qty 0, qty -1 | 400 VALIDATION_ERROR «trigger.qty_scaled: must be greater than 0» |
| qty «abc» | 400 BAD_REQUEST «invalid qty: invalid syntax» |
| qty «0.5137911» (7 знаков) | 400 BAD_REQUEST «invalid qty: too many fractional digits» |
| qty число JSON 0.5 | 400 BAD_REQUEST «json: cannot unmarshal number into Go struct field … qty of type string» |
| нет ни одной стратегии | 400 VALIDATION_ERROR «trigger.strategy: exactly one field is required in oneof» |
| stopLoss и takeProfit вместе | 400 BAD_REQUEST «exactly one of ladder or stopLoss or takeProfit or trailingStop or twap must be set» |
| лишнее поле (в trigger, на верхнем уровне, в stopLoss) | 400 BAD_REQUEST «json: unknown field "foo"» (все три случая) |
| несуществующий symbol «XXX-USDT» | 400 UNKNOWN_SYMBOL |
| symbol в нижнем регистре «avax-usdt» | 200 (принимается) |
| неверный feeAsset, selfTradePreventionMode, side | 400 BAD_REQUEST «invalid …: unknown enum value …» |
| без side | 400 VALIDATION_ERROR «trigger.stop_loss.side: must not be in list» |
| без child; child `{}`; два вида child сразу | 400 VALIDATION_ERROR / BAD_REQUEST с понятным текстом |
| BUY stop-loss с marketIoc child | 400 VALIDATION_ERROR «BUY stop-loss and take-profit triggers require a limit child» |
| SELL с feeAsset BASE | 400 FEE_ASSET_NOT_ALLOWED «SELL trigger children require QUOTE fee_asset» |
| SELL qty 100 AVAX при балансе около 3.4 | 400 INSUFFICIENT_FUNDS «Insufficient funds.» |
| subaccountId «zzzz» | 403 API_KEY_ROOT_SCOPE_ONLY |
| **qty 0.05** (minQty пары 0.1; сумма дочернего ордера 0.05 × 22.8 = 1.14 USDT при минимуме 5) | **200, ARMED** — минимум количества при создании не проверен |
| «triggerPriceSource» / «triggerDirection» в stopLoss | 400 BAD_REQUEST unknown field (управлять ими нельзя; в GET они есть: LAST_PRICE, BELOW/ABOVE) |

Не баги: всё, кроме двух строк ниже, соответствует ожиданиям.
Вопрос: is this by design? Для ордеров qty ниже минимума отклоняется (MIN_QTY и MIN_NOTIONAL — проверено для replace-batch в части 1), а триггер с qty 0.05 (ниже minQty 0.1) принят и стоит ARMED. Что будет при срабатывании — не проверял: в тесте с ожиданием 60 с ни этот триггер, ни контрольный триггер с нормальным qty не сработали (не было изменения цены), поэтому вывода о поведении при срабатывании нет.
Мелочь: `trigger: {}` и отсутствие `symbol` дают UNKNOWN_SYMBOL «unknown trading pair» вместо «symbol is required».
Дополнение к «scaled»: в справочнике `qty` — «Total quantity scaled by the pair's base_quantity_scale», пример «100000»; на деле qty — десятичная строка (в ответе GET триггера qty «0.513791»), а «100» воспринимается как 100 AVAX (INSUFFICIENT_FUNDS при балансе около 3.4 AVAX), а не как 0.0001.

""")
for lab in ["F5 без qty", "F12 qty ниже minQty (0.05 AVAX)", "F16 лишнее поле в trigger (foo)", "X2 SELL qty 100 AVAX (больше баланса)",
            "X5 SELL с feeAsset=BASE", "X1 BUY stopLoss с marketIoc child", "F2 trigger = {}"]:
    w(raw(lab, req=(lab.startswith(("F12", "X2")))))
w("""### 2.5 Трейлинг-стоп: дистанция на границах

В доках: `trailingStop` — «SELL market-IOC trailing stop.»; `trailingDistanceBps` (int32) — «Distance in basis points (1 bp = 0.01%).»; `trailingDistanceTicks` (string) — «Distance as a price delta in 1e-9 quote-unit ticks.»; `maxSlippageBps` (int32), `maxSlippageTicks` (int64) — «Positive maximum absolute price delta in Q9 execution-price ticks (1 tick = 1e-9 quote units).»; `activationPrice` необязательный. Границ значений на странице нет.

На деле (activationPrice всегда далеко выше рынка):
| Значение | Ответ |
|---|---|
| trailingDistanceBps 0, -1 | 400 VALIDATION_ERROR «trailing_distance_bps: must be greater than 0 and less than or equal to 10000» |
| 1, 500, 9999, 10000 | 200 |
| 10001, 1000000 | 400 VALIDATION_ERROR (тот же текст) |
| bps строкой «500» или дробное 1.5 | 400 BAD_REQUEST (json: cannot unmarshal …) |
| trailingDistanceTicks «0», «-1» | 400 VALIDATION_ERROR «trailing_distance_ticks: must be greater than 0» |
| ticks «1», «100000000», **«0.1»** | 200 (GET возвращает строку как отправлена: «1», «100000000», «0.1») |
| ticks «99999999999999» | 400 BAD_REQUEST «invalid trailingDistanceTicks: overflow» |
| bps и ticks вместе | 400 BAD_REQUEST «exactly one of trailingDistanceBps or trailingDistanceTicks must be set» |
| без дистанции | 400 VALIDATION_ERROR «trailing_distance: exactly one field is required in oneof» |
| maxSlippageBps 0, -1, 10001 | 400 VALIDATION_ERROR «max_slippage_bps: must be greater than 0 and less than or equal to 10000» |
| maxSlippageBps 10000 | 200 |
| maxSlippageTicks 0, -1 | 400 VALIDATION_ERROR «max_slippage_ticks: must be greater than 0» |
| maxSlippageBps и Ticks вместе | 400 BAD_REQUEST «exactly one of maxSlippageBps or maxSlippageTicks must be set» |
| side BUY | 400 VALIDATION_ERROR «trailing_stop side must be SELL» |
| activationPrice «-1» | 400 VALIDATION_ERROR «activation_price_ticks: must be greater than 0» |
| activationPrice «22.8005» | 400 PRICE_TICK_SIZE |
| activationPrice «0» | 200 (в GET то же значение «0», что и без activationPrice) |
| activationPrice ниже рынка или не задан (bps 5000) | 200 |

Коды TRAILING_DISTANCE_INVALID и MAX_SLIPPAGE_INVALID (есть в списке кодов справочника) не пришли ни разу: на границах приходит VALIDATION_ERROR с текстом диапазона.
Границы bps (больше 0 и не больше 10000) в справочнике не указаны — только в тексте ошибки. Не баг.
Дополнение к «scaled»: `trailingDistanceTicks` по справочнику — целое число тиков 1e-9, а на деле принимается дробная строка «0.1» и даёт overflow на «99999999999999» (1e14 тиков вписалось бы в int64; overflow возможен, если строка читается как десятичная цена и умножается на 1e9). Что означает «1» — 1 единица цены или 1e-9 — без срабатывания трейлинга не определил, поэтому не утверждаю.
Вопрос: is this by design? activationPrice «0» принимается (как «не задан»), а для triggerPrice «0» — ошибка.

""")
for lab in ["TR2 bps 0", "TR7 bps 10001", "TR15 ticks '0.1' десятичной строкой", "TR16 ticks больше цены '99999999999999'",
            "TR26 side BUY (трейлинг только SELL)", "TR27 activationPrice 0", "TR17 bps и ticks вместе"]:
    w(raw(lab, req=lab.startswith(("TR15", "TR16", "TR27"))))
w(raw("TR15 ticks '0.1' десятичной строкой | GET", req=False))
w("""### 2.6 Чтение сразу после записи, DELETE, коды

В доках: DELETE — «Cancel a trigger by trigger ID and release its reserved quantity.», статус ответа «Final status (should be STATUS_CANCELED)». «Possible errors» у POST /v1/triggers — 401, 403, 400, 404, 503.

На деле:
- `GET /v1/triggers/{id}` сразу после создания: в 1 случае из 3 — 404 NOT_FOUND (в остальных 200 со статусом CREATED, через 0.3 с — ARMED). В первом пробном запуске (не в логе) то же 404 было и на первом GET.
- `GET` сразу после `DELETE` (тот вернул CANCELED): в 1 случае из 3 статус ещё ARMED, через секунду CANCELED.
- Повторный DELETE уже отменённого триггера — 200 CANCELED. DELETE сработавшего (COMPLETED) — 400 TRIGGER_CANCEL_REJECTED «Trigger cannot be canceled in its current state.».
- Несуществующий id: GET — 404 NOT_FOUND, DELETE — 404 TRIGGER_NOT_FOUND. Недопустимый id «0» — 400 INVALID_TRIGGER_ID (в списке кодов справочника такого нет).
Не баги: коды TRIGGER_CANCEL_REJECTED и TRIGGER_NOT_FOUND соответствуют справочнику.
Вопрос: is this by design? GET сразу после создания может вернуть 404 (бот, который читает триггер по id сразу после POST, увидит «нет такого»); GET несуществующего id даёт общий NOT_FOUND, а DELETE — TRIGGER_NOT_FOUND.

""")
for lab in ["Y3 create (без паузы) | GET", "Y3 GET сразу после DELETE", "X10 GET несуществующий триггер",
            "X11 DELETE несуществующий триггер", "X12 GET триггер с неверным id"]:
    w(raw(lab, req=False))
w(raw("W1 SELL stopLoss ВЫШЕ рынка | DELETE", req=False))
w("""### 2.7 Не покрыто

TWAP и ladder не тестировал (в задании — стопы и трейлинг). Триггеры на чужом subaccount и с подпиской на события не проверялись. Срабатывание с рыночным дочерним ордером не проверялось (риск продажи монет общего аккаунта).
""")
open("docs/findings_2026-09-29.md", "w", encoding="utf-8").write(base + "\n".join(o))
print("ok", len(base + "\n".join(o)))
