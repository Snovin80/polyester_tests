"""bugtest2: POST /v1/triggers — граничные значения, живой тестнет, AVAX-USDT.
Безопасность: дочерний ордер — лимитка post-only далеко от рынка (SELL ×2, BUY ×0.5),
трейлинг — всегда с activationPrice далеко выше рынка (не активируется). Каждый созданный
триггер сразу читается (GET) и снимается DELETE по своему id; в finally — повторная уборка.
Сырые ответы — docs/raw_triggers_2026-09-29.jsonl.
Запуск: python3 tests/bugtest2_triggers.py [блок ...]"""
import json
import sys
import time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from rb_common import Rec  # noqa: E402

SYMBOL = "AVAX-USDT"
RAW = "docs/raw_triggers_2026-09-29.jsonl"
QTY = "0.513791"          # уникальный «маркер» количества
SELL_FAR = "22.800"       # ≈ 2× рынка — не исполнится
BUY_FAR = "5.700"         # ≈ 0.5× рынка — не исполнится


QTY_B = "1.013791"        # маркер для BUY (5.7 × 1.013791 ≥ min notional 5)


class TRec(Rec):
    def __init__(self):
        super().__init__(raw_path=RAW)
        self.trigger_ids = []
        self.deleted = set()

    def delete(self, label, tid):
        http, p = self.call(label, "DELETE", f"/v1/triggers/{tid}")
        if http == 200 and isinstance(p, dict) and p.get("status") == "CANCELED":
            self.deleted.add(tid)
        return http, p

    def create(self, label, trig, top=None, keep=False, auto_cid=True, settle=0.5, raw_body=None):
        """POST /v1/triggers; если создан — пауза, GET и DELETE (если не keep)."""
        trig = dict(trig) if isinstance(trig, dict) else trig
        if auto_cid and isinstance(trig, dict) and "clientTriggerId" not in trig:
            trig["clientTriggerId"] = "tt-" + self.rid()[:12]
        body = raw_body if raw_body is not None else {"trigger": trig}
        if top and raw_body is None:
            body.update(top)
        http, p = self.call(label, "POST", "/v1/triggers", body=body)
        tid = p.get("triggerId") if isinstance(p, dict) else None
        if tid:
            self.trigger_ids.append(tid)
            time.sleep(settle)
            self.call(label + " | GET", "GET", f"/v1/triggers/{tid}")
            if not keep:
                self.delete(label + " | DELETE", tid)
        return http, p

    def cleanup_triggers(self):
        print("--- уборка триггеров ---")
        for tid in dict.fromkeys(self.trigger_ids):
            if tid not in self.deleted:
                self.delete(f"cleanup DELETE {tid}", tid)
        time.sleep(2)
        left = []
        for tid in dict.fromkeys(self.trigger_ids):
            http, p = self.call(f"verify GET {tid}", "GET", f"/v1/triggers/{tid}")
            st = ((p or {}).get("trigger") or {}).get("status") if isinstance(p, dict) else None
            if st != "CANCELED":
                left.append((tid, st))
        print("триггеры не в CANCELED:", left)

    def sweep_orders(self):
        """Если какой-то триггер сработал: по /events берём childOrderId и снимаем СВОЙ ордер по id."""
        print("--- дочерние ордера сработавших триггеров (по /events) ---")
        kids = []
        for tid in dict.fromkeys(self.trigger_ids):
            http, p = self.call(f"sweep events {tid}", "GET", f"/v1/triggers/{tid}/events")
            for ev in (p or {}).get("events", []) if isinstance(p, dict) else []:
                if ev.get("childOrderId") and ev["childOrderId"] != "1":
                    kids.append(ev["childOrderId"])
        print("дочерних ордеров:", kids)
        for oid in dict.fromkeys(kids):
            http, p = self.call(f"sweep GET {oid}", "GET", f"/v1/spot/orders/{oid}")
            st = ((p or {}).get("order") or {}).get("status") if isinstance(p, dict) else None
            if st and st not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
                self.call(f"sweep cancel {oid}", "POST", "/v1/orders/cancel",
                          body={"symbol": SYMBOL, "orderId": oid})


def rand_cid(n, prefix="t"):
    import random
    import string
    return (prefix + "".join(random.choices(string.ascii_letters + string.digits, k=n)))[:n]


def market(r):
    http, p = r.call("market: orderbook AVAX-USDT", "GET", "/v1/orderbook/AVAX-USDT", query={"depth": 5})
    bid = float(p["bids"][0][0]); ask = float(p["asks"][0][0])
    return bid, ask, (bid + ask) / 2


def fmt(x):
    return f"{x:.3f}"


