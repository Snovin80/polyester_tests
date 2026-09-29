"""ПЕРЕПРОВЕРКА ОТЧЁТА 29.09 (docs/report_2026-09-29.txt): replace-batch и триггеры.
Пункты: replace-batch всегда REPLACED вместо AMENDED; oldOrderId «1»; replacementOrderId при CONFLICT;
ордер чужой пары (UNKNOWN_SYMBOL); цена привязанного TP не по шагу; clientTriggerId 65+ → 503;
повтор clientTriggerId → 502; 404 сразу после создания / ARMED после DELETE; фильтры parentOrderId и status;
ladder без postOnly; trailingDistanceTicks overflow.

ВНИМАНИЕ: ставит маленькие ордера (BUY ETH-USDT ~7 USDT на 50 % ниже рынка) и триггеры BUY на AVAX-USDT
с дочерним ордером на 50 % ниже рынка — исполниться не могут. В конце снимает ТОЛЬКО свои по id
(cancel-all не вызывает). Нужно ~80 USDT свободных на время работы (~1 мин).
Запуск из папки polyester_tests (нужен .env с ключами):
    py -3.12 tests\\recheck_2026-09-29.py
Как читать: [ВСЁ ЕЩЁ] — ошибка на месте; [ИСПРАВЛЕНО] — починили; [ИЗМЕНИЛОСЬ] — ответ другой,
смотреть подробно; [НЕ ПРОВЕРЕНО] — не удалось. Итог — docs/recheck_2026-09-29_result.txt."""
import json
import os
import random
import string
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api_client import PolyesterClient, _canonical_query  # noqa: E402

C = PolyesterClient()
LINES = []
MY_ORDERS = {}      # orderId -> symbol
MY_TRIGGERS = []


def out(tag, name, detail):
    line = f"[{tag}] {name}: {detail}"
    print(line)
    LINES.append(line)


def call(method, path, query="", body=None):
    canon = _canonical_query(query)
    bb = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
    h = C._sign(method, path, canon, bb)
    url = C.base_url + path + ("?" + canon if canon else "")
    r = C._session.request(method, url, headers=h, data=bb if body is not None else None, timeout=20)
    try:
        j = r.json()
    except Exception:
        j = None
    return r.status_code, j, r.text


def best(symbol):
    st, j, t = call("GET", f"/v1/orderbook/{symbol}", "depth=5")
    return Decimal(j["bids"][0][0]), Decimal(j["asks"][0][0])


def q(x, step):
    return x.quantize(Decimal(step), rounding=ROUND_DOWN)


def rid():
    return str(uuid.uuid4())


def order(symbol, price, qty):
    body = {"order": {"symbol": symbol, "side": "BUY", "baseQty": str(qty), "clientOrderId": "rck-" + rid()[:20],
                      "selfTradePreventionMode": "EXPIRE_MAKER", "feeAsset": "QUOTE",
                      "limitGtc": {"price": str(price), "postOnly": True}}}
    st, j, t = call("POST", "/v1/orders", body=body)
    oid = (j or {}).get("orderId") if isinstance(j, dict) else None
    if oid:
        MY_ORDERS[oid] = symbol
    return oid, body["order"]["clientOrderId"], st, t


def rb(symbol, items):
    st, j, t = call("POST", "/v1/orders/replace-batch", body={"symbol": symbol, "requestId": rid(), "items": items})
    res = ((j or {}).get("results") or [{}]) if isinstance(j, dict) else [{}]
    for it in res:
        v = it.get("replacementOrderId")
        if v and v != "1":
            MY_ORDERS[v] = symbol
    return st, res, t


def trig(body_trigger):
    st, j, t = call("POST", "/v1/triggers", body={"trigger": body_trigger})
    tid = (j or {}).get("triggerId") if isinstance(j, dict) else None
    if tid:
        MY_TRIGGERS.append(tid)
    return st, tid, t


