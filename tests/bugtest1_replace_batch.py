"""bugtest1: POST /v1/orders/replace-batch на живом тестнете (ETH-USDT).
Ордера — BUY 0.005 ETH по 1350 (≈50 % ниже рынка), post-only, не исполнятся.
Каждый вызов пишется дословно в docs/raw_replace_batch_2026-09-29.jsonl.
Уборка в finally — только свои id (включая replacementOrderId).
Запуск: python3 tests/bugtest1_replace_batch.py [A|B|C ...]  (без аргументов — все блоки)"""
import json
import sys
import time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from rb_common import Rec, SYMBOL  # noqa: E402

P = "/v1/orders/replace-batch"
FAKE = "2222222222"  # заведомо несуществующий id (base58, без 0/O/I/l)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def longid(prefix, n):
    """Уникальная строка ровно n символов (без повторов между прогонами)."""
    import uuid
    return (prefix + uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex)[:n]


def fake_id(i):
    """Валидный по алфавиту base58, но несуществующий orderId (8 символов)."""
    return "5USXJZm" + B58[i]


def rb(r, label, items, request_id=None, extra=None):
    body = {"symbol": SYMBOL, "requestId": request_id or r.rid(), "items": items}
    if extra:
        body.update(extra)
    http, p = r.call(label, "POST", P, body=body)
    if isinstance(p, dict):
        for it in p.get("results") or []:
            v = it.get("replacementOrderId")
            if v and v != "1":          # "1" = нулевой id в base58 (нет преемника)
                r.own_ids.append(str(v))
    return http, p


def succ(p, fallback):
    """id преемника, если он выдан, иначе прежний."""
    try:
        v = p["results"][0].get("replacementOrderId")
        return v if v and v != "1" else fallback
    except Exception:
        return fallback


def st(r, label, oid, pause=0.6):
    """Читает ордер и печатает одну строку состояния."""
    time.sleep(pause)
    http, p = r.get_order(label, oid)
    o = (p or {}).get("order") if isinstance(p, dict) else None
    if o:
        print(f"    >> {oid}: {o['status']} price={o['price']} origQty={o['origQty']} "
              f"leaves={o['leavesQty']} gen={o['lineage']['generation']} "
              f"terminal={o.get('terminalReason','-')}")
    return o


def block_a(r):
    """A. newPriceTicks: десятичная цена или тики."""
    o = r.new_order("A0 create @1350")
    rb_p = lambda l, price: rb(r, l, [{"orderId": o, "newPriceTicks": price, "newQtyScaled": 5000}])
    h, p = rb_p("A1 newPriceTicks string '1340.00'", "1340.00")
    st(r, "A1 get old", o); o = succ(p, o); st(r, "A1 get succ", o)
    h, p = rb(r, "A2 newPriceTicks JSON number 133000",
              [{"orderId": o, "newPriceTicks": 133000, "newQtyScaled": 5000}])
    st(r, "A2 get", o)
    h, p = rb_p("A3 newPriceTicks string '1320' (целое без точки)", "1320")
    st(r, "A3 get old", o); o = succ(p, o); st(r, "A3 get succ", o)
    h, p = rb_p("A3b обратная проверка: '132000' (если бы тики — 1320.00)", "132000")
    st(r, "A3b get old", o); s3 = succ(p, o)
    if s3 != o: st(r, "A3b get succ", s3)
    h, p = rb(r, "A4 только цена, qty не передан",
              [{"orderId": o, "newPriceTicks": "1310.00"}])
    st(r, "A4 get old", o); o = succ(p, o); st(r, "A4 get succ", o)


