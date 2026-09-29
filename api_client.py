"""
БЛОК 1 — Polyester API Client

Переиспользуемая обёртка над подписанными Ed25519-запросами к Polyester REST API.
Заменяет одноразовый polyester_signed_request.py на класс с методами под
конкретные операции (market data, balances, orders).

Ключи НИКОГДА не хардкодятся тут — берутся из переменных окружения:
    POLYESTER_API_KEY_ID
    POLYESTER_API_PRIVATE_KEY   (hex или base64, 32-байтный Ed25519 seed)
    POLYESTER_BASE_URL          (опционально, по умолчанию devnet)

Требуется: pip install pynacl requests --break-system-packages
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qsl, quote

from nacl.signing import SigningKey
import requests
from dotenv import load_dotenv

load_dotenv()  # подтягивает POLYESTER_API_KEY_ID / POLYESTER_API_PRIVATE_KEY из .env автоматически

DEFAULT_BASE_URL = "https://api-devnet.polyester.ai"


# --------------------------------------------------------------------------
# Ошибки
# --------------------------------------------------------------------------

class PolyesterApiError(Exception):
    """Ошибка ответа API (4xx/5xx)."""

    def __init__(self, status_code: int, code: Optional[str], message: str, raw: Any = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.raw = raw
        super().__init__(f"[{status_code}] {code or ''}: {message}")


class PolyesterRateLimitError(PolyesterApiError):
    """429 — отдельный тип, чтобы вызывающий код мог явно ждать retry_after."""

    def __init__(self, status_code, code, message, raw, retry_after: Optional[float] = None):
        super().__init__(status_code, code, message, raw)
        self.retry_after = retry_after


def is_post_only_cross(err: PolyesterApiError) -> bool:
    """
    Биржа отклоняет post-only заявку, которая пересекла бы спред, кодом
    POST_ONLY_CROSS (в документации ERROR_CODE_POST_ONLY_CROSS; в терминальной
    причине ордера — "POST_ONLY_CROSS"). Проверяем и код, и текст: точный
    формат ответа create на живой бирже ещё не подтверждён.
    """
    haystack = f"{err.code or ''} {err.message or ''}".upper()
    return "POST_ONLY" in haystack and ("CROSS" in haystack or "REJECT" in haystack)


def is_insufficient_funds(err: PolyesterApiError) -> bool:
    """
    Не хватает средств под заявку. Для сетки это НЕ поломка: sell-уровни выше
    цены обеспечиваются базой, которая появится после исполнения buy-уровней.
    Такой уровень должен ждать в PENDING, а не уходить в FAILED (19.09: при
    ручном диапазоне, где цена стояла у нижней границы, так отвалились
    четыре sell-уровня подряд).
    """
    haystack = f"{err.code or ''} {err.message or ''}".upper()
    return "INSUFFICIENT" in haystack or "NOT_ENOUGH" in haystack


def retry_after_seconds(payload: Any, headers: Any) -> Optional[float]:
    """
    Сколько ждать после 429. Документация: в теле ответа
    rate_limit.retry_after_ms с миллисекундной точностью, в заголовках —
    Retry-After / RateLimit-Reset в секундах. Тело точнее, берём его первым.
    """
    if isinstance(payload, dict):
        rl = payload.get("rate_limit") or payload.get("rateLimit") or {}
        ms = rl.get("retry_after_ms") or rl.get("retryAfterMs")
        try:
            if ms is not None and float(ms) > 0:
                return float(ms) / 1000.0
        except (TypeError, ValueError):
            pass
    for name in ("Retry-After", "RateLimit-Reset"):
        value = headers.get(name) if headers is not None else None
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


# --------------------------------------------------------------------------
# Подпись запроса
# --------------------------------------------------------------------------

def _load_private_key(key_str: str) -> SigningKey:
    """Принимает hex (64 символа) или base64-строку, декодирует в 32-байтный seed."""
    key_str = key_str.strip()
    raw = None
    if len(key_str) == 64:
        try:
            raw = bytes.fromhex(key_str)
        except ValueError:
            raw = None
    if raw is None:
        try:
            raw = base64.b64decode(key_str)
        except Exception:
            pass
    if raw is None or len(raw) != 32:
        raise ValueError(
            f"Приватный ключ должен декодироваться в 32 байта (получено: {len(raw) if raw else 'None'})"
        )
    return SigningKey(raw)


def _canonical_query(raw_query: str) -> str:
    """Нормализация query-строки: decode -> сортировка по key,value -> re-encode RFC3986."""
    if not raw_query:
        return ""
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    pairs_sorted = sorted(pairs, key=lambda kv: (kv[0], kv[1]))

    def enc(s: str) -> str:
        return quote(s, safe="")

    return "&".join(f"{enc(k)}={enc(v)}" for k, v in pairs_sorted)


# --------------------------------------------------------------------------
# Клиент
# --------------------------------------------------------------------------

@dataclass
class PolyesterCredentials:
    key_id: str
    private_key: str  # hex или base64
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "PolyesterCredentials":
        key_id = os.environ.get("POLYESTER_API_KEY_ID")
        private_key = os.environ.get("POLYESTER_API_PRIVATE_KEY")
        base_url = os.environ.get("POLYESTER_BASE_URL", DEFAULT_BASE_URL)
        if not key_id or not private_key:
            raise RuntimeError(
                "Не заданы POLYESTER_API_KEY_ID / POLYESTER_API_PRIVATE_KEY в окружении. "
                "Не храните ключи в коде."
            )
        return cls(key_id=key_id, private_key=private_key, base_url=base_url)


class PolyesterClient:
    """
    Синхронный клиент. Каждый публичный метод — один вызов REST API,
    без побочной логики (стратегии/риск-менеджмент живут в engines/*, не тут).
    """

    def __init__(self, credentials: Optional[PolyesterCredentials] = None, timeout: float = 15.0):
        self.creds = credentials or PolyesterCredentials.from_env()
        self.signing_key = _load_private_key(self.creds.private_key)
        self.base_url = self.creds.base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        # Поправка часов: биржа принимает подпись только при |X-API-TIMESTAMP -
        # server time| <= 10 с. Если часы компьютера уплыли, все запросы
        # падают с TIMESTAMP_SKEW. Смещение вычисляется из заголовка Date
        # ответа и дальше прибавляется к каждой метке времени.
        self.time_offset_ms: int = 0

    # ---- низкоуровневые примитивы ----

    @staticmethod
    def _server_time_ms(headers: Any) -> Optional[int]:
        """Время сервера из заголовка Date (RFC 1123), в миллисекундах."""
        value = headers.get("Date") if headers is not None else None
        if not value:
            return None
        try:
            from email.utils import parsedate_to_datetime
            return int(parsedate_to_datetime(value).timestamp() * 1000)
        except Exception:
            return None

    def _sign(self, method: str, path: str, query: str, body_bytes: bytes) -> dict:
        timestamp_ms = str(int(time.time() * 1000) + self.time_offset_ms)
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        canonical = "\n".join([timestamp_ms, method.upper(), path, query, body_hash])
        signature = self.signing_key.sign(canonical.encode("utf-8")).signature.hex()
        headers = {
            "X-API-KEY-ID": self.creds.key_id,
            "X-API-TIMESTAMP": timestamp_ms,
            "X-API-SIGNATURE": signature,
            "Accept": "*/*",
        }
        if body_bytes:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        query: Optional[dict] = None,
        body: Optional[dict] = None,
        max_retries: int = 2,
        idempotent: bool = False,
    ) -> Any:
        raw_query = "&".join(f"{k}={v}" for k, v in (query or {}).items() if v is not None)
        canon_query = _canonical_query(raw_query)
        body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else b""

        last_exc = None
        for attempt in range(max_retries + 1):
            headers = self._sign(method, path, canon_query, body_bytes)
            url = f"{self.base_url}{path}"
            if canon_query:
                url += f"?{canon_query}"

            try:
                resp = self._session.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    data=body_bytes if body is not None else None,
                    timeout=self.timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                # Ответа не было вообще: оборванное или протухшее соединение в
                # пуле. Такое лечится новым соединением, поэтому закрываем
                # сессию перед повтором. Повторяем ТОЛЬКО чтение: у POST после
                # таймаута ордер может уже стоять на бирже, и слепой повтор
                # создал бы второй (см. открытые вопросы про 5xx-then-CONFLICT).
                # idempotent=True — для вызовов, повтор которых ничего не создаёт
                # и не удваивает: dead-man switch просто заново взводит таймер.
                self._session.close()
                last_exc = e
                if (method.upper() == "GET" or idempotent) and attempt < max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise

            try:
                payload = resp.json()
            except Exception:
                payload = {"raw": resp.text}

            if resp.status_code == 429:
                retry_after_f = retry_after_seconds(payload, resp.headers)
                err = PolyesterRateLimitError(
                    resp.status_code,
                    payload.get("code") if isinstance(payload, dict) else None,
                    payload.get("message", "rate limited") if isinstance(payload, dict) else "rate limited",
                    payload,
                    retry_after=retry_after_f,
                )
                if attempt < max_retries:
                    time.sleep(retry_after_f or (1.5 * (attempt + 1)))
                    last_exc = err
                    continue
                raise err

            if resp.status_code >= 500:
                last_exc = PolyesterApiError(
                    resp.status_code,
                    payload.get("code") if isinstance(payload, dict) else None,
                    payload.get("message", "server error") if isinstance(payload, dict) else "server error",
                    payload,
                )
                if attempt < max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise last_exc

            if resp.status_code >= 400:
                if isinstance(payload, dict):
                    msg = payload.get("message") or payload.get("detail") or payload.get("error") or str(payload)
                else:
                    msg = str(payload)
                code = payload.get("code") if isinstance(payload, dict) else None

                # Часы уплыли: один раз подстраиваемся под время сервера и
                # повторяем тот же запрос (тело и идемпотентный ключ те же).
                if code == "TIMESTAMP_SKEW" and attempt < max_retries:
                    server_ms = self._server_time_ms(resp.headers)
                    if server_ms is not None:
                        self.time_offset_ms = server_ms - int(time.time() * 1000)
                        continue
                    msg = f"{msg} (часы компьютера расходятся с биржей больше чем на 10 с — синхронизируйте время)"

                raise PolyesterApiError(resp.status_code, code, msg, payload)

            return payload

        raise last_exc  # недостижимо при max_retries >= 0, но для типизации

    @staticmethod
    def new_client_order_id(prefix: str = "bot") -> str:
        """Идемпотентный client_order_id: буквы/цифры/./_/:/ -//-, 1-36 символов."""
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    # ---- market data (публичные, не требуют средств на счёте) ----

    def get_spot_config(self) -> Any:
        return self._request("GET", "/v1/spot/config")

    def get_fee_rates(self) -> Any:
        return self._request("GET", "/v1/spot/fee-rates")

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        include_incomplete: bool = False,
    ) -> Any:
        """
        timeframe: один из 1s, 1m, 5m, 15m, 30m, 1h, 4h, 1d, 12h, 1w, 1mo
        limit: максимум 10000 закрытых свечей (по умолчанию у API — 500)
        include_incomplete: включить текущую незакрытую свечу первой в списке
        """
        return self._request(
            "GET",
            f"/v1/spot/markets/{symbol}/candles",
            query={
                "timeframe": timeframe,
                "limit": limit,
                "startTime": start_time,
                "endTime": end_time,
                "includeIncomplete": str(include_incomplete).lower() if include_incomplete else None,
            },
        )

    def get_orderbook(self, symbol: str, depth: Optional[int] = None) -> Any:
        """
        ВАЖНО: без параметра depth API возвращает ПУСТЫЕ bids/asks (проверено на
        тестнете: bookSeq растёт, ts живой, а списки пустые). Поэтому глубина
        всегда передаётся явно — иначе бот решит, что торгов нет.
        """
        return self._request("GET", f"/v1/orderbook/{symbol}",
                             query={"depth": depth if depth is not None else 50})

    def get_public_trades(self, symbol: str, limit: int = 100) -> Any:
        return self._request("GET", f"/v1/spot/markets/{symbol}/trades", query={"limit": limit})

    # ---- account / balances ----

    def get_balances(self) -> Any:
        return self._request("GET", "/v1/balances")

    def get_rate_limits(self) -> Any:
        return self._request("GET", "/v1/trading/rate-limits")

    # ---- orders ----

    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: str,
        price: Optional[str] = None,
        tif: str = "gtc",
        post_only: bool = False,
        client_order_id: Optional[str] = None,
        max_slippage_bps: Optional[int] = None,
        attached_risk: Optional[dict] = None,
    ) -> Any:
        """
        order_type: 'limit' | 'market'
        side: 'BUY' | 'SELL'
        tif (только для limit): 'gtc' | 'ioc' | 'fok'
        attached_risk: сырой dict для поля order.attachedRisk (take_profit/stop_loss/oco),
                       см. build_take_profit_attached_risk() ниже для готового хелпера.
        """
        order: dict = {
            "symbol": symbol,
            "side": side,
            "baseQty": qty,
            "clientOrderId": client_order_id or self.new_client_order_id(),
            # Оба значения — дефолты биржи, но документация советует слать их
            # явно, чтобы в журнале и при сверке было видно, что именно применено.
            # EXPIRE_MAKER: при самосделке снимается наш же лежащий ордер, а
            # входящий продолжает исполняться — для аварийного flatten это и нужно.
            # QUOTE: комиссия BUY берётся в USDT, база приходит полностью.
            "selfTradePreventionMode": "EXPIRE_MAKER",
            "feeAsset": "QUOTE",
        }
        if order_type == "limit":
            child_key = {"gtc": "limitGtc", "ioc": "limitIoc", "fok": "limitFok"}.get(tif)
            if child_key is None:
                raise ValueError(f"Неизвестный tif для limit-ордера: {tif}")
            child_body: dict = {"price": price}
            if child_key == "limitGtc":
                child_body["postOnly"] = post_only
            order[child_key] = child_body
        elif order_type == "market":
            market_body: dict = {}
            if max_slippage_bps is not None:
                market_body["maxSlippageBps"] = max_slippage_bps
            order["marketIoc"] = market_body  # ИСПРАВЛЕНО: было "market" - API отвечал "unknown field"
        else:
            raise ValueError(f"Неизвестный order_type: {order_type}")

        if attached_risk is not None:
            order["attachedRisk"] = attached_risk

        return self._request("POST", "/v1/orders", body={"order": order})

    @staticmethod
    def build_take_profit_attached_risk(trigger_price: str, limit_price: Optional[str] = None) -> dict:
        """
        Готовый attachedRisk для 'на исполнении родителя выставить take-profit лимитку'.
        limit_price по умолчанию = trigger_price (продать лимиткой ровно на целевом уровне).
        Формат собран по документации API — первый реальный вызов покажет, верен ли он 1-в-1;
        если биржа вернёт ошибку валидации, поправим по тексту ошибки, как и раньше.
        """
        return {
            "oco": False,
            "takeProfit": {
                "triggerPrice": trigger_price,
                "child": {"limitGtc": {"price": limit_price or trigger_price}},
            },
        }

    def cancel_order(self, order_id: Optional[str] = None, client_order_id: Optional[str] = None, symbol: Optional[str] = None) -> Any:
        body = {"symbol": symbol}
        if order_id:
            body["orderId"] = order_id
        if client_order_id:
            body["clientOrderId"] = client_order_id
        return self._request("POST", "/v1/orders/cancel", body=body)

    def cancel_all_orders(
        self,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        dry_run: bool = False,
        request_id: Optional[str] = None,
    ) -> Any:
        """
        Отмена всех открытых ордеров счёта (не инстанса!). По документации:
        symbols — список, requestId обязателен, стоимость 10 единиц лимита.
        Бот в штатной работе этим не пользуется — снимает свои ордера по одному
        (документация не советует account-wide cancel для уборки за процессом).
        """
        body: dict = {
            "requestId": request_id or str(uuid.uuid4()),
            "dryRun": dry_run,
        }
        if symbol:
            body["symbols"] = [symbol]
        if side:
            body["side"] = side
        return self._request("POST", "/v1/orders/cancel-all", body=body)

    def cancel_all_after(self, timeout_sec: int, symbol: Optional[str] = None,
                         request_id: Optional[str] = None, side: Optional[str] = None) -> Any:
        """
        Dead-man switch: если не подтверждать вызов чаще timeout_sec — API сам отменит все ордера.
        По документации timeout_sec: 0 выключает, 10..120 взводит. Другое значение
        отсекаем до отправки, чтобы ошибка была понятной, а не 400 от биржи.
        """
        if timeout_sec != 0 and not 10 <= timeout_sec <= 120:
            raise ValueError(f"cancel_all_after: timeout_sec={timeout_sec}, допустимо 0 или 10..120")
        if side is not None and side.upper() not in ("BUY", "SELL"):
            raise ValueError(f"cancel_all_after: side={side}, допустимо BUY или SELL")
        body = {
            "timeoutSec": timeout_sec,
            "symbol": symbol,
            "requestId": request_id or str(uuid.uuid4()),
        }
        if side:
            body["side"] = side.upper()
        return self._request("POST", "/v1/orders/cancel-all-after", idempotent=True, body=body)

    def modify_order(
        self,
        order_id: str,
        symbol: str,
        new_qty_scaled: int,
        new_price: Optional[str] = None,
        request_id: Optional[str] = None,
        new_client_order_id: Optional[str] = None,
    ) -> Any:
        """
        Формат по REST-справочнику: symbol и requestId обязательны, количество
        передаётся ЦЕЛЫМ числом в масштабе пары (newQtyScaled = qty *
        10^baseQuantityScale), цена — строкой newPrice. Бот modify не использует
        (стратегия: amend не в v1), метод оставлен для песочницы. Живым вызовом
        не проверен.
        """
        body: dict = {
            "orderId": order_id,
            "symbol": symbol,
            "newQtyScaled": int(new_qty_scaled),
            "requestId": request_id or str(uuid.uuid4()),
            "behavior": "AMEND_OR_REPLACE",
        }
        if new_price is not None:
            body["newPrice"] = new_price
        if new_client_order_id:
            body["newClientOrderId"] = new_client_order_id
        return self._request("POST", "/v1/orders/modify", body=body)

    def preview_order(self, symbol: str, side: str, order_type: str, qty: str, price: Optional[str] = None) -> Any:
        order = {"symbol": symbol, "side": side, "baseQty": qty}
        if order_type == "limit":
            order["limitGtc"] = {"price": price}
        else:
            order["marketIoc"] = {}  # тот же ключ, что и в create_order — "market" API отвергает
        return self._request("POST", "/v1/orders/preview", body={"order": order})

    def batch_create_orders(self, orders: list[dict], request_id: Optional[str] = None) -> Any:
        """orders — список тел ордеров в том же формате, что create_order собирает внутри. Максимум 20."""
        if len(orders) > 20:
            raise ValueError("batch_create_orders: максимум 20 ордеров за вызов")
        return self._request(
            "POST",
            "/v1/orders/batch",
            body={"orders": orders, "requestId": request_id or str(uuid.uuid4())},
        )

    def get_open_orders(self, symbol: Optional[str] = None, limit: int = 1000) -> Any:
        """
        Открытые ордера счёта. В REST-справочнике фильтр symbol показан
        JSON-массивом (symbol=["BTC-USDT"]) — живая биржа на такое отвечает
        400 UNKNOWN_SYMBOL (проверено 18.09), а обычную строку принимает.
        Шлём строку и на всякий случай фильтруем ещё раз у себя.
        Лимит у биржи 1..1000; страницы докручиваем по nextPageToken, чтобы
        сверка никогда не работала по усечённому списку.
        """
        limit = max(1, min(1000, limit))
        orders: list = []
        page_token: Optional[str] = None
        for _ in range(20):  # защита от бесконечного цикла на битом токене
            data = self._request("GET", "/v1/orders/open",
                                 query={"symbol": symbol, "limit": limit, "pageToken": page_token})
            orders.extend(data.get("orders", []))
            page_token = data.get("nextPageToken") or None
            if not page_token:
                break
        if symbol:
            orders = [o for o in orders if o.get("symbol") == symbol]
        return {"orders": orders}

    def get_order_history(self, symbol: Optional[str] = None, limit: int = 100) -> Any:
        data = self._request("GET", "/v1/orders/history", query={"limit": max(1, min(1000, limit))})
        if symbol:
            data = {**data, "orders": [o for o in data.get("orders", []) if o.get("symbol") == symbol]}
        return data

    def get_order(
        self,
        order_id: str,
        include_attached_risk_state: bool = False,
        include_execution_history: bool = False,
    ) -> Any:
        """
        Состояние одного ордера. По умолчанию БЕЗ истории исполнений
        (includeExecutionHistory=false — документация называет это state-only
        polling): так опрос уровней каждые 15 с не тянет список сделок.
        С include_execution_history=True в ответе появляются trades с
        реальными комиссиями — это нужно, когда ордер закрылся.
        """
        return self._request(
            "GET",
            f"/v1/spot/orders/{order_id}",
            query={
                "includeAttachedRiskState": "true" if include_attached_risk_state else None,
                "includeAttachedRisk": "true" if include_attached_risk_state else None,
                "includeExecutionHistory": "true" if include_execution_history else "false",
            },
        )

    def get_user_trades(self, symbol: Optional[str] = None, order_id: Optional[str] = None,
                        limit: int = 100) -> Any:
        """Наши исполнения (GET /v1/spot/trades): цена, объём, комиссия, maker/taker."""
        return self._request(
            "GET", "/v1/spot/trades",
            query={"symbol": symbol, "orderId": order_id, "limit": max(1, min(1000, limit))},
        )


# --------------------------------------------------------------------------
# Ручная проверка (read-only, без торговли) — запускать: python -m core.api_client
# --------------------------------------------------------------------------

if __name__ == "__main__":
    client = PolyesterClient()

    print("=== Spot config ===")
    print(json.dumps(client.get_spot_config(), indent=2, ensure_ascii=False)[:1000])

    print("\n=== Candles BTC-USDT (4h, 5 last) ===")
    print(json.dumps(client.get_candles("BTC-USDT", "4h", limit=5), indent=2, ensure_ascii=False))

    print("\n=== Balances ===")
    print(json.dumps(client.get_balances(), indent=2, ensure_ascii=False))