def child(side):
    return {"limitGtc": {"price": SELL_FAR if side == "SELL" else BUY_FAR, "postOnly": True}}


def qty_for(side):
    return QTY if side == "SELL" else QTY_B


def trig(kind, side, price, **over):
    """kind: stopLoss | takeProfit. child — далёкая лимитка post-only."""
    t = {"symbol": SYMBOL, "qty": qty_for(side), "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER",
         kind: {"side": side, "triggerPrice": price, "child": child(side)}}
    t.update(over)
    return t


def block_base(r):
    r.create("T0 базовый SELL stop-loss @10.000 (рынок ≈11.40), child limitGtc postOnly 22.800",
             trig("stopLoss", "SELL", "10.000"))


def block_p(r):
    """P. Цена триггера: 0, отрицательные, лишние знаки, формат."""
    cases = [
        ("P1 цена 0", "0"), ("P2 цена -1", "-1"), ("P3 цена -0.001", "-0.001"),
        ("P4 пустая строка", ""), ("P5 'abc'", "abc"),
        ("P6 10.0005 (лишний знак сверх шага 0.001)", "10.0005"),
        ("P7 10.0010 (лишний нуль, значение на шаге)", "10.0010"),
        ("P8 10.000000001 (9 знаков)", "10.000000001"),
        ("P9 10.0000000001 (10 знаков)", "10.0000000001"),
        ("P10 '1e1'", "1e1"), ("P11 ' 10' с пробелом", " 10"), ("P12 '.5'", ".5"), ("P13 '10.'", "10."),
        ("P14 '+10'", "+10"), ("P15 минимальная 0.000000001 (1e-9)", "0.000000001"),
        ("P16 огромная 99999999999999999999", "99999999999999999999"),
        ("P17 число JSON 10 вместо строки", 10),
        ("P18 число JSON 10.5 вместо строки", 10.5),
    ]
    for label, price in cases:
        r.create(label, trig("stopLoss", "SELL", price))
    # неверный шаг цены у дочерней лимитки и нулевые/отрицательные цены child
    for label, cp in [("P19 child price 22.8005 (лишний знак)", "22.8005"), ("P20 child price 0", "0"),
                      ("P21 child price -1", "-1")]:
        t = trig("stopLoss", "SELL", "10.000")
        t["stopLoss"]["child"] = {"limitGtc": {"price": cp, "postOnly": True}}
        r.create(label, t)


def block_s(r):
    """S. Цена относительно рынка (все дочерние — далёкие лимитки; проверяем, не сработал ли триггер)."""
    bid, ask, mid = market(r)
    lo, hi = fmt(mid * 0.8), fmt(mid * 1.2)
    print(f"    >> рынок bid={bid} ask={ask}; ниже={lo} выше={hi}")
    cases = [
        ("S1 SELL stopLoss ниже рынка (штатно)", "stopLoss", "SELL", lo),
        ("S2 SELL stopLoss ВЫШЕ рынка", "stopLoss", "SELL", hi),
        ("S3 SELL takeProfit выше рынка (штатно)", "takeProfit", "SELL", hi),
        ("S4 SELL takeProfit НИЖЕ рынка", "takeProfit", "SELL", lo),
        ("S5 BUY stopLoss выше рынка (штатно)", "stopLoss", "BUY", hi),
        ("S6 BUY stopLoss НИЖЕ рынка", "stopLoss", "BUY", lo),
        ("S7 BUY takeProfit ниже рынка (штатно)", "takeProfit", "BUY", lo),
        ("S8 BUY takeProfit ВЫШЕ рынка", "takeProfit", "BUY", hi),
        ("S9 SELL stopLoss ровно по лучшему bid", "stopLoss", "SELL", fmt(bid)),
        ("S10 BUY stopLoss ровно по лучшему ask", "stopLoss", "BUY", fmt(ask)),
    ]
    for label, kind, side, price in cases:
        h, p = r.create(label + f" @{price}", trig(kind, side, price), keep=True, settle=1.5)
        tid = p.get("triggerId") if isinstance(p, dict) else None
        if tid:
            r.delete(label + " | DELETE", tid)