def block_b(r):
    """B. Что значит AMENDED: qty вниз/вверх."""
    o = r.new_order("B0 create @1350 qty 0.005")
    # qty вниз, цена та же (передаём текущую цену явно)
    h, p = rb(r, "B1 qty вниз 5000->4500, цена не передана",
              [{"orderId": o, "newQtyScaled": 4500}])
    st(r, "B1 get old", o); o2 = succ(p, o)
    if o2 != o: st(r, "B1 get succ", o2)
    o = o2
    # qty вниз с явной той же ценой
    h, p = rb(r, "B2 qty вниз ->4000, цена 1350 явно",
              [{"orderId": o, "newPriceTicks": "1350", "newQtyScaled": 4000}])
    st(r, "B2 get old", o); o2 = succ(p, o)
    if o2 != o: st(r, "B2 get succ", o2)
    o = o2
    # qty вверх
    h, p = rb(r, "B3 qty вверх ->6000",
              [{"orderId": o, "newQtyScaled": 6000}])
    st(r, "B3 get old", o); o2 = succ(p, o)
    if o2 != o: st(r, "B3 get succ", o2)


def block_c(r):
    """C. Границы."""
    r0 = r.new_order("C0 create @1350")
    r1 = r.new_order("C0b create @1350")
    rb(r, "C1 пустой items", [])
    rb(r, "C2 items отсутствует", None)  # items: null
    ids50 = [fake_id(i) for i in range(50)]
    ids51 = [fake_id(i) for i in range(51)]
    h, p = rb(r, "C3 50 элементов (несуществующие id)",
              [{"orderId": i, "newQtyScaled": 4000} for i in ids50])
    if p: print("    >> status", p.get("status"), "accepted", p.get("acceptedCount"), "rejected", p.get("rejectedCount"),
                "results", len(p.get("results") or []))
    h, p = rb(r, "C4 51 элемент (несуществующие id)",
              [{"orderId": i, "newQtyScaled": 4000} for i in ids51])
    rb(r, "C5 qty 0", [{"orderId": r0, "newQtyScaled": 0}])
    st(r, "C5 get", r0)
    rb(r, "C6 qty отрицательное -1000", [{"orderId": r0, "newQtyScaled": -1000}])
    rb(r, "C7 пункт без изменений (только orderId)", [{"orderId": r0}])
    st(r, "C7 get", r0)
    rb(r, "C8 пункт с теми же цена/qty (1350 / 5000)",
       [{"orderId": r0, "newPriceTicks": "1350", "newQtyScaled": 5000}])
    st(r, "C8 get", r0)
    h, p = rb(r, "C9 один ордер дважды (qty 4000 и 3000)",
              [{"orderId": r1, "newQtyScaled": 4000}, {"orderId": r1, "newQtyScaled": 3000}])
    st(r, "C9 get old", r1)
    if p and p.get("results"):
        for it in p["results"]:
            v = it.get("replacementOrderId")
            if v and v != "1": st(r, f"C9 get succ {v}", v)
    rb(r, "C10 несуществующий orderId", [{"orderId": FAKE, "newQtyScaled": 4000}])
    # C11 повтор requestId: тот же body дважды, потом другой body с тем же requestId
    rid = r.rid()
    o = r.new_order("C11 create @1350")
    body = [{"orderId": o, "newQtyScaled": 4000}]
    h, p = rb(r, "C11a requestId первый раз", body, request_id=rid)
    o_new = succ(p, o)
    rb(r, "C11b тот же requestId, тот же body", body, request_id=rid)
    rb(r, "C11c тот же requestId, другой body (qty 3000)",
       [{"orderId": o_new, "newQtyScaled": 3000}], request_id=rid)
    st(r, "C11 get", o_new)
    # доп. границы цены/qty
    o = o_new
    for lbl, item in [
        ("C12 цена 0", {"orderId": o, "newPriceTicks": "0"}),
        ("C13 цена отрицательная -1", {"orderId": o, "newPriceTicks": "-1"}),
        ("C14 лишний знак цены 1340.001", {"orderId": o, "newPriceTicks": "1340.001"}),
        ("C15 qty ниже минимума (50 = 0.00005 ETH)", {"orderId": o, "newQtyScaled": 50}),
        ("C16 notional ниже минимума (qty 1000 = 0.001 ETH * 1350)", {"orderId": o, "newQtyScaled": 1000}),
    ]:
        rb(r, lbl, [item])
        st(r, lbl + " get", o)