def rand_cid(n):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def check_replace_batch():
    bid, ask = best("ETH-USDT")
    price = q(bid * Decimal("0.5"), "0.01")
    qty = (Decimal(8) / price).quantize(Decimal("0.000001"), rounding=ROUND_UP)
    qs = int(qty * 1000000)
    a, a_cid, st, t = order("ETH-USDT", price, qty)
    b, b_cid, st2, t2 = order("ETH-USDT", price - Decimal("0.01"), qty)
    if not a or not b:
        out("НЕ ПРОВЕРЕНО", "replace-batch", f"ордер не создан: {t[:120]}")
        return
    time.sleep(0.8)
    # 1. AMENDED
    st, res, t = rb("ETH-USDT", [{"orderId": a, "newQtyScaled": int(qs * 0.9)}])
    act = res[0].get("actionTaken")
    out("ВСЁ ЕЩЁ" if act == "REPLACED" else ("ИСПРАВЛЕНО" if act == "AMENDED" else "ИЗМЕНИЛОСЬ"),
        "replace-batch: уменьшение qty → REPLACED вместо AMENDED", f"actionTaken={act}, ответ {t[:140]}")
    a = res[0].get("replacementOrderId") if act == "REPLACED" else a
    time.sleep(0.5)
    # 2. oldOrderId «1» для неизвестного id
    st, res, t = rb("ETH-USDT", [{"orderId": "5USXJZm2", "newQtyScaled": qs}])
    old = res[0].get("oldOrderId")
    out("ВСЁ ЕЩЁ" if old == "1" else "ИСПРАВЛЕНО", "replace-batch: oldOrderId «1» у неизвестного id",
        f"oldOrderId={old}, code={res[0].get('code')}")
    # 3. CONFLICT: replacementOrderId = oldOrderId
    st, res, t = rb("ETH-USDT", [{"orderId": a, "newQtyScaled": int(qs * 0.8), "newClientOrderId": b_cid}])
    r0 = res[0]
    if r0.get("code") == "CONFLICT_DUPLICATE_CLIENT_ORDER_ID":
        out("ВСЁ ЕЩЁ" if r0.get("replacementOrderId") == r0.get("oldOrderId") else "ИСПРАВЛЕНО",
            "replace-batch: replacementOrderId = oldOrderId при CONFLICT",
            f"old={r0.get('oldOrderId')} replacement={r0.get('replacementOrderId')}")
    else:
        out("ИЗМЕНИЛОСЬ", "replace-batch: повтор clientOrderId", f"ответ {t[:160]}")
        if r0.get("replacementOrderId") not in (None, "1"):
            a = r0["replacementOrderId"]
    # 4. ордер другой пары
    abid, aask = best("AVAX-USDT")
    ap = q(abid * Decimal("0.5"), "0.001")
    aq = (Decimal(8) / ap).quantize(Decimal("0.000001"), rounding=ROUND_UP)
    av, _, st, t = order("AVAX-USDT", ap, aq)
    if av:
        time.sleep(0.5)
        st, res, t = rb("ETH-USDT", [{"orderId": av, "newPriceTicks": str(q(ap * Decimal("0.99"), "0.01"))}])
        code = res[0].get("code")
        out("ВСЁ ЕЩЁ" if code == "UNKNOWN_SYMBOL" else "ИЗМЕНИЛОСЬ", "replace-batch: ордер другой пары → UNKNOWN_SYMBOL",
            f"code={code}")
    # 5. цена привязанного TP не по шагу
    tp = str(q(ask * 2, "0.01")) + "5"
    st, res, t = rb("ETH-USDT", [{"orderId": a, "newAttachedRisk": {"oco": False, "takeProfit": {
        "triggerPrice": tp, "child": {"marketIoc": {}}}}}])
    s0 = res[0].get("status")
    out("ВСЁ ЕЩЁ" if s0 == "ADMITTED" else "ИСПРАВЛЕНО", f"replace-batch: TP {tp} (шаг 0.01) принимается",
        f"HTTP {st}, status={s0}, code={res[0].get('code')} {t[:100] if st != 200 else ''}")


