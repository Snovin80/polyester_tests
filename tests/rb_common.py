"""Общая обвязка для тестов replace-batch: пишет каждый вызов дословно
(время UTC, HTTP-код, тело запроса, тело ответа) в docs/raw_*.jsonl.
Уборка — только свои ордера по id (без cancel-all)."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api_client import PolyesterClient, PolyesterApiError, PolyesterCredentials  # noqa: E402

SYMBOL = "ETH-USDT"
RAW_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "raw_replace_batch_2026-09-29.jsonl")


class Rec:
    def __init__(self, raw_path=RAW_PATH, sub=False):
        if sub:  # ключ субаккаунта из .env (POLYESTER_SUB_API_*)
            self.c = PolyesterClient(PolyesterCredentials(
                key_id=os.environ["POLYESTER_SUB_API_KEY_ID"],
                private_key=os.environ["POLYESTER_SUB_API_PRIVATE_KEY"],
                base_url=os.environ.get("POLYESTER_BASE_URL", "https://api.testnet.polyester.com")))
        else:
            self.c = PolyesterClient()
        self.raw_path = raw_path
        self.own_ids = []           # id ордеров, созданных тестом (в т.ч. replacementOrderId)
        self.own_sym = {}           # id -> symbol, если не SYMBOL
        self.own_triggers = []      # id триггеров (например, привязанных ног), снимать DELETE
        self._last = {}
        orig = self.c._session.request

        def wrap(*a, **kw):
            r = orig(*a, **kw)
            self._last = {"status": r.status_code, "text": r.text}
            return r

        self.c._session.request = wrap

    def call(self, label, method, path, body=None, query=None):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self._last = {}
        try:
            self.c._request(method, path, query=query, body=body, max_retries=0)
        except PolyesterApiError as e:
            pass
        except Exception as e:  # сеть и т.п.
            self._last = {"status": None, "text": f"EXC {type(e).__name__}: {e}"}
        text = self._last.get("text", "")
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        entry = {"label": label, "utc": ts, "method": method, "path": path,
                 "request": body if body is not None else query,
                 "http": self._last.get("status"), "response_raw": text}
        with open(self.raw_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[{ts}] {label}: HTTP {entry['http']}\n  {text[:700]}")
        return entry["http"], parsed

    def new_order(self, label, price="1350.00", qty="0.005"):
        """Лимитка BUY далеко от рынка. Возвращает orderId."""
        order = {"symbol": SYMBOL, "side": "BUY", "baseQty": qty,
                 "clientOrderId": PolyesterClient.new_client_order_id("rbt"),
                 "selfTradePreventionMode": "EXPIRE_MAKER", "feeAsset": "QUOTE",
                 "limitGtc": {"price": price, "postOnly": True}}
        http, p = self.call(label, "POST", "/v1/orders", body={"order": order})
        oid = None
        if isinstance(p, dict):
            oid = (p.get("order") or {}).get("orderId") or p.get("orderId")
        if oid:
            self.own_ids.append(str(oid))
        return oid

    def rid(self):
        return str(uuid.uuid4())

    def get_order(self, label, oid):
        return self.call(label, "GET", f"/v1/spot/orders/{oid}")

    def cleanup(self):
        """Снимает только свои ордера по id, если ещё открыты."""
        print("--- уборка ---")
        for oid in dict.fromkeys(self.own_ids):
            http, p = self.call(f"cleanup get {oid}", "GET", f"/v1/spot/orders/{oid}")
            st = ((p or {}).get("order") or {}).get("status") if isinstance(p, dict) else None
            if st and st not in ("CANCELED", "FILLED", "REJECTED", "EXPIRED"):
                self.call(f"cleanup cancel {oid}", "POST", "/v1/orders/cancel",
                          body={"symbol": self.own_sym.get(oid, SYMBOL), "orderId": oid})
        for tid in dict.fromkeys(self.own_triggers):
            http, p = self.call(f"cleanup GET trigger {tid}", "GET", f"/v1/triggers/{tid}")
            st = ((p or {}).get("trigger") or {}).get("status") if isinstance(p, dict) else None
            if st and st not in ("CANCELED", "COMPLETED", "FAILED"):
                self.call(f"cleanup DELETE trigger {tid}", "DELETE", f"/v1/triggers/{tid}")