def block_d(r):
    """D. Поля ответа, смешанный пакет, newClientOrderId, контраст с modify."""
    a = r.new_order("D0a create @1350")
    b = r.new_order("D0b create @1350")
    # два реальных пункта: itemIndex 1 в ответе?
    h, p = rb(r, "D1 два реальных пункта (цена 1340 и qty 4500)",
              [{"orderId": a, "newPriceTicks": "1340"}, {"orderId": b, "newQtyScaled": 4500}])
    a2 = succ(p, a)
    b2 = b
    try:
        b2 = p["results"][1].get("replacementOrderId") or b
    except Exception:
        pass
    st(r, "D1 get succ a", a2); st(r, "D1 get succ b", b2)
    # смешанный: реальный + несуществующий -> PARTIALLY_ADMITTED?
    h, p = rb(r, "D2 смешанный: реальный + несуществующий",
              [{"orderId": a2, "newPriceTicks": "1330"}, {"orderId": fake_id(1), "newQtyScaled": 4500}])
    a3 = succ(p, a2)
    st(r, "D2 get a", a3)
    # newClientOrderId: возвращается ли clientOrderId в results?
    ncid = "rbt-newcid-" + r.rid()[:8]
    h, p = rb(r, "D3 newClientOrderId " + ncid,
              [{"orderId": a3, "newPriceTicks": "1320", "newClientOrderId": ncid}])
    a4 = succ(p, a3)
    st(r, "D3 get succ", a4)
    # контраст: modify qty вниз на свежем ордере (справочник modify: AMENDED, id тот же)
    m = r.new_order("D4 create for modify @1350")
    http, p = r.call("D4 modify qty вниз 5000->4500", "POST", "/v1/orders/modify",
                     body={"symbol": SYMBOL, "requestId": r.rid(), "orderId": m,
                           "newQtyScaled": 4500, "behavior": "AMEND_OR_REPLACE"})
    st(r, "D4 get old", m)
    try:
        fin = p.get("finalOrderId")
        if fin and fin != m:
            r.own_ids.append(fin); st(r, "D4 get final", fin)
    except Exception:
        pass


def block_e(r):
    """E. Приём (ADMITTED) не значит успех: преемник post-only пересекает рынок."""
    o = r.new_order("E0 create @1350")
    h, p = rb(r, "E1 цена 3000 (выше лучшего ask, post-only пересечёт)",
              [{"orderId": o, "newPriceTicks": "3000"}])
    st(r, "E1 get old", o)
    s2 = succ(p, o)
    if s2 != o: st(r, "E1 get succ", s2)