def trig_body(mid, cid, qty):
    child = q(mid * Decimal("0.5"), "0.001")
    return {"symbol": "AVAX-USDT", "qty": str(qty), "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER",
            "clientTriggerId": cid, "stopLoss": {"side": "BUY", "triggerPrice": str(q(mid * Decimal("1.2"), "0.001")),
                                                  "child": {"limitGtc": {"price": str(child), "postOnly": True}}}}


def check_triggers():
    bid, ask = best("AVAX-USDT")
    mid = (bid + ask) / 2
    qty = (Decimal(6) / q(mid * Decimal("0.5"), "0.001")).quantize(Decimal("0.000001"), rounding=ROUND_UP)
    # 6. clientTriggerId 65
    st, tid, t = trig(trig_body(mid, rand_cid(65), qty))
    out("ВСЁ ЕЩЁ" if st == 503 else ("ИСПРАВЛЕНО" if st == 400 else "ИЗМЕНИЛОСЬ"), "clientTriggerId 65 символов → 503",
        f"HTTP {st} {t[:120]}")
    # 7. повтор clientTriggerId с другим телом
    cid = "rck-" + rand_cid(12)
    st, tid, t = trig(trig_body(mid, cid, qty))
    if tid:
        st2, _, t2 = trig(trig_body(mid, cid, qty + Decimal("0.1")))
        out("ВСЁ ЕЩЁ" if st2 == 502 else ("ИСПРАВЛЕНО" if st2 in (400, 409) else "ИЗМЕНИЛОСЬ"),
            "повтор clientTriggerId с другим телом → 502", f"HTTP {st2} {t2[:100] if not t2.lstrip().startswith('<') else '[HTML Cloudflare]'}")
    else:
        out("НЕ ПРОВЕРЕНО", "повтор clientTriggerId", f"первый не создан: HTTP {st} {t[:120]}")
    # 8. 404 сразу после создания / ARMED после DELETE
    n404 = narmed = n = 0
    for i in range(10):
        st, tid, t = trig(trig_body(mid, "rck-" + rand_cid(12), qty))
        if not tid:
            continue
        n += 1
        s1, j1, t1 = call("GET", f"/v1/triggers/{tid}")
        n404 += (s1 == 404)
        call("DELETE", f"/v1/triggers/{tid}")
        s2, j2, t2 = call("GET", f"/v1/triggers/{tid}")
        narmed += ((j2 or {}).get("trigger", {}).get("status") == "ARMED") if isinstance(j2, dict) else 0
    out("ВСЁ ЕЩЁ" if n404 or narmed else "ИСПРАВЛЕНО", "GET сразу после POST → 404 / сразу после DELETE → ARMED",
        f"из {n}: 404 — {n404}, ARMED после DELETE — {narmed}")
    # 9. фильтры
    s_all, j_all, _ = call("GET", "/v1/triggers", "limit=20")
    s_f, j_f, t_f = call("GET", "/v1/triggers", "limit=20&parentOrderId=5USXJZm2")
    ids_all = [x.get("triggerId") for x in (j_all or {}).get("triggers", [])] if isinstance(j_all, dict) else []
    ids_f = [x.get("triggerId") for x in (j_f or {}).get("triggers", [])] if isinstance(j_f, dict) else []
    out("ВСЁ ЕЩЁ" if s_f == 200 and ids_f and ids_f == ids_all else "ИСПРАВЛЕНО",
        "фильтр parentOrderId игнорируется", f"HTTP {s_f}, с несуществующим родителем {len(ids_f)} записей")
    s_st, _, t_st = call("GET", "/v1/triggers", "status=CANCELED&limit=5")
    out("ВСЁ ЕЩЁ" if s_st == 400 else "ИСПРАВЛЕНО", "фильтр status=CANCELED → 400", f"HTTP {s_st} {t_st[:80]}")
    # 10. ladder без postOnly
    pmin = q(mid * Decimal("0.45"), "0.001")
    pmax = q(mid * Decimal("0.5"), "0.001")
    lq = (Decimal(12) / pmin).quantize(Decimal("0.000001"), rounding=ROUND_UP)
    st, tid, t = trig({"symbol": "AVAX-USDT", "qty": str(lq), "feeAsset": "QUOTE", "clientTriggerId": "rck-" + rand_cid(12),
                       "selfTradePreventionMode": "EXPIRE_MAKER",
                       "ladder": {"side": "BUY", "levels": 2, "priceMin": str(pmin), "priceMax": str(pmax)}})
    if tid:
        time.sleep(2)
        s, j, _ = call("GET", f"/v1/triggers/{tid}/events")
        kids = [e["childOrderId"] for e in (j or {}).get("events", []) if e.get("childOrderId") not in (None, "1")]
        for k in kids:
            MY_ORDERS[k] = "AVAX-USDT"
        po = []
        for k in kids:
            s, jo, _ = call("GET", f"/v1/spot/orders/{k}")
            po.append(((jo or {}).get("order") or {}).get("postOnly"))
        out("ВСЁ ЕЩЁ", "ladder без postOnly принимается (в REST-справочнике required)", f"создан, postOnly уровней: {po}")
    else:
        out("ИСПРАВЛЕНО" if st == 400 else "ИЗМЕНИЛОСЬ", "ladder без postOnly", f"HTTP {st} {t[:120]}")
    # 11. trailingDistanceTicks overflow на 9223372037
    st, tid, t = trig({"symbol": "AVAX-USDT", "qty": "0.1", "feeAsset": "QUOTE", "clientTriggerId": "rck-" + rand_cid(12),
                       "selfTradePreventionMode": "EXPIRE_MAKER",
                       "trailingStop": {"side": "SELL", "activationPrice": str(q(mid * 2, "0.001")),
                                        "trailingDistanceTicks": "9223372037"}})
    if "overflow" in t:
        out("ВСЁ ЕЩЁ", "trailingDistanceTicks 9223372037 → overflow (читается как цена ×1e9)", f"HTTP {st} {t[:100]}")
    elif "INSUFFICIENT_FUNDS" in t:
        out("НЕ ПРОВЕРЕНО", "trailingDistanceTicks", "нет AVAX на счёте для SELL-трейлинга")
    else:
        out("ИЗМЕНИЛОСЬ", "trailingDistanceTicks 9223372037", f"HTTP {st} {t[:120]}")