def block_w(r):
    """W. «Неправильная сторона»: срабатывает ли триггер, если цена уже за порогом (опрос 30 с)."""
    bid, ask, mid = market(r)
    lo, hi = fmt(mid * 0.8), fmt(mid * 1.2)
    ids = {}
    for label, kind, side, price in [("W1 SELL stopLoss ВЫШЕ рынка", "stopLoss", "SELL", hi),
                                     ("W2 BUY stopLoss НИЖЕ рынка", "stopLoss", "BUY", lo)]:
        h, p = r.create(f"{label} @{price} (держим 30 с)", trig(kind, side, price), keep=True)
        if isinstance(p, dict) and p.get("triggerId"):
            ids[label] = p["triggerId"]
    for i in range(10):
        time.sleep(3)
        for label, tid in ids.items():
            http, p = r.call(f"{label} poll {(i + 1) * 3}s", "GET", f"/v1/triggers/{tid}")
            t = (p or {}).get("trigger", {}) if isinstance(p, dict) else {}
            print(f"    >> {label} t+{(i + 1) * 3}s: {t.get('status')} fired={t.get('firedAt')} child={t.get('childOrderId')}")
    for label, tid in ids.items():
        r.delete(f"{label} | DELETE", tid)


def block_r(r):
    """R. Длина clientTriggerId (503) и повтор clientTriggerId (502). Все id случайные."""
    t0 = trig("stopLoss", "SELL", "10.000")
    res = {}
    for n in (64, 65, 100, 200, 64):
        h, p = r.create(f"R1 clientTriggerId {n} символов" + (" (повтор контроля)" if n == 64 and 64 in res else ""),
                        {**t0, "clientTriggerId": rand_cid(n)}, auto_cid=False)
        res.setdefault(n, h)
        time.sleep(2)
    if res.get(65) == 200:
        for n in (80, 90):
            r.create(f"R1 clientTriggerId {n} символов", {**t0, "clientTriggerId": rand_cid(n)}, auto_cid=False)
            time.sleep(2)
    cid = rand_cid(20, "tt-dup-")
    h, p = r.create("R3.0 clientTriggerId повтор — первый", {**t0, "clientTriggerId": cid}, keep=True)
    first = p.get("triggerId") if isinstance(p, dict) else None
    time.sleep(1)
    r.create("R3.1 тот же clientTriggerId, тот же body (пока ARMED)", {**t0, "clientTriggerId": cid}, auto_cid=False, keep=True)
    r.create("R3.2 тот же clientTriggerId, другой qty", {**t0, "clientTriggerId": cid, "qty": "0.613791"}, auto_cid=False)
    time.sleep(2)
    r.create("R3.3 тот же clientTriggerId, другая цена", {**t0, "clientTriggerId": cid,
             "stopLoss": {**t0["stopLoss"], "triggerPrice": "9.500"}}, auto_cid=False)
    time.sleep(1)
    if first:
        r.call("R3.4 GET первого после попыток", "GET", f"/v1/triggers/{first}")
        r.delete("R3.5 DELETE первого", first)
        time.sleep(1.5)
        h, p = r.create("R3.6 тот же clientTriggerId и body после DELETE", {**t0, "clientTriggerId": cid}, auto_cid=False, keep=True)
    for tid in list(r.trigger_ids):
        if tid not in r.deleted:
            r.delete("R cleanup DELETE", tid)


def block_m(r):
    """M. Триггер с qty ниже минимума принят при создании; что при срабатывании (условие уже выполнено)."""
    bid, ask, mid = market(r)
    hi = fmt(mid * 1.2)
    ids = {}
    for label, q in [("M0 контроль qty 0.513791 (норма)", "0.513791"), ("M1 qty 0.05 (< minQty 0.1)", "0.05"), ("M2 qty 0.3 (notional < 5)", "0.3")]:
        t = trig("stopLoss", "SELL", hi)
        t["qty"] = q
        h, p = r.create(f"{label} SELL stopLoss ВЫШЕ рынка @{hi}", t, keep=True)
        if isinstance(p, dict) and p.get("triggerId"):
            ids[label] = p["triggerId"]
    for i in range(12):
        time.sleep(5)
        for label, tid in ids.items():
            http, p = r.call(f"{label} poll {(i + 1) * 5}s", "GET", f"/v1/triggers/{tid}")
            t = (p or {}).get("trigger", {}) if isinstance(p, dict) else {}
            print(f"    >> {label} t+{(i + 1) * 5}s: {t.get('status')}")
    for label, tid in ids.items():
        r.call(f"{label} events", "GET", f"/v1/triggers/{tid}/events")
        r.delete(f"{label} | DELETE", tid)