def block_f(r):
    """F. Чужой символ, закрытый ордер, newClientOrderId, subaccountId, newAttachedRisk, modify без изменений."""
    # F1: ордер AVAX-USDT, а в запросе symbol ETH-USDT
    body = {"order": {"symbol": "AVAX-USDT", "side": "BUY", "baseQty": "1.013791",
                      "clientOrderId": r.c.new_client_order_id("rbt"), "selfTradePreventionMode": "EXPIRE_MAKER",
                      "feeAsset": "QUOTE", "limitGtc": {"price": "5.700", "postOnly": True}}}
    http, p = r.call("F0 create AVAX-USDT BUY 1.013791 @5.700", "POST", "/v1/orders", body=body)
    av = p.get("orderId") if isinstance(p, dict) else None
    if av:
        r.own_ids.append(av); r.own_sym[av] = "AVAX-USDT"
        h, p = rb(r, "F1 symbol ETH-USDT, orderId от AVAX-USDT, цена 5.600",
                  [{"orderId": av, "newPriceTicks": "5.600"}])
        st(r, "F1 get AVAX order", av)
        s1 = succ(p, av)
        if s1 != av:
            r.own_sym[s1] = "AVAX-USDT"; st(r, "F1 get succ", s1)
    # F2: уже отменённый ордер; F3: старый id после замены
    o = r.new_order("F2 create @1350")
    r.call("F2 cancel", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": o})
    st(r, "F2 get after cancel", o, pause=1.0)
    rb(r, "F2 replace отменённого ордера", [{"orderId": o, "newQtyScaled": 4500}])
    o = r.new_order("F3 create @1350")
    h, p = rb(r, "F3a replace (получить преемника)", [{"orderId": o, "newQtyScaled": 4500}])
    s3 = succ(p, o)
    time.sleep(0.8)
    rb(r, "F3b replace старого id (ORDER_REPLACED)", [{"orderId": o, "newQtyScaled": 4000}])
    # F4-F6: newClientOrderId
    other = r.new_order("F4 create второй ордер @1349")
    http, p = r.get_order("F4 get второй ордер", other)
    other_cid = ((p or {}).get("order") or {}).get("clientOrderId")
    h, p = rb(r, "F4 newClientOrderId = clientOrderId другого живого ордера",
              [{"orderId": s3, "newQtyScaled": 4500, "newClientOrderId": other_cid}])
    s3 = succ(p, s3)
    h, p = rb(r, "F5 newClientOrderId 37 символов", [{"orderId": s3, "newQtyScaled": 4600, "newClientOrderId": longid("c", 37)}])
    s3 = succ(p, s3)
    cid36 = longid("d", 36)
    h, p = rb(r, "F6 newClientOrderId 36 символов (контроль)", [{"orderId": s3, "newQtyScaled": 4700, "newClientOrderId": cid36}])
    s3 = succ(p, s3)
    st(r, "F6 get succ", s3)
    # F7: subaccountId
    rb(r, "F7a subaccountId чужой/неверный 'zzzz'", [{"orderId": s3, "newQtyScaled": 4800}], extra={"subaccountId": "zzzz"})
    h, p = rb(r, "F7b subaccountId свой корневой RCx3H2SjGz6", [{"orderId": s3, "newQtyScaled": 4800}],
              extra={"subaccountId": "RCx3H2SjGz6"})
    s3 = succ(p, s3)
    # F8: newAttachedRisk
    a = r.new_order("F8 create @1350 без attachedRisk")
    risk = {"oco": False, "takeProfit": {"triggerPrice": "3500", "child": {"limitGtc": {"price": "3500", "postOnly": True}}}}
    h, p = rb(r, "F8a newAttachedRisk takeProfit 3500 (limitGtc)", [{"orderId": a, "newPriceTicks": "1340", "newAttachedRisk": risk}])
    a2 = succ(p, a)
    time.sleep(0.8)
    r.call("F8a get succ с attachedRisk", "GET", f"/v1/spot/orders/{a2}",
           query={"includeAttachedRiskState": "true", "includeAttachedRisk": "true"})
    http, p = r.call("F8a triggers по parentOrderId", "GET", "/v1/triggers", query={"parentOrderId": a2, "limit": 50})
    for t in (p or {}).get("triggers", []) if isinstance(p, dict) else []:
        if t.get("parentOrderId") == a2:          # фильтр API не работает — берём только свои ноги
            r.own_triggers.append(t["triggerId"])
    h, p = rb(r, "F8b newAttachedRisk как в примере справочника (triggerPrice 0.005, marketIoc)",
              [{"orderId": a2, "newAttachedRisk": {"oco": False,
                "stopLoss": {"child": {"marketIoc": {}}, "triggerPrice": "0.005"},
                "takeProfit": {"child": {"marketIoc": {}}, "triggerPrice": "0.005"}}}])
    a3 = succ(p, a2)
    r.call("F8c cancel родителя", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": a3})
    time.sleep(1.5)
    r.call("F8c get родителя после cancel", "GET", f"/v1/spot/orders/{a3}",
           query={"includeAttachedRiskState": "true", "includeAttachedRisk": "true"})
    for pid in dict.fromkeys([a2, a3]):
        http, p = r.call(f"F8c triggers по parentOrderId {pid} после cancel", "GET", "/v1/triggers",
                         query={"parentOrderId": pid, "limit": 50})
        for t in (p or {}).get("triggers", []) if isinstance(p, dict) else []:
            if t.get("parentOrderId") == pid:     # только ноги своего ордера
                r.own_triggers.append(t["triggerId"])
    # F9: modify без изменений (сравнение с C8)
    m = r.new_order("F9 create @1350")
    http, p = r.call("F9 modify те же цена/qty (1350 / 5000)", "POST", "/v1/orders/modify",
                     body={"symbol": SYMBOL, "requestId": r.rid(), "orderId": m, "newQtyScaled": 5000,
                           "newPrice": "1350", "behavior": "AMEND_OR_REPLACE"})
    st(r, "F9 get", m)
    fin = p.get("finalOrderId") if isinstance(p, dict) else None
    if fin and fin != m:
        r.own_ids.append(fin); st(r, "F9 get final", fin)


def order_body(price="1350.00", attached=None, symbol=None):
    b = {"symbol": symbol or SYMBOL, "side": "BUY", "baseQty": "0.005" if not symbol else "1.013791",
         "clientOrderId": longid("rbt-", 24), "selfTradePreventionMode": "EXPIRE_MAKER", "feeAsset": "QUOTE",
         "limitGtc": {"price": price, "postOnly": True}}
    if attached:
        b["attachedRisk"] = attached
    return {"order": b}


def create_raw(r, label, body):
    http, p = r.call(label, "POST", "/v1/orders", body=body)
    oid = p.get("orderId") if isinstance(p, dict) else None
    if oid:
        r.own_ids.append(oid)
    return oid


def legs_of(r, label, pid):
    """Ноги привязанного риска — только с parentOrderId == pid (фильтр API игнорируется)."""
    http, p = r.call(label, "GET", "/v1/triggers", query={"parentOrderId": pid, "limit": 200})
    legs = [t for t in ((p or {}).get("triggers", []) if isinstance(p, dict) else []) if t.get("parentOrderId") == pid]
    for t in legs:
        r.own_triggers.append(t["triggerId"])
    print(f"    >> ноги {pid}: {[(t['triggerId'], t['status'], t.get('cancelReason')) for t in legs]}")
    return legs


def block_g(r):
    """G. Длина newClientOrderId, newAttachedRisk против создания ордера, коды для закрытого ордера."""
    o = r.new_order("G0 create @1350")
    for n in (36, 37, 64, 100):
        h, p = rb(r, f"G1 newClientOrderId {n} символов", [{"orderId": o, "newQtyScaled": 5000 - n, "newClientOrderId": longid("g", n)}])
        o = succ(p, o)
    st(r, "G1 get succ", o)
    # G2: newAttachedRisk без postOnly
    tp = {"oco": False, "takeProfit": {"triggerPrice": "3500", "child": {"limitGtc": {"price": "3500"}}}}
    h, p = rb(r, "G2 newAttachedRisk takeProfit 3500 limitGtc (без postOnly)", [{"orderId": o, "newAttachedRisk": tp}])
    o = succ(p, o)
    time.sleep(0.8)
    r.call("G2 get succ с attachedRisk", "GET", f"/v1/spot/orders/{o}",
           query={"includeAttachedRiskState": "true", "includeAttachedRisk": "true"})
    # G3/G4: неверный шаг и TP ниже цены входа — replace-batch против создания ордера
    bad_tick = {"oco": False, "takeProfit": {"triggerPrice": "3500.005", "child": {"marketIoc": {}}}}
    tp_below = {"oco": False, "takeProfit": {"triggerPrice": "1000", "child": {"marketIoc": {}}}}
    h, p = rb(r, "G3a replace-batch newAttachedRisk TP 3500.005 (шаг 0.01)", [{"orderId": o, "newAttachedRisk": bad_tick}])
    o = succ(p, o)
    create_raw(r, "G3b create ордера с attachedRisk TP 3500.005", order_body(attached=bad_tick))
    h, p = rb(r, "G4a replace-batch newAttachedRisk TP 1000 (ниже цены входа 1350)", [{"orderId": o, "newAttachedRisk": tp_below}])
    o = succ(p, o)
    create_raw(r, "G4b create ордера с attachedRisk TP 1000 (ниже цены входа 1350)", order_body(attached=tp_below))
    ex = {"oco": False, "stopLoss": {"child": {"marketIoc": {}}, "triggerPrice": "0.005"},
          "takeProfit": {"child": {"marketIoc": {}}, "triggerPrice": "0.005"}}
    h, p = rb(r, "G5a replace-batch newAttachedRisk как в примере (0.005/0.005)", [{"orderId": o, "newAttachedRisk": ex}])
    o = succ(p, o)
    create_raw(r, "G5b create ордера с attachedRisk как в примере (0.005/0.005)", order_body(attached=ex))
    time.sleep(0.8)
    r.call("G5 get succ с attachedRisk", "GET", f"/v1/spot/orders/{o}",
           query={"includeAttachedRiskState": "true", "includeAttachedRisk": "true"})
    r.call("G6 cancel родителя", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": o})
    time.sleep(1.5)
    r.call("G6 get родителя после cancel", "GET", f"/v1/spot/orders/{o}",
           query={"includeAttachedRiskState": "true", "includeAttachedRisk": "true"})
    legs_of(r, "G6 ноги после cancel", o)
    # G7: закрытый ордер — replace-batch против modify
    rb(r, "G7a replace-batch отменённого ордера", [{"orderId": o, "newQtyScaled": 4500}])
    r.call("G7b modify отменённого ордера", "POST", "/v1/orders/modify",
           body={"symbol": SYMBOL, "requestId": r.rid(), "orderId": o, "newQtyScaled": 4500, "behavior": "AMEND_OR_REPLACE"})
    # G8: у преемника clientOrderId наследуется, а в results[] его нет
    k = r.new_order("G8 create @1350")
    h, p = rb(r, "G8 replace без newClientOrderId", [{"orderId": k, "newQtyScaled": 4500}])
    st(r, "G8 get old", k)
    st(r, "G8 get succ", succ(p, k))


def block_h(r):
    """H. Чужой счёт: ключ субаккаунта против ордера и триггера основного счёта."""
    from rb_common import Rec as _Rec
    sub = _Rec(raw_path=r.raw_path, sub=True)
    o = r.new_order("H0 root create @1350")
    trig = {"symbol": "AVAX-USDT", "qty": "0.25", "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER",
            "clientTriggerId": longid("h-", 20),
            "stopLoss": {"side": "SELL", "triggerPrice": "9.000", "child": {"limitGtc": {"price": "22.800", "postOnly": True}}}}
    http, p = r.call("H0 root create trigger AVAX SELL stopLoss 9.000", "POST", "/v1/triggers", body={"trigger": trig})
    tid = p.get("triggerId") if isinstance(p, dict) else None
    if tid:
        r.own_triggers.append(tid)
    st(r, "H0 root get order до", o)
    try:
        sub.call("H1 SUB balances", "GET", "/v1/balances")
        sub.call("H2 SUB GET ордер основного счёта", "GET", f"/v1/spot/orders/{o}")
        sub.call("H3 SUB replace-batch ордера основного счёта", "POST", P,
                 body={"symbol": SYMBOL, "requestId": r.rid(), "items": [{"orderId": o, "newQtyScaled": 4500}]})
        sub.call("H4 SUB modify ордера основного счёта", "POST", "/v1/orders/modify",
                 body={"symbol": SYMBOL, "requestId": r.rid(), "orderId": o, "newQtyScaled": 4500, "behavior": "AMEND_OR_REPLACE"})
        sub.call("H5 SUB cancel ордера основного счёта", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": o})
        sub.call("H6 SUB replace-batch с subaccountId основного счёта", "POST", P,
                 body={"symbol": SYMBOL, "requestId": r.rid(), "subaccountId": "RCx3H2SjGz6",
                       "items": [{"orderId": o, "newQtyScaled": 4500}]})
        if tid:
            sub.call("H7 SUB GET триггер основного счёта", "GET", f"/v1/triggers/{tid}")
            sub.call("H8 SUB DELETE триггер основного счёта", "DELETE", f"/v1/triggers/{tid}")
        sub.call("H9 SUB список триггеров", "GET", "/v1/triggers", query={"limit": 50})
        sub.call("H10 SUB create ордер без денег (0.005 ETH @1350)", "POST", "/v1/orders", body=order_body())
    finally:
        time.sleep(1)
        st(r, "H11 root get order после", o)
        if tid:
            r.call("H12 root GET trigger после", "GET", f"/v1/triggers/{tid}")
        # если SUB вдруг что-то создал — снять
        for oid in sub.own_ids:
            sub.call(f"H SUB cleanup cancel {oid}", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": oid})


def block_j(r):
    """J. Чужой счёт, обратное направление: основной ключ против ордера субаккаунта."""
    from rb_common import Rec as _Rec
    sub = _Rec(raw_path=r.raw_path, sub=True)
    so = None
    try:
        http, p = sub.call("J0 SUB create ETH BUY 0.005 @1350", "POST", "/v1/orders", body=order_body())
        so = p.get("orderId") if isinstance(p, dict) else None
        if not so:
            return
        sub.own_ids.append(so)
        time.sleep(0.6)
        sub.call("J0 SUB GET свой ордер до", "GET", f"/v1/spot/orders/{so}")
        r.call("J1 ROOT GET ордер субаккаунта", "GET", f"/v1/spot/orders/{so}")
        r.call("J2 ROOT replace-batch ордера субаккаунта", "POST", P,
               body={"symbol": SYMBOL, "requestId": r.rid(), "items": [{"orderId": so, "newQtyScaled": 4500}]})
        r.call("J3 ROOT modify ордера субаккаунта", "POST", "/v1/orders/modify",
               body={"symbol": SYMBOL, "requestId": r.rid(), "orderId": so, "newQtyScaled": 4500, "behavior": "AMEND_OR_REPLACE"})
        r.call("J4 ROOT cancel ордера субаккаунта", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": so})
        r.call("J5 ROOT replace-batch с subaccountId субаккаунта", "POST", P,
               body={"symbol": SYMBOL, "requestId": r.rid(), "subaccountId": "387wT9mN3df",
                     "items": [{"orderId": so, "newQtyScaled": 4500}]})
        time.sleep(1)
        sub.call("J6 SUB GET свой ордер после", "GET", f"/v1/spot/orders/{so}")
        http, p = sub.call("J7 SUB replace-batch своего ордера qty 5000->4500 (контроль)", "POST", P,
                           body={"symbol": SYMBOL, "requestId": r.rid(), "items": [{"orderId": so, "newQtyScaled": 4500}]})
        for it in (p or {}).get("results", []) if isinstance(p, dict) else []:
            v = it.get("replacementOrderId")
            if v and v != "1":
                sub.own_ids.append(v)
    finally:
        time.sleep(1)
        for oid in dict.fromkeys(sub.own_ids):
            http, p = sub.call(f"J SUB cleanup GET {oid}", "GET", f"/v1/spot/orders/{oid}")
            stt = ((p or {}).get("order") or {}).get("status") if isinstance(p, dict) else None
            if stt and stt not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
                sub.call(f"J SUB cleanup cancel {oid}", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": oid})


def main():
    blocks = [a.upper() for a in sys.argv[1:]] or ["A", "B", "C", "D", "E", "F", "G", "H"]
    r = Rec()
    try:
        for b in blocks:
            print(f"\n===== блок {b} =====")
            {"A": block_a, "B": block_b, "C": block_c, "D": block_d, "E": block_e, "F": block_f, "G": block_g, "H": block_h, "J": block_j}[b](r)
    finally:
        r.cleanup()


if __name__ == "__main__":
    main()
