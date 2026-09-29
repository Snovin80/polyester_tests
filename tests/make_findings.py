"""Собирает docs/findings_2026-09-29.md из сырых логов перепроверки.
Тела ответов вставляются дословно; счётчики считаются из логов, а не пишутся руками.
Для каждой метки берётся ПОСЛЕДНИЙ вызов (свежий прогон)."""
import json
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
RB = os.path.join(ROOT, "docs/raw_replace_batch_2026-09-29.jsonl")
TR = os.path.join(ROOT, "docs/raw_triggers_2026-09-29.jsonl")
TF = os.path.join(ROOT, "docs/raw_triggers_filters_2026-09-29.jsonl")
OUT = os.path.join(ROOT, "docs/findings_2026-09-29.md")


def load(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    last = {}
    for e in rows:
        last[e["label"]] = e
    return rows, last


rb_rows, RBL = load(RB)
tr_rows, TRL = load(TR)
tf_rows, TFL = load(TF)
ALL = {**RBL, **TRL, **TFL}


def J(e):
    try:
        return json.loads(e["response_raw"])
    except Exception:
        return None


def find(prefix, contains=""):
    """Последняя запись, метка которой начинается с prefix и содержит contains."""
    hit = None
    for rows in (rb_rows, tr_rows, tf_rows):
        for e in rows:
            if e["label"].startswith(prefix) and contains in e["label"]:
                hit = e
    if hit is None:
        raise KeyError(f"нет записи {prefix!r} / {contains!r}")
    return hit


def raw(e, req=True, cut=1100):
    if isinstance(e, str):
        e = ALL[e]
    body = e["response_raw"]
    if body.lstrip().startswith("<"):
        t = body.split("<title>")[1].split("</title>")[0] if "<title>" in body else "?"
        body = f"[HTML-страница Cloudflare, <title>{t}</title>; тело целиком в raw-логе]"
    elif len(body) > cut:
        body = body[:cut] + " …(обрезано; полный ответ в raw-логе)"
    s = f"`{e['method']} {e['path']}` — {e['utc']} — HTTP {e['http']} — «{e['label']}»\n\n"
    if req and e["request"] is not None:
        rq = json.dumps(e["request"], ensure_ascii=False)
        if len(rq) > 600:
            rq = rq[:600] + " …(обрезано)"
        s += f"Запрос: `{rq}`\n\n"
    return s + "Ответ:\n```\n" + body + "\n```\n"


def order_line(e):
    o = (J(e) or {}).get("order", {})
    return (f"{o.get('orderId')} {o.get('status')} price={o.get('price')} qty={o.get('origQty')} "
            f"clientOrderId={o.get('clientOrderId')} generation={o.get('lineage', {}).get('generation')} "
            f"terminalReason={o.get('terminalReason', '-')}")


# ---------- счётчики из данных ----------
y_get = [e for e in tr_rows if e["label"].startswith("Y") and e["label"].endswith("create (без паузы) | GET")]
y_del = [e for e in tr_rows if e["label"].startswith("Y") and e["label"].endswith("GET сразу после DELETE")]
y404 = sum(1 for e in y_get if e["http"] == 404)
yarm = sum(1 for e in y_del if (J(e) or {}).get("trigger", {}).get("status") == "ARMED")
r1 = [(e["label"], e["http"]) for e in tr_rows if e["label"].startswith("R1 clientTriggerId")]
r3 = [(e["label"], e["http"]) for e in tr_rows if e["label"].startswith(("R3.2", "R3.3", "F33c"))]
ids_created = []
for e in tr_rows:
    if e["method"] == "POST" and e["path"] == "/v1/triggers" and e["http"] == 200:
        ids_created.append((J(e) or {}).get("triggerId"))
ids_created = list(dict.fromkeys(i for i in ids_created if i))
final = {}
for e in tr_rows:
    if e["label"].startswith("verify GET "):
        final[e["label"][11:]] = (J(e) or {}).get("trigger", {}).get("status")
fin_counts = {}
for s in final.values():
    fin_counts[s] = fin_counts.get(s, 0) + 1

o = []
w = o.append
w(f"""# Findings 2026-09-29 (перепроверено)

Все утверждения ниже — из свежего прогона 29.09 (replace-batch — около 09:56–09:58 UTC, триггеры — около 09:59–10:20 UTC). Для каждого пункта: что в справочнике (дословно из `docs/polyester_docs/`, выгрузка 29.09 11:20 UTC; страницы эндпоинтов совпадают со снимком 08:44 слово в слово), что на деле, сырой ответ (тело дословно, время UTC, HTTP-код). Не баг — так и написано.

Сырые логи: `docs/raw_replace_batch_2026-09-29.jsonl` ({len(rb_rows)} вызовов), `docs/raw_triggers_2026-09-29.jsonl` ({len(tr_rows)} вызовов), `docs/raw_triggers_filters_2026-09-29.jsonl` ({len(tf_rows)} вызовов). Скрипты: `tests/bugtest1_replace_batch.py`, `tests/bugtest2_triggers.py`, сборка этого файла — `tests/make_findings.py`. Первый прогон (до перепроверки) — в истории git. Текст для команды (EN + RU + «Проверено») — `docs/report_2026-09-29.txt`.

Аккаунт общий. Ордера — ETH-USDT BUY 0.005 по 1350 (рынок ≈2700) и AVAX-USDT BUY по 5.7 (рынок ≈11.5), post-only. Дочерние ордера триггеров — SELL 22.8 / BUY 5.0–5.7, трейлинг — с activationPrice ≈2× рынка. Всё своё снято по id; cancel-all не вызывался. Итог уборки — в разделе 3.

""")

w("""## 0. Сверка с полной документацией (docs/polyester_docs/, 11:20 UTC)

| Пункт | Что нашлось в документации | Итог |
|---|---|---|
| 1.2 AMENDED, 1.5 замена без изменений | user-docs/trade__manage-orders: «An in-place amend is limited to a Limit order that keeps its price and does not increase its total quantity»; «Amend or Replace — Amends when possible … The final Order ID can change»; connect GetBatchReplaceStatus: «AMENDED is cancel-only: no successor exists»; пример ответа там же — AMENDED с replacementOrderId = oldOrderId | пункты объединены и усилены; живьём: GetBatchReplaceStatus свежей замены с уменьшением qty — REPLACED |
| 1.1 newPriceTicks / newQtyScaled | developer-docs/connectrpc__scaled-integers: «Polyester REST APIs expose decimal values as strings … REST clients do not need to decode *_scaled, *_ticks …» | newPriceTicks соответствует; newQtyScaled (replace-batch и modify) — нет: только целое, «0.0045» строкой и числом — 400 |
| 1.4 привязанный TP/SL | api-docs POST /v1/orders: attachedRisk «arm after the parent order fills», triggerPrice «scaled by 1e9»; про проверку цены — ничего | без изменений |
| 1.6 acceptedTs | общего правила о формате времени в REST нет; REST и connect страницы — строка | без изменений |
| 2.1 clientTriggerId | connect CreateTrigger: «min 1 chars», максимума нет; REST: «Client-provided trigger ID for idempotency.» (SDK-доки не используем — команда просила не опираться на SDK) | повтор после DELETE → прежний триггер — по докам нормально, убрано из отчёта |
| 2.2 «не с той стороны» | user-docs stop-loss: «A Last Price trade at or below 95,000 USDT activates the stop» | **не баг по докам**, убрано из отчёта |
| 2.3 qty ниже минимума | user-docs ladder/twap: «Every rounded child must independently satisfy … minimum-order rules»; order-triggers: «A child rejection is a terminal trigger failure»; stop-loss: «An accepted trigger does not prove that the later child is admitted» | **не баг по докам**, убрано из отчёта |
| 2.7 ladder postOnly | REST — required; connect — Unset/False/True | в отчёте: расхождение REST и connect |
| 2.8 чтение после записи | developer-docs/shared-concepts__client-order-ids: «GetOrder briefly waits server-side … returns a temporary unavailable error instead of NOT_FOUND» (про ордера) | усилено сравнением; журнал бота: 24.09 не воспроизвелось, сейчас 3 из 10 |
| 2.9 фильтры | connect ListTriggers: parentOrderId — «Optional filter by attached parent order ID», статусы STATUS_* | живьём: в connect фильтр работает (ровно 2 ноги, несуществующий id — 400), в REST нет ни base58, ни числом |
| отправленное ранее | docs/bot/journal.md, «ОТПРАВЛЕНО» | дублей нет; родственное помечено «Addition/Related» (28.09 modify, 27.09 502, 24.09 scaled) |

""")
for lab in ["M2 replace-batch qty 5000->4500"]:
    if lab in ALL: w(raw(lab, cut=600))
for e in rb_rows:
    if e["label"].startswith("M2 ConnectRPC GetBatchReplaceStatus +0.5s"):
        w(raw(e, cut=600))
for lab in ["K9 replace-batch newQtyScaled десятичной строкой '0.0045'", "K10 replace-batch newQtyScaled числом 0.0045",
            "K11 modify newQtyScaled десятичной строкой '0.0045'"]:
    w(raw(lab, cut=500))
for e in tf_rows:
    if e["label"].startswith(("FILT10", "FILT11", "FILT12")):
        w(raw(e, cut=450))

# ================= ЧАСТЬ 1 =================
w("## Часть 1. POST /v1/orders/replace-batch\n\n")
w("""### 1.1 newPriceTicks: десятичная цена, не тики

В доках: у `items` только «Replacement items.», поля пункта на странице не описаны; в примере `"newPriceTicks": "0.005"` (строка) и `"newQtyScaled": 100000` (число).
На деле: `newPriceTicks` — десятичная цена строкой: «1340.00» → цена 1340, «1320» → 1320 (если бы это были тики по 0.01, было бы 13.20). Обратная проверка: «132000» стало ценой 132000 (преемник отклонён POST_ONLY_CROSS, исходный ордер остался WORKING). Число JSON вместо строки — 400. `newQtyScaled` — целое число в масштабе пары (ETH: 0.0045 = 4500); строка — 400 «cannot unmarshal string … of type int64».
Итог: пример справочника верен, но имя поля («Ticks») вводит в заблуждение и поля пункта не описаны.

""")
w(raw("A1 newPriceTicks string '1340.00'"))
w(f"Состояние после: `{order_line(ALL['A1 get succ'])}`\n\n")
w(raw("A3 newPriceTicks string '1320' (целое без точки)", req=False))
w(f"Состояние после: `{order_line(ALL['A3 get succ'])}`\n\n")
w(raw("A3b обратная проверка: '132000' (если бы тики — 1320.00)", req=False))
w(f"Преемник: `{order_line(ALL['A3b get succ'])}`; исходный: `{order_line(ALL['A3b get old'])}`\n\n")
w(raw("A2 newPriceTicks JSON number 133000"))
w(raw("K1 newQtyScaled строкой '4500' (несуществующий id)"))

w("""### 1.2 actionTaken AMENDED

В доках replace-batch (results[].actionTaken): «REPLACED admits a successor. AMENDED cancels the original order's remaining quantity without a successor; keep tracking old_order_id until its terminal state is confirmed. Unspecified for rejected items.» (results[].replacementOrderId): «Assigned successor order ID. Zero for cancel-only outcomes or rejection before assignment.» Но пример ответа на той же странице: `"actionTaken": "AMENDED"`, `"oldOrderId": "5USXJZmk"`, `"replacementOrderId": "5USXJZmk"` — у AMENDED преемник равен исходному, не ноль; пример противоречит описанию. Когда replace-batch возвращает AMENDED, на странице не сказано. У modify поле behavior — только AMEND_OR_REPLACE («MODIFY_BEHAVIOR_UNSPECIFIED is treated as AMEND_OR_REPLACE»).
В доках modify: `finalOrderId` — «Final active order ID; same as old_order_id for amendments.»

На деле replace-batch при уменьшении qty (5000→4500, 4500→4000) и при увеличении (→6000) всегда отвечает REPLACED: новый id, старый ордер CANCELED с terminalReason ORDER_REPLACED, преемник WORKING с новым qty, той же ценой, тем же clientOrderId и тем же createdAt (lineage.generation +1). AMENDED не пришёл ни разу за оба прогона.
Обратная проверка: modify с тем же уменьшением (5000→4500) на свежем ордере — AMENDED, finalOrderId = oldOrderId, ордер тот же.
Не проверено: случай из описания AMENDED («без преемника»). Предположение «нужен частично исполненный ордер» — моё, в справочнике его нет; такой ордер не создавался. Вопрос команде — в отчёте.

""")
w(raw("B1 qty вниз 5000->4500, цена не передана"))
w(f"Старый: `{order_line(ALL['B1 get old'])}`\n\nПреемник: `{order_line(ALL['B1 get succ'])}`\n\n")
w(raw("B3 qty вверх ->6000", req=False))
w(raw("D4 modify qty вниз 5000->4500"))
w(f"После modify: `{order_line(ALL['D4 get old'])}`\n\n")

w("""### 1.3 Границы

| Случай | На деле |
|---|---|
| пустой `items` / `items` нет | 400 VALIDATION_ERROR «items: must contain at least 1 item(s)» |
| 50 пунктов | 200, 50 результатов (последний itemIndex 49) |
| 51 пункт | 400 BATCH_TOO_LARGE «Batch size exceeds the maximum allowed (50 items).» |
| qty 0 / −1000 | 400 VALIDATION_ERROR «items[0].new_qty_scaled: must be greater than 0» |
| пункт только с orderId | 400 VALIDATION_ERROR «items[0]: at least one patch field must be set» |
| пункт с теми же ценой и qty | 200, REPLACED, новый id, старый CANCELED ORDER_REPLACED (см. 1.5) |
| один ордер дважды | 400 VALIDATION_ERROR «each item target must be unique within a batch»; ордер не тронут |
| несуществующий orderId | 200, REJECTED, ORDER_UNKNOWN, oldOrderId «1» |
| уже отменённый свой ордер; старый id после замены | 200, REJECTED, ORDER_UNKNOWN, oldOrderId — переданный id (modify на отменённый — 404 ORDER_UNKNOWN; код ORDER_ALREADY_TERMINAL из списка не пришёл) |
| ордер другой пары (AVAX-USDT) при symbol ETH-USDT | 200, REJECTED, UNKNOWN_SYMBOL, oldOrderId «1»; ордер не тронут (как у modify — уже отправлено 28.09) |
| повтор requestId, тот же body | 200, ответ побайтно тот же, второй замены нет |
| повтор requestId, другой body | 409 CONFLICT_IDEMPOTENCY_KEY_REUSE |
| цена 0 / −1 | 400 VALIDATION_ERROR «items[0].new_price_ticks: must be greater than 0» |
| цена 1340.001 | 200, REJECTED, PRICE_TICK_SIZE |
| qty 50 (0.00005 ETH) | 200, REJECTED, MIN_QTY |
| qty 1000 (0.001 ETH × 1350 = 1.35 USDT < 5) | 200, REJECTED, MIN_NOTIONAL |
| newClientOrderId 36 символов | 200; 37, 64, 100 — 400 «new_client_order_id: must be at most 36 characters» |
| newClientOrderId = clientOrderId другого живого ордера | 200, REJECTED, CONFLICT_DUPLICATE_CLIENT_ORDER_ID, replacementOrderId = oldOrderId (не «1», см. 1.6) |
| subaccountId «zzzz» и свой корневой RCx3H2SjGz6 | 403 API_KEY_ROOT_SCOPE_ONLY (ключ только для корневого счёта) |
| чужой ордер (субаккаунт ↔ основной счёт, в обе стороны) | 200, REJECTED, ORDER_UNKNOWN, oldOrderId «1»; modify/cancel/GET — 404; ордер не тронут (см. 1.8) |

Не баги: всё в таблице, кроме отмеченного в 1.5 и 1.6. Коды BATCH_TOO_LARGE, CONFLICT_IDEMPOTENCY_KEY_REUSE, CONFLICT_DUPLICATE_CLIENT_ORDER_ID, PRICE_TICK_SIZE, MIN_QTY, MIN_NOTIONAL, ORDER_UNKNOWN есть в списке кодов справочника. В таблице «Possible errors» страницы — только 401, 403, 400, 404, 503 (нет 409) — мелочь.

""")
for lab in ["C1 пустой items", "C4 51 элемент (несуществующие id)", "C5 qty 0", "C7 пункт без изменений (только orderId)",
            "C9 один ордер дважды (qty 4000 и 3000)", "C10 несуществующий orderId", "F2 replace отменённого ордера",
            "F1 symbol ETH-USDT, orderId от AVAX-USDT, цена 5.600", "C11a requestId первый раз",
            "C11b тот же requestId, тот же body", "C11c тот же requestId, другой body (qty 3000)", "C12 цена 0",
            "C14 лишний знак цены 1340.001", "C15 qty ниже минимума (50 = 0.00005 ETH)",
            "C16 notional ниже минимума (qty 1000 = 0.001 ETH * 1350)", "G1 newClientOrderId 37 символов",
            "F4 newClientOrderId = clientOrderId другого живого ордера", "F7b subaccountId свой корневой RCx3H2SjGz6"]:
    w(raw(lab, req=lab.startswith(("C9", "C11", "F1", "F4"))))
c11 = ALL["C11a requestId первый раз"]["response_raw"] == ALL["C11b тот же requestId, тот же body"]["response_raw"]
w(f"Проверка «побайтно тот же»: ответы C11a и C11b совпадают — {c11}.\n\n")

w("""### 1.4 newAttachedRisk: цены привязанного TP/SL не проверяются

В доках replace-batch: поле есть только в примере — `newAttachedRisk: {oco:false, stopLoss:{child:{marketIoc:{}}, triggerPrice:"0.005"}, takeProfit:{child:{marketIoc:{}}, triggerPrice:"0.005"}}` для BTC-USDT.
На деле (ETH-USDT, шаг цены 0.01, BUY-родитель по 1350):
- TP 3500 с limitGtc принят, в ордере появляется attachedRisk. `postOnly` в limitGtc привязанной ноги — 400 «unknown field "postOnly"» (у отдельного триггера это поле есть).
- TP 3500.005 (не по шагу) принят и сохранён как «3500.005» — и в replace-batch, и при создании ордера `POST /v1/orders`. Обратная проверка: отдельный триггер `POST /v1/triggers` на ETH-USDT с 3500.005 — 400 PRICE_TICK_SIZE, с 3500.00 — 200.
- TP 1000 (ниже цены входа 1350 у BUY) принят — и в replace-batch, и при создании ордера.
- Пример справочника (SL и TP по 0.005) принят как есть.
- При отмене родителя без исполнения ноги отменяются сами (cancelReason PARENT_CANCELED_NO_FILL) — не баг.
Вопрос: is this by design? Цены привязанных ног не проверяются ни на шаг цены, ни на сторону, хотя отдельный триггер на тот же шаг отвечает PRICE_TICK_SIZE.

""")
for lab in ["F8a newAttachedRisk takeProfit 3500 (limitGtc)", "G3a replace-batch newAttachedRisk TP 3500.005 (шаг 0.01)",
            "G3b create ордера с attachedRisk TP 3500.005", "X14 ETH-USDT BUY stopLoss 3500.005 (шаг 0.01) — сравнение с привязанным TP/SL",
            "X14b ETH-USDT BUY stopLoss 3500.00 (контроль)", "G4a replace-batch newAttachedRisk TP 1000 (ниже цены входа 1350)",
            "G4b create ордера с attachedRisk TP 1000 (ниже цены входа 1350)",
            "G5a replace-batch newAttachedRisk как в примере (0.005/0.005)"]:
    w(raw(lab))
w("Хранение (перепроверено 10:31–10:32 UTC): ордер из `POST /v1/orders` с TP 3500.005 и с TP 1000 — в attachedRisk сохранено «3500.005» и «1000»; преемник replace-batch с TP 3500.005 — в attachedRisk «3500.005», нога-триггер создана с triggerPrice «3500.005» (статус CREATED). У заменённого ордера attachedRisk в ответе пропадает — риск переходит к преемнику, это не отдельная проблема.\n\n")
for lab in ["K2 GET ордер из G3b (create, TP 3500.005)", "K4 GET ордер из G4b (create, TP 1000)",
            "K6 replace-batch newAttachedRisk TP 3500.005 (marketIoc)", "K6 GET преемник сразу", "K8 GET нога cSbDnHzCuP8"]:
    w(raw(lab, req=lab.startswith("K6 replace"), cut=1300))
w(raw("G6 get родителя после cancel", req=False, cut=1600))

w("""### 1.5 Пункт с теми же ценой и qty меняет id

На деле: пункт с ценой и qty, равными текущим (1350 / 5000), выполняется как REPLACED — новый id, старый CANCELED ORDER_REPLACED. modify с теми же значениями делает то же самое (REPLACED, новый finalOrderId). Пункт вообще без полей — 400.
Вопрос: is this by design? Влияние на место в очереди не проверял.

""")
w(raw("C8 пункт с теми же цена/qty (1350 / 5000)"))
w(raw("F9 modify те же цена/qty (1350 / 5000)"))

w("""### 1.6 Поля ответа против справочника

| Поле | В доках | На деле |
|---|---|---|
| acceptedTs | «string», пример «2025-01-01T00:00:00Z» | объект `{"seconds":…,"nanos":…}` во всех ответах (у `POST /v1/orders` время приходит строкой acceptedAt) |
| acceptedCount / rejectedCount | integer, пример 1 / 1 | поле отсутствует, когда значение 0 |
| results[].itemIndex | «Zero-based index in the request.» | у пункта с индексом 0 поля нет; у пункта 1 — `"itemIndex":1` |
| results[].oldOrderId | «Original order targeted by the replacement.» | для несуществующего id и для ордера другой пары — «1» (нулевой id), а не переданный id; для отменённого своего — переданный id |
| results[].replacementOrderId | «Zero for cancel-only outcomes or rejection before assignment.» | при отказе обычно «1» (ноль); при CONFLICT_DUPLICATE_CLIENT_ORDER_ID — равен oldOrderId |
| results[].clientOrderId | «Client order ID assigned to the successor when available.» | есть, только если передан newClientOrderId; без него преемник наследует clientOrderId, но в results его нет |
| results[].code, error.code | есть | оба при отказе; error содержит только code |
| status | ADMITTED / PARTIALLY_ADMITTED / REJECTED | все три получены |

Расхождение с доками: acceptedTs. Вопросы (is this by design?): пропуск нулевых значений, oldOrderId «1» вместо переданного id, replacementOrderId = oldOrderId при отказе.

""")
for lab in ["D1 два реальных пункта (цена 1340 и qty 4500)", "D2 смешанный: реальный + несуществующий",
            "C3 50 элементов (несуществующие id)", "G8 replace без newClientOrderId"]:
    w(raw(lab, req=False, cut=900))
w(f"G8: исходный `{order_line(ALL['G8 get old'])}`; преемник `{order_line(ALL['G8 get succ'])}`\n\n")

w("""### 1.7 Приём ≠ результат (не баг)

В доках: «return an admission receipt». На деле: замена post-only ордера на цену выше рынка — 200 ADMITTED REPLACED, затем преемник REJECTED POST_ONLY_CROSS, исходный остаётся WORKING. Соответствует «admission receipt».

""")
w(raw("E1 цена 3000 (выше лучшего ask, post-only пересечёт)", req=False))
w(f"Исходный: `{order_line(ALL['E1 get old'])}`; преемник: `{order_line(ALL['E1 get succ'])}`\n\n")

w("""### 1.8 Чужой счёт: субаккаунт в обе стороны (не баг)

Ключ субаккаунта snovin-1 (387wT9mN3df) против ордера и триггера основного счёта: GET ордера — 404 NOT_FOUND; replace-batch — 200 REJECTED ORDER_UNKNOWN (oldOrderId «1»); modify и cancel — 404 ORDER_UNKNOWN; replace-batch с subaccountId основного счёта — 404; GET триггера — 404 NOT_FOUND, DELETE — 404 TRIGGER_NOT_FOUND; список триггеров субаккаунта пуст. Ордер и триггер основного счёта не изменились.
Основной ключ против ордера субаккаунта: GET — 404; replace-batch — 200 REJECTED ORDER_UNKNOWN; modify и cancel — 404 ORDER_UNKNOWN; replace-batch с subaccountId субаккаунта — 403 API_KEY_ROOT_SCOPE_ONLY. Ордер субаккаунта не изменился; ключ субаккаунта сам меняет его штатно (REPLACED).
Изоляция работает. Попутно: до того как автор включил торговлю в правах самого субаккаунта (отдельно от прав ключа), создание ордера давало 403 POLICY_SPOT_TRADE_DENY — это настройка, не баг.

""")
for lab in ["H2 SUB GET ордер основного счёта", "H3 SUB replace-batch ордера основного счёта", "H5 SUB cancel ордера основного счёта",
            "H8 SUB DELETE триггер основного счёта", "H11 root get order после", "J1 ROOT GET ордер субаккаунта",
            "J2 ROOT replace-batch ордера субаккаунта", "J4 ROOT cancel ордера субаккаунта",
            "J5 ROOT replace-batch с subaccountId субаккаунта", "J6 SUB GET свой ордер после",
            "J7 SUB replace-batch своего ордера qty 5000->4500 (контроль)"]:
    w(raw(lab, req=lab.startswith(("H3", "J2", "J5")), cut=700))

# ================= ЧАСТЬ 2 =================
w("## Часть 2. POST /v1/triggers\n\n")
w("""Пара AVAX-USDT (шаг цены 0.001, minQty 0.1, min notional 5). Справочник — `docs/polyester_docs/api-docs/rest__POST__v1__triggers.txt`, `rest__GET__v1__triggers.txt`, `rest__GET__v1__triggers__trigger_id.txt`, `rest__DELETE__v1__triggers__trigger_id.txt` и ConnectRPC-версии `connect__triggers.v1.TriggersService__*.txt`.
Уже отправлено (не дублирую): «цены scaled вместо decimal» в справочнике триггеров, childOrderId «1». Ниже родственное помечено «дополнение».

""")
r1s = ", ".join(f"{l.replace('R1 clientTriggerId ', '')} → {h}" for l, h in r1)
r3s = ", ".join(f"«{l}» → {h}" for l, h in r3)
w(f"""### 2.1 clientTriggerId

В доках: «Client-provided trigger ID for idempotency.» — не помечен required (required только symbol и поля стратегий).
На деле:
- без clientTriggerId — 400 VALIDATION_ERROR «trigger.client_trigger_id: must be at least 1 characters» (поле обязательно);
- длина (случайные id): {r1s}. Граница — 64: длиннее — 503 UNAVAILABLE с текстом «read_decode: read tcp …->…:9100: read: connection reset by peer» (внутренний адрес и порт в тексте ошибки) вместо 400. Лишних триггеров от этих 503 нет (проверено списком GET /v1/triggers);
- повтор clientTriggerId с тем же телом, пока триггер жив — 200 и тот же triggerId (идемпотентность работает);
- повтор с другим телом: {r3s} — HTML-страница Cloudflare «502: Bad gateway», без JSON; исходный триггер не изменён (GET после попыток). Код CONFLICT_DUPLICATE_CLIENT_TRIGGER_ID есть в списке кодов справочника, но не пришёл;
- повтор с тем же телом после DELETE — 200, свежий acceptedAt, но triggerId прежний, и он CANCELED: нового триггера нет.
Вопросы: поле обязательно, но не помечено; длинный id → 503 вместо 400; другой body → 502 вместо 409; после DELETE повтор отвечает как успешное создание.

""")
for lab in ["F6 без clientTriggerId"]:
    w(raw(lab))
w(raw(find("R1 clientTriggerId 64"), req=False))
w(raw(find("R1 clientTriggerId 65"), req=False))
w(raw(find("R1 clientTriggerId 200"), req=False))
w(raw("R3.0 clientTriggerId повтор — первый"))
w(raw("R3.1 тот же clientTriggerId, тот же body (пока ARMED)", req=False))
w(raw("R3.2 тот же clientTriggerId, другой qty"))
w(raw("R3.4 GET первого после попыток", req=False))
w(raw("R3.6 тот же clientTriggerId и body после DELETE"))
w(raw("R3.6 тот же clientTriggerId и body после DELETE | GET", req=False))

# FIRE — из данных
# только последний ЗАВЕРШЁННЫЙ запуск FIRE (от «FIRE балансы до» до «FIRE сделки после ожидания»)
_starts = [i for i, e in enumerate(tr_rows) if e["label"] == "FIRE балансы до"]
_ends = [i for i, e in enumerate(tr_rows) if e["label"] == "FIRE сделки после ожидания"]
_run = []
for st_i in reversed(_starts):
    en = [j for j in _ends if j > st_i]
    if en:
        nxt = [k for k in _starts if k > st_i]
        _run = tr_rows[st_i: (nxt[0] if nxt else len(tr_rows))]
        break
fire_create = [e for e in _run if e["label"].startswith(("FW", "FM", "FC")) and e["method"] == "POST"]
fire_after = [e for e in _run if e["label"].startswith(("FW", "FM", "FC")) and e["label"].endswith("| GET после сделки")]
fire_ev = [e for e in _run if e["label"].startswith(("FW", "FM", "FC")) and e["label"].endswith("| events")]
waited = [e for e in _run if e["label"] == "FIRE сделки после ожидания"]
fire_wait_line = next((e for e in _run if e["label"] == "FIRE последняя сделка до ожидания"), None)
fire_rows = []
for e in fire_after:
    t = (J(e) or {}).get("trigger", {})
    fire_rows.append(f"| {e['label'].split(' | ')[0]} | {t.get('status')} | {t.get('failureReason', '-')} |")
w("""### 2.2 Цена «не с той стороны» рынка: принимается и срабатывает на следующей сделке (по докам — не баг, см. раздел 0)

В доках: TRIGGER_PRICE_INVALID есть в списке кодов (страница replace-batch); на странице POST /v1/triggers — ни слова, когда он приходит.
На деле: все 10 сочетаний (SELL/BUY × stopLoss/takeProfit, выше/ниже рынка, ровно по bid/ask) создаются со статусом ARMED; направление (BELOW/ABOVE) выводится из типа и стороны, а не из рынка. Цена 0 и отрицательная — VALIDATION_ERROR; TRIGGER_PRICE_INVALID не пришёл ни разу.
Срабатывание: триггеры ждали первую новую сделку по паре (сделки идут в среднем раз в 48 с, бывают паузы до 38 мин). Результат последнего ожидания (была ли новая сделка — строка «FIRE сделки после ожидания» ниже):

| Триггер | Статус после сделки | failureReason |
|---|---|---|
""")
w("\n".join(fire_rows) + "\n\n")
if fire_wait_line and waited:
    _t0 = max((t["executedAt"] for t in (J(fire_wait_line) or {}).get("trades", [])), default="")
    _t1 = max((t["executedAt"] for t in (J(waited[-1]) or {}).get("trades", [])), default="")
    if _t1 > _t0:
        w(f"В этом ожидании новая сделка была: {_t1} (до ожидания последняя — {_t0}).\n\n")
    else:
        w(f"В этом ожидании (с {fire_wait_line['utc']} до {waited[-1]['utc']}) новых сделок по паре не было — последняя {_t0}; поэтому статусы не изменились и вывода о срабатывании из этого запуска нет.\n\n")
w("""Контроль (верная сторона, далеко от рынка) не сработал — значит, срабатывание вызвано уже выполненным условием, а не сбоем. Дочерние ордера сработавших — далёкие лимитки, сняты по id.
Первый прогон (09:33) — единственная сделка за время теста. Перечитано живыми вызовами в 10:19 UTC (ниже): два триггера «не с той стороны» (SELL stopLoss 13.707 и BUY stopLoss 9.138 при рынке ≈11.4) — COMPLETED, completedAt 09:33:11.804064116; события FIRED с tsNs 1790674391804064116 и firePx 11.422; публичная сделка AVAX-USDT 11.422 в 09:33:11.804064116 — то же время до наносекунды. Дочерние ордера (SELL 22.8, BUY 5.7) отменены мной по id в 09:34.
Перепроверка: первое ожидание (10:04–10:19) сделки не дождалось — все триггеры, включая контроль, остались ARMED (без сделки срабатывания нет, противоречия нет). Второе ожидание (с 10:20) дождалось сделки 10:29:50.283879368 — таблица выше: FW1 и FW2 («неверная сторона») COMPLETED с completedAt и tsNs события, равными времени сделки; firePx 11.608 — одна из сделок этой наносекунды; дочерние ордера SELL 22.8 и BUY 5.7 выставлены и сняты мной по id; контроль FC2 (верная сторона) остался ARMED.
Вопрос: is this by design? Триггер, условие которого уже выполнено при создании, принимается без ошибки и срабатывает на первой же сделке. Когда возвращается TRIGGER_PRICE_INVALID?

""")
for lab in ["W-run1 GET Zafbqjkj34P (перечитано)", "W-run1 events Zafbqjkj34P (перечитано)",
            "W-run1 GET 4b6gLadxpm3 (перечитано)", "W-run1 events 4b6gLadxpm3 (перечитано)"]:
    w(raw(lab, req=False, cut=1200))
e = ALL["W-run1 публичные сделки AVAX-USDT (limit 1000)"]
tr933 = [t for t in (J(e) or {}).get("trades", []) if t["executedAt"].startswith("2026-09-29T09:33:")]
w(f"`GET /v1/spot/markets/AVAX-USDT/trades` — {e['utc']} — HTTP {e['http']} — сделки 09:33 из ответа (выборка):\n```\n{json.dumps(tr933, ensure_ascii=False)}\n```\n")
for e in fire_create:
    w(raw(e, req=e["label"].startswith("FW1")))
for e in fire_after + fire_ev:
    w(raw(e, req=False, cut=900))
if waited:
    w(raw(waited[-1], req=True, cut=700))
for lab in ["FIRE2 child DGtExYgSR7e после отмены", "FIRE2 child 6ZFFe9n7QBW после отмены"]:
    if lab in ALL:
        w(raw(lab, req=False, cut=900))
if "FIRE2 публичные сделки (limit 50)" in ALL:
    e = ALL["FIRE2 публичные сделки (limit 50)"]
    t50 = [t for t in (J(e) or {}).get("trades", []) if t["executedAt"].startswith("2026-09-29T10:29:50")]
    w(f"`GET /v1/spot/markets/AVAX-USDT/trades` — {e['utc']} — HTTP {e['http']} — сделки 10:29:50 из ответа (выборка):\n```\n{json.dumps(t50, ensure_ascii=False)}\n```\n")

w("""### 2.3 qty ниже минимума принимается при создании (по докам — не баг, см. раздел 0)

В доках: ограничений qty на странице нет. Для ордеров биржа отклоняет MIN_QTY / MIN_NOTIONAL (обратная проверка ниже).
На деле: stop-loss с qty 0.05 (minQty пары 0.1; дочерний ордер 0.05 × 22.8 = 1.14 USDT < 5) — 200, ARMED. Ladder и TWAP с qty 0.2 (на уровень/слайс 0.1) — 200, через ~1 с FAILED с failureReason MIN_NOTIONAL.
Stop-loss с qty 0.05 при срабатывании (сделка 10:29:50.283879368, FM1 в таблице 2.2) — FAILED, failureReason MIN_QUANTITY, событие FAILED, дочернего ордера нет. То есть о слишком малом qty клиент узнаёт только в момент, когда стоп должен был сработать.
Вопрос: is this by design? Проверка минимумов откладывается до срабатывания, и защитный стоп молча не исполняется.

""")
for lab in ["F12 qty ниже minQty (0.05 AVAX)", "X15 ордер AVAX BUY qty 0.05 @5.700 (обратная проверка minQty)",
            "L11 qty на уровень ниже минимума (0.2 / 2 уровня)", "L11 qty на уровень ниже минимума (0.2 / 2 уровня) | GET"]:
    w(raw(lab, req=lab.startswith(("F12", "X15", "L11 qty на уровень ниже минимума (0.2 / 2 уровня)")) and not lab.endswith("GET"), cut=800))

w("""### 2.4 Цена: 0, отрицательная, шаг, лишние знаки, формат

| Значение triggerPrice | Ответ |
|---|---|
| «0», «-1», «-0.001», «» | 400 VALIDATION_ERROR «trigger.stop_loss.trigger_price_ticks: must be greater than 0» |
| «abc», « 10», «1e1» | 400 BAD_REQUEST «invalid triggerPrice: invalid syntax» |
| «10.0005», «10.000000001», «0.000000001» | 400 PRICE_TICK_SIZE «…trigger_price_ticks must align with tick size 1000000» |
| «10.0000000001» (10 знаков) | 400 BAD_REQUEST «invalid triggerPrice: too many fractional digits» |
| «99999999999999999999» | 400 BAD_REQUEST «invalid triggerPrice: overflow» |
| «10.0010», «.5», «10.», «+10» | 200 |
| число JSON 10 / 10.5 | 400 BAD_REQUEST «cannot unmarshal number … of type string» |
| цена child «22.8005» / «0» / «-1» | 400 PRICE_TICK_SIZE / VALIDATION_ERROR |

Не баги. Дополнение к «scaled»: в тексте PRICE_TICK_SIZE шаг назван «1000000» (0.001 × 1e9) и поле `trigger_price_ticks`, а в запросе поле `triggerPrice` и десятичная цена.

""")
for lab in ["P1 цена 0", "P6 10.0005 (лишний знак сверх шага 0.001)", "P9 10.0000000001 (10 знаков)", "P12 '.5'",
            "P17 число JSON 10 вместо строки"]:
    w(raw(lab, req=False))

w("""### 2.5 Поля: пустые, лишние, неверные

| Случай | Ответ |
|---|---|
| тело `{}` | 400 VALIDATION_ERROR «trigger is required» |
| `trigger: {}`, без symbol, symbol «XXX-USDT» | 400 UNKNOWN_SYMBOL «unknown trading pair» |
| symbol «avax-usdt» | 200 |
| без qty, qty 0, −1 | 400 VALIDATION_ERROR «trigger.qty_scaled: must be greater than 0» |
| qty «abc» / «0.5137911» / число 0.5 | 400 BAD_REQUEST (invalid syntax / too many fractional digits / cannot unmarshal number) |
| нет стратегии / две стратегии | 400 VALIDATION_ERROR / BAD_REQUEST «exactly one of …» |
| лишнее поле foo (в trigger, сверху, в stopLoss), triggerPriceSource, triggerDirection | 400 BAD_REQUEST «json: unknown field …» |
| неверный feeAsset / selfTradePreventionMode / side | 400 BAD_REQUEST «unknown enum value» |
| без side / без child / child `{}` / два child | 400 VALIDATION_ERROR или BAD_REQUEST с понятным текстом |
| BUY stopLoss с marketIoc | 400 VALIDATION_ERROR «BUY stop-loss and take-profit triggers require a limit child» |
| SELL с feeAsset BASE | 400 FEE_ASSET_NOT_ALLOWED |
| SELL qty 100 при ~3.36 AVAX | 400 INSUFFICIENT_FUNDS |
| subaccountId «zzzz» | 403 API_KEY_ROOT_SCOPE_ONLY |
| child limitIoc / limitFok / SELL marketIoc | 200 |

Не баги. Дополнение к «scaled»: в справочнике `qty` — «Total quantity scaled by the pair's base_quantity_scale», пример «100000»; на деле десятичная строка до 6 знаков (GET триггера отдаёт «0.513791»), «100» — это 100 AVAX (INSUFFICIENT_FUNDS).

""")
for lab in ["F5 без qty", "F14 qty 7 знаков 0.5137911 (шаг 0.000001)", "X2 SELL qty 100 AVAX (больше баланса)",
            "F16 лишнее поле в trigger (foo)", "X1 BUY stopLoss с marketIoc child", "X5 SELL с feeAsset=BASE", "F2 trigger = {}"]:
    w(raw(lab, req=lab.startswith("X2")))

w("""### 2.6 Трейлинг: дистанция на границах; trailingDistanceTicks — десятичная цена

В доках: `trailingDistanceBps` (int32) «Distance in basis points (1 bp = 0.01%).»; `trailingDistanceTicks` (string) «Distance as a price delta in 1e-9 quote-unit ticks.»; `maxSlippageBps`, `maxSlippageTicks` «Positive …»; границ нет.

| Значение | Ответ |
|---|---|
| bps 0, −1, 10001, 1000000 | 400 VALIDATION_ERROR «must be greater than 0 and less than or equal to 10000» |
| bps 1, 500, 9999, 10000 | 200 |
| bps «500» строкой / 1.5 | 400 BAD_REQUEST |
| ticks «0», «-1» | 400 VALIDATION_ERROR «must be greater than 0» |
| ticks «1», «100000000», «0.1», «0.000000001», «9223372036» | 200 |
| ticks «9223372037», «99999999999999» | 400 BAD_REQUEST «invalid trailingDistanceTicks: overflow» |
| ticks «0.0000000001» (10 знаков) | 400 BAD_REQUEST «too many fractional digits» |
| bps и ticks вместе / ни одного | 400 «exactly one of …» |
| maxSlippageBps 0, −1, 10001 / 10000 | 400 (0 < x ≤ 10000) / 200 |
| maxSlippageTicks 0, −1 | 400 «must be greater than 0» |
| side BUY | 400 «trailing_stop side must be SELL» |
| activationPrice «-1» / «22.8005» / «0» | 400 / 400 PRICE_TICK_SIZE / 200 (как «не задан») |

Дополнение к «scaled»: граница overflow ровно 9223372036 / 9223372037 = предел int64 / 1e9, а дробная часть — до 9 знаков. Значит, trailingDistanceTicks читается как десятичная цена (умножается на 1e9), а не как число тиков 1e-9: «1» — это 1 USDT. Обратно: если бы это были тики, «99999999999999» (1e14) поместилось бы в int64.
Коды TRAILING_DISTANCE_INVALID и MAX_SLIPPAGE_INVALID (есть в списке кодов справочника) не пришли ни разу; дистанция 9 223 372 036 USDT для монеты за ~11.5 USDT принята.

""")
for lab in ["TR2 bps 0", "TR7 bps 10001", "TR15 ticks '0.1' десятичной строкой", "TR32 ticks '9223372036' (≈ int64 / 1e9)",
            "TR33 ticks '9223372037' (> int64 / 1e9)", "TR34 ticks '0.000000001' (9 знаков)", "TR35 ticks '0.0000000001' (10 знаков)",
            "TR26 side BUY (трейлинг только SELL)"]:
    w(raw(lab, req=lab.startswith(("TR32", "TR33")), cut=900))
w(raw("TR15 ticks '0.1' десятичной строкой | GET", req=False, cut=1400))

w("""### 2.7 Ladder и TWAP

В доках ladder: levels (int32, required), postOnly (boolean, **required**), priceMin/priceMax, side. TWAP: durationMs, sliceIntervalMs (required), limitGtc или marketIoc, side. Границ нет. (TWAP с marketIoc не создавал — он покупал бы по рынку.)
На деле:
- ladder BUY 2 уровня 5.000–5.500 — RUNNING, два дочерних ордера (5.0 и 5.5 по 1.05); DELETE снимает оба (USER_REQUEST). Не баг.
- TWAP BUY limitGtc 5.500, 60 с / 30 с — RUNNING, первый слайс 1.05 по 5.5; DELETE снимает слайс; через 35 с новых слайсов нет. Не баг.
- levels: 0, −1, 1 — 400 «must be greater than or equal to 2 and less than or equal to 100». priceMin ≥ priceMax — 400. priceMin 0 — 400. Неверный шаг — PRICE_TICK_SIZE. Без side — 400.
- TWAP: durationMs 0/−1, sliceIntervalMs 0/−1, интервал больше длительности — 400 «slice_interval_ms must be between 100 and duration_ms». Без исполнения / цена 0 / неверный шаг — 400.
- **ladder без postOnly — 200**, уровни выставлены без post-only (postOnly в ордере отсутствует), хотя в справочнике поле required. Вопрос: is this by design? Пропуск поля молча даёт ордера, которые могут брать ликвидность.

""")
for lab in ["L1 ladder BUY 2 уровня 5.000–5.500 qty 2.1 (штатно) | events до DELETE",
            "L1 ladder BUY 2 уровня 5.000–5.500 qty 2.1 (штатно) | DELETE", "L4 levels 1", "L9 без postOnly",
            "TW6 slice больше duration (120 с / 60 с)"]:
    w(raw(lab, req=lab.startswith(("L9",)), cut=900))
kids9 = [e for e in tr_rows if e["label"].startswith("sweep GET") and "CpqTeo5DwFo" in e["response_raw"]]
for e in kids9:
    w(raw(e, req=False, cut=900))

w(f"""### 2.8 Чтение сразу после записи

В доках GET /v1/triggers/{{id}}: «Possible errors» 401, 403, 400, 404, 503. DELETE: «Final status (should be STATUS_CANCELED).»
На деле (10 попыток подряд, без пауз): GET сразу после успешного POST — 404 NOT_FOUND в {y404} из {len(y_get)}; через 0.3 с — всегда 200. GET сразу после DELETE, вернувшего CANCELED, — статус ARMED в {yarm} из {len(y_del)}; через 1 с — CANCELED.
Вопрос: is this by design? Бот, который читает триггер сразу после создания, получает «нет такого».
Прочее (не баги): повторный DELETE отменённого — 200 CANCELED; DELETE сработавшего/FAILED — 400 TRIGGER_CANCEL_REJECTED; несуществующий id: GET — 404 NOT_FOUND, DELETE — 404 TRIGGER_NOT_FOUND; id «0» — 400 INVALID_TRIGGER_ID.

""")
first404 = next((e for e in y_get if e["http"] == 404), None)
if first404:
    w(raw(first404, req=False))
    num = first404["label"].split(" ")[0]
    w(raw(f"{num} GET +0.3s после create", req=False, cut=500))
firstarm = next((e for e in y_del if (J(e) or {}).get("trigger", {}).get("status") == "ARMED"), None)
if firstarm:
    num = firstarm["label"].split(" ")[0]
    w(raw(f"{num} DELETE", req=False))
    w(raw(firstarm, req=False, cut=500))
    w(raw(f"{num} GET +1s после DELETE", req=False, cut=500))

w("""### 2.9 GET /v1/triggers: фильтры parentOrderId и status

В доках (query): `parentOrderId` (string, base58); `status` — «Repeatable or comma-separated TriggerStatus enum names»; значения статуса в той же странице: CREATED, ARMED, RUNNING, COMPLETED, CANCELED, FAILED, PAUSED.
На деле:
- parentOrderId не фильтрует: запрос с id родителя, у которого 2 ноги, с несуществующим id и совсем без фильтра дают одинаковый ответ (тот же nextPageToken, те же первые записи; ног этого родителя среди 200 записей — 2). Фильтр symbol работает (только ETH-USDT).
- status=CANCELED (и ARMED, RUNNING, «COMPLETED,CANCELED») — 400 INVALID_TRIGGER_STATUS «invalid status». Работает только с префиксом: status=STATUS_CANCELED, STATUS_RUNNING, «STATUS_COMPLETED,STATUS_FAILED». В ответах статус без префикса.
Найдено попутно: при уборке я искал ноги своего ордера по parentOrderId и получил все триггеры аккаунта; ничего не удалено (DELETE не вызывался), уборка теперь фильтрует ноги у себя.

""")
for lab in ["FILT7 parentOrderId=FzKTbH9PGF5 (2 ноги)", "FILT8 parentOrderId=2222222222 (нет такого)", "FILT9 без фильтров",
            "FILT6 status=CANCELED", "FILT6 status=STATUS_CANCELED", "FILT6 status=STATUS_COMPLETED,STATUS_FAILED"]:
    w(raw(lab, cut=420))

w("""### 2.10 Резерв монет (не баг)

В доках DELETE: «Cancel a trigger by trigger ID and release its reserved quantity.» На деле SELL-триггер 0.25 AVAX переводит 0.25 AVAX в reserved, DELETE возвращает. Соответствует.

""")
for lab in ["RSV балансы до", "RSV балансы с триггером", "RSV балансы после DELETE"]:
    e = ALL[lab]
    b = {x["symbol"]: (x["available"], x["reserved"]) for x in (J(e) or {}).get("balances", []) if x["symbol"] == "AVAX"}
    w(f"- {e['utc']} «{lab}»: AVAX available/reserved = {b.get('AVAX')}\n")
w("\n")

# ================= ЧАСТЬ 3 =================
w(f"""## Часть 3. Согласованность проверок и уборка

- Срабатывание «неверной стороны» требует сделки. В первом прогоне блок M (qty ниже минимума) 60 с не срабатывал — по публичным сделкам в эти окна не было ни одной; в окне W сделка была ровно в момент срабатывания (09:33:11.804). Противоречия нет; в перепроверке срабатывание ждёт факт новой сделки.
- Фиксированные clientTriggerId («a»×36, «b»×64) при повторном прогоне дали бы повтор и старый id. В перепроверке все id случайные.
- SELL-триггеры резервируют AVAX (2.10). Обычно держал их секунды (0.05–0.51 AVAX); в 2.2 — до первой сделки, суммарно 0.55 AVAX из ~3.36. INSUFFICIENT_FUNDS в X2 не из-за этого (100 AVAX больше всего баланса).
- Первый прогон: «qty 0.3 ниже минимума» — неверно, 0.3 × 22.8 = 6.84 ≥ 5; из выводов убрано.
- A3b: преемник по 132000 отклонён, исходный ордер остался WORKING, поэтому A4 заменил исходный — согласуется с 1.7.
- Первый прогон: «newClientOrderId 37 символов принят» — ошибка моего теста (строка была 33 символа). Исправлено: 37 → 400.
- Фильтр parentOrderId (2.9) — из-за него уборка чуть не взяла чужие триггеры; удалений не было, проверено по логу.
- 429 не было ни разу.
- Триггеры перепроверки: создано {len(ids_created)}, итог статусов {fin_counts}. COMPLETED/FAILED — сработавшие в 2.2 и FAILED из 2.3/2.7; их дочерние ордера сняты по id. Все ордера replace-batch закрыты (проверено по id). RUNNING-триггеры на LTC — не мои (ноги автора, созданы 08:09), не трогал.

## Не покрыто

- AMENDED в replace-batch (случай «без преемника»): не проверено; когда он возвращается, спрошено у команды.
- Трейлинг/стоп с рыночным дочерним ордером при срабатывании — продал бы монеты общего счёта; не делал.
""")

open(OUT, "w", encoding="utf-8").write("".join(o))
print("ok", OUT, len("".join(o)))