def cleanup():
    print("--- уборка своего ---")
    for tid in MY_TRIGGERS:
        s, j, _ = call("GET", f"/v1/triggers/{tid}")
        stt = ((j or {}).get("trigger") or {}).get("status") if isinstance(j, dict) else None
        if stt not in ("CANCELED", "COMPLETED", "FAILED"):
            call("DELETE", f"/v1/triggers/{tid}")
    time.sleep(1)
    for oid, sym in MY_ORDERS.items():
        s, j, _ = call("GET", f"/v1/spot/orders/{oid}")
        stt = ((j or {}).get("order") or {}).get("status") if isinstance(j, dict) else None
        if stt and stt not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
            call("POST", "/v1/orders/cancel", body={"symbol": sym, "orderId": oid})
    left = []
    for oid in MY_ORDERS:
        s, j, _ = call("GET", f"/v1/spot/orders/{oid}")
        stt = ((j or {}).get("order") or {}).get("status") if isinstance(j, dict) else None
        if stt not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
            left.append((oid, stt))
    print("открытых своих ордеров после уборки:", left or "нет")
    LINES.append(f"уборка: своих ордеров {len(MY_ORDERS)}, триггеров {len(MY_TRIGGERS)}, открытых осталось: {left or 'нет'}")


def main():
    print("Перепроверка отчёта 29.09 —", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
          "— Python", sys.version.split()[0])
    try:
        check_replace_batch()
        check_triggers()
    finally:
        cleanup()
    path = os.path.join(ROOT, "docs", "recheck_2026-09-29_result.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") + "\n" + "\n".join(LINES) + "\n")
    print("\nИтог сохранён:", path)


if __name__ == "__main__":
    main()