def block_x(r):
    """X. Коды ошибок из справочника: чего ждём и что приходит."""
    t0 = trig("stopLoss", "SELL", "10.000")
    r.create("X1 BUY stopLoss с marketIoc child",
             {**trig("stopLoss", "BUY", "13.000"), "stopLoss": {"side": "BUY", "triggerPrice": "13.000", "child": {"marketIoc": {}}}})
    r.create("X2 SELL qty 100 AVAX (больше баланса)", {**t0, "qty": "100"})
    r.create("X3 stopLoss.triggerPriceSource=MARK_PRICE (лишнее поле)",
             {**t0, "stopLoss": {**t0["stopLoss"], "triggerPriceSource": "MARK_PRICE"}})
    r.create("X4 stopLoss.triggerDirection=ABOVE (лишнее поле)",
             {**t0, "stopLoss": {**t0["stopLoss"], "triggerDirection": "ABOVE"}})
    r.create("X5 SELL с feeAsset=BASE", {**t0, "feeAsset": "BASE"})
    r.create("X6 BUY с feeAsset=BASE", {**trig("stopLoss", "BUY", "13.000"), "feeAsset": "BASE"})
    r.create("X7 stopLoss child limitIoc", {**t0, "stopLoss": {"side": "SELL", "triggerPrice": "10.000", "child": {"limitIoc": {"price": SELL_FAR}}}})
    r.create("X8 stopLoss child limitFok", {**t0, "stopLoss": {"side": "SELL", "triggerPrice": "10.000", "child": {"limitFok": {"price": SELL_FAR}}}})
    r.create("X9 stopLoss child marketIoc (SELL, без монет не хватит? qty норм)", {**t0, "stopLoss": {"side": "SELL", "triggerPrice": "10.000", "child": {"marketIoc": {}}}})
    eth = {"symbol": "ETH-USDT", "qty": "0.005", "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER",
           "stopLoss": {"side": "BUY", "triggerPrice": "3500.005", "child": {"limitGtc": {"price": "1350.00", "postOnly": True}}}}
    r.create("X14 ETH-USDT BUY stopLoss 3500.005 (шаг 0.01) — сравнение с привязанным TP/SL", eth)
    r.create("X14b ETH-USDT BUY stopLoss 3500.00 (контроль)", {**eth, "stopLoss": {**eth["stopLoss"], "triggerPrice": "3500.00"}})
    http, p = r.call("X15 ордер AVAX BUY qty 0.05 @5.700 (обратная проверка minQty)", "POST", "/v1/orders",
                     body={"order": {"symbol": SYMBOL, "side": "BUY", "baseQty": "0.05", "clientOrderId": rand_cid(20, "tto-"),
                                     "selfTradePreventionMode": "EXPIRE_MAKER", "feeAsset": "QUOTE",
                                     "limitGtc": {"price": BUY_FAR, "postOnly": True}}})
    if isinstance(p, dict) and p.get("orderId"):
        r.call("X15 cancel", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": p["orderId"]})
    r.call("X10 GET несуществующий триггер", "GET", "/v1/triggers/2222222222")
    r.call("X11 DELETE несуществующий триггер", "DELETE", "/v1/triggers/2222222222")
    r.call("X12 GET триггер с неверным id", "GET", "/v1/triggers/0")
    h, p = r.create("X13 триггер для двойного DELETE", t0, keep=True)
    tid = p.get("triggerId") if isinstance(p, dict) else None
    if tid:
        r.delete("X13 DELETE #1", tid)
        r.call("X13 DELETE #2 (уже CANCELED)", "DELETE", f"/v1/triggers/{tid}")
        time.sleep(1.5)
        r.call("X13 GET после DELETE", "GET", f"/v1/triggers/{tid}")


def block_y(r):
    """Y. Чтение сразу после записи: GET после создания и GET после DELETE."""
    t0 = trig("stopLoss", "SELL", "10.000")
    for i in range(10):
        h, p = r.create(f"Y{i + 1} create (без паузы)", t0, keep=True, settle=0)   # create() делает GET сразу
        tid = p.get("triggerId") if isinstance(p, dict) else None
        if not tid:
            continue
        for d in (0.3, 1.0):
            time.sleep(d)
            r.call(f"Y{i + 1} GET +{d}s после create", "GET", f"/v1/triggers/{tid}")
        r.delete(f"Y{i + 1} DELETE", tid)
        r.call(f"Y{i + 1} GET сразу после DELETE", "GET", f"/v1/triggers/{tid}")
        time.sleep(1.0)
        r.call(f"Y{i + 1} GET +1s после DELETE", "GET", f"/v1/triggers/{tid}")


def last_trade_ts(r, label):
    http, p = r.call(label, "GET", f"/v1/spot/markets/{SYMBOL}/trades", query={"limit": 5})
    tr = (p or {}).get("trades", []) if isinstance(p, dict) else []
    return max((t["executedAt"] for t in tr), default=""), tr


def block_fire(r, max_wait=None):
    """FIRE. Срабатывание по факту сделки: «неверная сторона», qty ниже минимума, контроль верной стороны."""
    import os
    max_wait = max_wait or int(os.environ.get("FIRE_WAIT", "900"))
    bid, ask, mid = market(r)
    lo, hi = fmt(mid * 0.8), fmt(mid * 1.2)
    r.call("FIRE балансы до", "GET", "/v1/balances")
    plan = [
        ("FW1 SELL stopLoss ВЫШЕ рынка (неверная сторона) qty 0.25", "stopLoss", "SELL", hi, "0.25"),
        ("FW2 BUY stopLoss НИЖЕ рынка (неверная сторона)", "stopLoss", "BUY", lo, QTY_B),
        ("FM1 SELL stopLoss ВЫШЕ рынка, qty 0.05 (< minQty 0.1)", "stopLoss", "SELL", hi, "0.05"),
        ("FC2 контроль: BUY stopLoss ВЫШЕ рынка (верная сторона)", "stopLoss", "BUY", hi, QTY_B),
    ]
    ids = {}
    for label, kind, side, price, q in plan:
        t = trig(kind, side, price)
        t["qty"] = q
        h, p = r.create(f"{label} @{price}", t, keep=True)
        if isinstance(p, dict) and p.get("triggerId"):
            ids[label] = p["triggerId"]
    r.call("FIRE балансы после создания (резерв?)", "GET", "/v1/balances")
    t0, _ = last_trade_ts(r, "FIRE последняя сделка до ожидания")
    print(f"    >> последняя сделка до ожидания: {t0}; жду новую сделку до {max_wait} с")
    start = time.time()
    t1 = t0
    while time.time() - start < max_wait:
        time.sleep(5)
        r._last = {}
        try:
            r.c._request("GET", f"/v1/spot/markets/{SYMBOL}/trades", query={"limit": 5}, max_retries=0)
            tr = json.loads(r._last.get("text", "{}")).get("trades", [])
            t1 = max((t["executedAt"] for t in tr), default=t0)
        except Exception:
            pass
        if t1 > t0:
            break
    print(f"    >> новая сделка: {t1} (ждал {int(time.time() - start)} с)")
    last_trade_ts(r, "FIRE сделки после ожидания")
    time.sleep(3)
    for label, tid in ids.items():
        r.call(f"{label} | GET после сделки", "GET", f"/v1/triggers/{tid}")
        r.call(f"{label} | events", "GET", f"/v1/triggers/{tid}/events")
    for label, tid in ids.items():
        r.delete(f"{label} | DELETE", tid)


def block_rsv(r):
    """RSV. Резервирует ли SELL-триггер монеты (справочник DELETE: «release its reserved quantity»)."""
    r.call("RSV балансы до", "GET", "/v1/balances")
    h, p = r.create("RSV SELL stopLoss @9.000 qty 0.25 (верная сторона)", {**trig("stopLoss", "SELL", "9.000"), "qty": "0.25"}, keep=True)
    r.call("RSV балансы с триггером", "GET", "/v1/balances")
    tid = p.get("triggerId") if isinstance(p, dict) else None
    if tid:
        r.delete("RSV DELETE", tid)
    time.sleep(1)
    r.call("RSV балансы после DELETE", "GET", "/v1/balances")


def ladder(**lad):
    lad = {"side": "BUY", "levels": 2, "priceMin": "5.000", "priceMax": "5.500", "postOnly": True, **lad}
    lad = {k: v for k, v in lad.items() if v is not None}
    return {"symbol": SYMBOL, "qty": "2.1", "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER", "ladder": lad}


def twap(**tw):
    tw = {"side": "BUY", "durationMs": 60000, "sliceIntervalMs": 30000, "limitGtc": {"price": "5.500"}, **tw}
    tw = {k: v for k, v in tw.items() if v is not None}
    return {"symbol": SYMBOL, "qty": "2.1", "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER", "twap": tw}


def kids_of(r, label, tid):
    http, p = r.call(label, "GET", f"/v1/triggers/{tid}/events")
    return [ev["childOrderId"] for ev in ((p or {}).get("events", []) if isinstance(p, dict) else [])
            if ev.get("childOrderId") and ev["childOrderId"] != "1"]


def valid_strategy(r, label, t, watch_after=0):
    """Создать ladder/twap, посмотреть дочерние, DELETE, проверить, что дочерние сняты; при нужде — отменить по id."""
    h, p = r.create(label, t, keep=True, settle=2.0)
    tid = p.get("triggerId") if isinstance(p, dict) else None
    if not tid:
        return
    kids = kids_of(r, label + " | events до DELETE", tid)
    for k in kids:
        r.call(f"{label} | child {k} до DELETE", "GET", f"/v1/spot/orders/{k}")
    r.delete(label + " | DELETE", tid)
    time.sleep(2)
    r.call(label + " | GET после DELETE", "GET", f"/v1/triggers/{tid}")
    for k in kids:
        http, pp = r.call(f"{label} | child {k} после DELETE", "GET", f"/v1/spot/orders/{k}")
        stt = ((pp or {}).get("order") or {}).get("status") if isinstance(pp, dict) else None
        if stt and stt not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
            r.call(f"{label} | child {k} cancel вручную", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": k})
    if watch_after:
        time.sleep(watch_after)
        late = kids_of(r, label + f" | events через {watch_after} с после DELETE", tid)
        for k in set(late) - set(kids):
            http, pp = r.call(f"{label} | НОВЫЙ child {k} после DELETE", "GET", f"/v1/spot/orders/{k}")
            stt = ((pp or {}).get("order") or {}).get("status") if isinstance(pp, dict) else None
            if stt and stt not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
                r.call(f"{label} | НОВЫЙ child {k} cancel", "POST", "/v1/orders/cancel", body={"symbol": SYMBOL, "orderId": k})


def block_lad(r):
    """LAD. Ladder BUY далеко ниже рынка: штатный и границы."""
    valid_strategy(r, "L1 ladder BUY 2 уровня 5.000–5.500 qty 2.1 (штатно)", ladder())
    for label, kw in [("L2 levels 0", dict(levels=0)), ("L3 levels -1", dict(levels=-1)), ("L4 levels 1", dict(levels=1)),
                      ("L5 priceMin > priceMax", dict(priceMin="5.500", priceMax="5.000")),
                      ("L6 priceMin = priceMax, levels 2", dict(priceMin="5.000", priceMax="5.000")),
                      ("L7 priceMin 0", dict(priceMin="0")), ("L8 priceMax неверный шаг 5.5005", dict(priceMax="5.5005")),
                      ("L9 без postOnly", dict(postOnly=None)), ("L10 без side", dict(side=None))]:
        r.create(label, ladder(**kw), settle=1.0)
    r.create("L11 qty на уровень ниже минимума (0.2 / 2 уровня)", {**ladder(), "qty": "0.2"}, settle=1.5)


def block_twap(r):
    """TWAP. BUY limitGtc далеко ниже рынка: штатный и границы (marketIoc не используем)."""
    valid_strategy(r, "TW1 twap BUY limitGtc 5.500, 60 с / 30 с, qty 2.1 (штатно)", twap(), watch_after=35)
    for label, kw in [("TW2 durationMs 0", dict(durationMs=0)), ("TW3 durationMs -1", dict(durationMs=-1)),
                      ("TW4 sliceIntervalMs 0", dict(sliceIntervalMs=0)), ("TW5 sliceIntervalMs -1", dict(sliceIntervalMs=-1)),
                      ("TW6 slice больше duration (120 с / 60 с)", dict(sliceIntervalMs=120000)),
                      ("TW7 без limitGtc и marketIoc", dict(limitGtc=None)),
                      ("TW8 limitGtc price 0", dict(limitGtc={"price": "0"})),
                      ("TW9 limitGtc price неверный шаг 5.5005", dict(limitGtc={"price": "5.5005"})),
                      ("TW10 без side", dict(side=None))]:
        r.create(label, twap(**kw), settle=1.0)
    r.create("TW11 qty на слайс ниже минимума (0.2 / 2 слайса)", {**twap(), "qty": "0.2"}, settle=1.5)


def block_filt(r):
    """FILT. Фильтры GET /v1/triggers (только чтение)."""
    parent = "FzKTbH9PGF5"   # ордер ETH-USDT из части 1 (G5b) с двумя привязанными ногами, отменён
    for label, q in [("FILT1 parentOrderId существующего родителя с 2 ногами", {"parentOrderId": parent, "limit": 200}),
                     ("FILT2 parentOrderId несуществующий", {"parentOrderId": "2222222222", "limit": 200}),
                     ("FILT3 symbol=ETH-USDT", {"symbol": "ETH-USDT", "limit": 200}),
                     ("FILT4 status=CANCELED", {"status": "CANCELED", "limit": 200})]:
        http, p = r.call(label, "GET", "/v1/triggers", query=q)
        ts = (p or {}).get("triggers", []) if isinstance(p, dict) else []
        print(f"    >> {label}: всего {len(ts)}, с parentOrderId={parent}: {sum(1 for t in ts if t.get('parentOrderId') == parent)}, "
              f"символы {sorted(set(t.get('symbol') for t in ts))}, статусы {sorted(set(t.get('status') for t in ts))}")


def block_f(r):
    """F. Пустые, лишние, неверные поля."""
    t0 = trig("stopLoss", "SELL", "10.000")
    def drop(k):
        t = dict(t0); t.pop(k); return t
    r.create("F1 пустое тело {}", None, raw_body={})
    r.create("F2 trigger = {}", {}, auto_cid=False)
    r.create("F3 trigger = {} с clientTriggerId", {}, auto_cid=True)
    r.create("F4 без symbol", drop("symbol"))
    r.create("F5 без qty", drop("qty"))
    r.create("F6 без clientTriggerId", t0, auto_cid=False)
    r.create("F7 без stopLoss/takeProfit/trailing/twap/ladder", {k: v for k, v in t0.items() if k != "stopLoss"})
    r.create("F8 stopLoss и takeProfit вместе", {**t0, "takeProfit": {"side": "SELL", "triggerPrice": "13.000", "child": child("SELL")}})
    r.create("F9 qty 0", {**t0, "qty": "0"})
    r.create("F10 qty -1", {**t0, "qty": "-1"})
    r.create("F11 qty 'abc'", {**t0, "qty": "abc"})
    r.create("F12 qty ниже minQty (0.05 AVAX)", {**t0, "qty": "0.05"})
    r.create("F13 qty ниже min notional (0.3 AVAX × 22.8 child)", {**t0, "qty": "0.3"})
    r.create("F14 qty 7 знаков 0.5137911 (шаг 0.000001)", {**t0, "qty": "0.5137911"})
    r.create("F15 qty число JSON 0.5", {**t0, "qty": 0.5})
    r.create("F16 лишнее поле в trigger (foo)", {**t0, "foo": 1})
    r.create("F17 лишнее поле на верхнем уровне (foo)", t0, top={"foo": 1})
    r.create("F18 лишнее поле в stopLoss (foo)", {**t0, "stopLoss": {**t0["stopLoss"], "foo": 1}})
    r.create("F19 symbol не существует", {**t0, "symbol": "XXX-USDT"})
    r.create("F20 symbol в нижнем регистре", {**t0, "symbol": "avax-usdt"})
    r.create("F21 feeAsset неверный", {**t0, "feeAsset": "BASE_X"})
    r.create("F22 selfTradePreventionMode неверный", {**t0, "selfTradePreventionMode": "NOPE"})
    r.create("F23 side неверный (HOLD)", {**t0, "stopLoss": {**t0["stopLoss"], "side": "HOLD"}})
    r.create("F24 без side", {**t0, "stopLoss": {"triggerPrice": "10.000", "child": child("SELL")}})
    r.create("F25 без child", {**t0, "stopLoss": {"side": "SELL", "triggerPrice": "10.000"}})
    r.create("F26 child пустой {}", {**t0, "stopLoss": {"side": "SELL", "triggerPrice": "10.000", "child": {}}})
    r.create("F27 child: limitGtc и marketIoc вместе",
             {**t0, "stopLoss": {"side": "SELL", "triggerPrice": "10.000",
                                 "child": {"limitGtc": {"price": SELL_FAR, "postOnly": True}, "marketIoc": {}}}})
    r.create("F28 subaccountId неверный", t0, top={"subaccountId": "zzzz"})
    # clientTriggerId: длина и повтор
    for label, cid in [("F29 clientTriggerId 36 символов", rand_cid(36)), ("F30 clientTriggerId 37 символов", rand_cid(37)),
                       ("F32 clientTriggerId со спецсимволами", "tt id/#?" + rand_cid(6))]:
        r.create(label, {**t0, "clientTriggerId": cid}, auto_cid=False)
    cid = "tt-dup-" + r.rid()[:8]
    r.create("F33a clientTriggerId повтор — первый", {**t0, "clientTriggerId": cid}, keep=True)
    r.create("F33b clientTriggerId повтор — второй (тот же body)", {**t0, "clientTriggerId": cid}, auto_cid=False)
    r.create("F33c clientTriggerId повтор — другой qty", {**t0, "clientTriggerId": cid, "qty": "0.613791"}, auto_cid=False)
    for tid in list(r.trigger_ids):
        if tid not in r.deleted:
            r.delete("F33 cleanup DELETE", tid)


def block_tr(r):
    """TR. Трейлинг: границы дистанции (activationPrice всегда далеко выше рынка — не активируется)."""
    bid, ask, mid = market(r)
    act = fmt(mid * 2)

    def tr(**ts):
        ts = {"side": "SELL", "activationPrice": act, **ts}
        return {"symbol": SYMBOL, "qty": QTY, "feeAsset": "QUOTE", "selfTradePreventionMode": "EXPIRE_MAKER",
                "trailingStop": ts}
    cases = [
        ("TR1 bps 500 (штатно)", dict(trailingDistanceBps=500)),
        ("TR2 bps 0", dict(trailingDistanceBps=0)),
        ("TR3 bps -1", dict(trailingDistanceBps=-1)),
        ("TR4 bps 1", dict(trailingDistanceBps=1)),
        ("TR5 bps 9999", dict(trailingDistanceBps=9999)),
        ("TR6 bps 10000", dict(trailingDistanceBps=10000)),
        ("TR7 bps 10001", dict(trailingDistanceBps=10001)),
        ("TR8 bps 1000000", dict(trailingDistanceBps=1000000)),
        ("TR9 bps строкой '500'", dict(trailingDistanceBps="500")),
        ("TR10 bps 1.5 (дробное)", dict(trailingDistanceBps=1.5)),
        ("TR11 ticks '100000000' (0.1 quote)", dict(trailingDistanceTicks="100000000")),
        ("TR12 ticks '0'", dict(trailingDistanceTicks="0")),
        ("TR13 ticks '-1'", dict(trailingDistanceTicks="-1")),
        ("TR14 ticks '1' (1e-9)", dict(trailingDistanceTicks="1")),
        ("TR15 ticks '0.1' десятичной строкой", dict(trailingDistanceTicks="0.1")),
        ("TR16 ticks больше цены '99999999999999'", dict(trailingDistanceTicks="99999999999999")),
        ("TR17 bps и ticks вместе", dict(trailingDistanceBps=500, trailingDistanceTicks="100000000")),
        ("TR18 без дистанции вообще", dict()),
        ("TR19 maxSlippageBps 0 (при bps 500)", dict(trailingDistanceBps=500, maxSlippageBps=0)),
        ("TR20 maxSlippageBps -1", dict(trailingDistanceBps=500, maxSlippageBps=-1)),
        ("TR21 maxSlippageBps 10000", dict(trailingDistanceBps=500, maxSlippageBps=10000)),
        ("TR22 maxSlippageBps 10001", dict(trailingDistanceBps=500, maxSlippageBps=10001)),
        ("TR23 maxSlippageTicks 0", dict(trailingDistanceBps=500, maxSlippageTicks=0)),
        ("TR24 maxSlippageTicks -1", dict(trailingDistanceBps=500, maxSlippageTicks=-1)),
        ("TR25 maxSlippageBps и Ticks вместе", dict(trailingDistanceBps=500, maxSlippageBps=100, maxSlippageTicks=1000000)),
        ("TR26 side BUY (трейлинг только SELL)", dict(trailingDistanceBps=500, side="BUY")),
        ("TR27 activationPrice 0", dict(trailingDistanceBps=500, activationPrice="0")),
        ("TR28 activationPrice -1", dict(trailingDistanceBps=500, activationPrice="-1")),
        ("TR29 activationPrice неверный шаг 22.8005", dict(trailingDistanceBps=500, activationPrice="22.8005")),
    ]
    cases += [
        ("TR32 ticks '9223372036' (≈ int64 / 1e9)", dict(trailingDistanceTicks="9223372036")),
        ("TR33 ticks '9223372037' (> int64 / 1e9)", dict(trailingDistanceTicks="9223372037")),
        ("TR34 ticks '0.000000001' (9 знаков)", dict(trailingDistanceTicks="0.000000001")),
        ("TR35 ticks '0.0000000001' (10 знаков)", dict(trailingDistanceTicks="0.0000000001")),
    ]
    for label, ts in cases:
        r.create(label, tr(**ts))
    # activationPrice ниже рынка + огромная дистанция (не сработает: нужно падение на 50 %+)
    r.create("TR30 activationPrice ниже рынка (9.000), bps 5000", tr(trailingDistanceBps=5000, activationPrice="9.000"))
    r.create("TR31 без activationPrice, bps 5000", {"symbol": SYMBOL, "qty": QTY, "feeAsset": "QUOTE",
             "selfTradePreventionMode": "EXPIRE_MAKER", "trailingStop": {"side": "SELL", "trailingDistanceBps": 5000}})


BLOCKS = {"BASE": block_base, "P": block_p, "S": block_s, "W": block_w, "R": block_r, "M": block_m, "X": block_x, "Y": block_y, "FIRE": block_fire, "RSV": block_rsv, "LAD": block_lad, "TWAP": block_twap, "FILT": block_filt, "F": block_f, "TR": block_tr}


def main():
    names = [a.upper() for a in sys.argv[1:]] or ["P", "S", "F", "TR", "X", "Y", "R", "RSV", "LAD", "TWAP", "FILT", "FIRE"]
    r = TRec()
    try:
        for n in names:
            print(f"\n===== {n} =====")
            BLOCKS[n](r)
    finally:
        r.cleanup_triggers()
        r.sweep_orders()


if __name__ == "__main__":
    main()
