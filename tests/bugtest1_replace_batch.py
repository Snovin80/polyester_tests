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


def main():
    blocks = [a.upper() for a in sys.argv[1:]] or ["A", "B", "C", "D", "E"]
    r = Rec()
    try:
        for b in blocks:
            print(f"\n===== блок {b} =====")
            {"A": block_a, "B": block_b, "C": block_c, "D": block_d, "E": block_e}[b](r)
    finally:
        r.cleanup()


if __name__ == "__main__":
    main()
