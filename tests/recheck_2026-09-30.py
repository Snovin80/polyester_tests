"""ПЕРЕПРОВЕРКА ОТЧЁТА 30.09 (docs/report_2026-09-30.txt): сверка справочника REST против API.
Пункты: примеры с десятичным id; preview без комиссий; пример flow_id; Cloudflare для Python 3.14;
маршруты 404 (guard-signer, polychart, layouts); collab SIGNATURE_INVALID и auth/api-keys forbidden.

Только чтение: ничего не создаёт и не меняет (preview ордер не ставит).
Запуск из папки polyester_tests (нужен .env с ключами):
    py -3.12 tests\\recheck_2026-09-30.py
Как читать: по строке на пункт. [ВСЁ ЕЩЁ] — ошибка на месте, абзац отчёта оставить.
[ИСПРАВЛЕНО] — абзац убрать. [НЕ ПРОВЕРЕНО] — не удалось проверить (причина в строке).
Итог сохраняется в docs/recheck_2026-09-30_result.txt."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api_client import PolyesterClient, _canonical_query  # noqa: E402

C = PolyesterClient()
DOCS = "https://testnet.polyester.com/docs/api-docs/rest"
LINES = []


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


def doc_page(path):
    """Текст страницы справочника (requests, при Cloudflare — curl)."""
    import requests
    try:
        t = requests.get(DOCS + path, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        if "Just a moment" not in t:
            return t
    except Exception:
        pass
    try:
        return subprocess.run(["curl", "-s", DOCS + path], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def doc_still(name, path, needle):
    t = doc_page(path)
    if not t or "Just a moment" in t:
        out("НЕ ПРОВЕРЕНО", name, "страница справочника не открылась")
    elif needle in t:
        out("ВСЁ ЕЩЁ", name, f"на странице по-прежнему «{needle}»")
    else:
        out("ИСПРАВЛЕНО", name, f"на странице больше нет «{needle}»")


def main():
    print("Перепроверка отчёта 30.09 —", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
          "— Python", sys.version.split()[0])

    # 1. Примеры с десятичным id
    doc_still("Пример GET /v1/spot/orders/{order_id} (справочник)", "/GET/v1/spot/orders/order_id", "9876543210123")
    doc_still("Примеры /v1/triggers/{trigger_id} (справочник)", "/GET/v1/triggers/trigger_id", "9155001234567")
    st, j, t = call("GET", "/v1/spot/orders/9876543210123", "includeExecutionHistory=true&limit=100")
    out("ВСЁ ЕЩЁ" if st == 400 else "ИЗМЕНИЛОСЬ", "API на пример /v1/spot/orders/9876543210123", f"HTTP {st} {t[:90]}")

    # 2. Preview без комиссий и quote debit
    doc_still("Preview: описание обещает quote debit и fees", "/POST/v1/orders/preview", "quote debit, and fees")
    body = {"order": {"symbol": "ETH-USDT", "side": "BUY", "baseQty": "0.005", "feeAsset": "QUOTE",
                      "selfTradePreventionMode": "EXPIRE_MAKER", "marketIoc": {}}}
    st, j, t = call("POST", "/v1/orders/preview", body=body)
    if st == 200 and isinstance(j, dict):
        keys = [k for k in j if "fee" in k.lower() or "debit" in k.lower()]
        out("ИСПРАВЛЕНО" if keys else "ВСЁ ЕЩЁ", "Preview: в ответе нет комиссий/quote debit",
            f"поля ответа {sorted(j)}")
        pb = str(j.get("protectedPriceBound", ""))
        out("ВСЁ ЕЩЁ" if "." in pb or (pb.isdigit() and len(pb) < 9) else "ИЗМЕНИЛОСЬ",
            "protectedPriceBound десятичный (а в доках scaled 1e9)", f"значение {pb}")
    else:
        out("НЕ ПРОВЕРЕНО", "Preview", f"HTTP {st} {t[:120]}")

    # 3. Пример flow_id
    doc_still("Пример flow_id с буквой I (справочник)", "/GET/v1/chain/flows/flow_id", "flow_7YWHMfk9JZeLMgZauHuiSxhI")
    st, j, t = call("GET", "/v1/chain/flows/flow_7YWHMfk9JZeLMgZauHuiSxhI")
    out("ВСЁ ЕЩЁ" if st == 400 else "ИЗМЕНИЛОСЬ", "API на пример flow_7YWHMfk9JZeLMgZauHuiSxhI", f"HTTP {st} {t[:90]}")

    # 4. Cloudflare для Python 3.14
    code = ("import requests,sys;r=requests.get('https://api.testnet.polyester.com/v1/rate-limits',timeout=20);"
            "print(sys.version.split()[0],r.status_code,r.headers.get('cf-mitigated'))")
    try:
        res = subprocess.run(["py", "-3.14", "-c", code], capture_output=True, text=True, timeout=60)
        o = (res.stdout or res.stderr).strip()
        if " 403 challenge" in o:
            out("ВСЁ ЕЩЁ", "Cloudflare: /v1/rate-limits на Python 3.14", o)
        elif " 200 " in o:
            out("ИСПРАВЛЕНО", "Cloudflare: /v1/rate-limits на Python 3.14", o)
        else:
            out("НЕ ПРОВЕРЕНО", "Cloudflare: Python 3.14", o[:150])
    except Exception as e:
        out("НЕ ПРОВЕРЕНО", "Cloudflare: Python 3.14 не запустился", str(e)[:120])

    # 5. Маршруты 404
    for p in ["/v1/chain/guard-signer/status", "/v1/polychart/markets/2/layers", "/v1/polychart/markets/2/layers/inbox",
              "/v1/layouts", "/v1/layouts/templates/subscriptions"]:
        st, j, t = call("GET", p)
        if "Just a moment" in t:
            out("НЕ ПРОВЕРЕНО", f"Маршрут {p}", "Cloudflare — запускать на Python 3.12")
        else:
            out("ВСЁ ЕЩЁ" if st == 404 else "ИСПРАВЛЕНО", f"Маршрут {p} (404)", f"HTTP {st} {t[:80]}")

    # 6. collab и auth/api-keys
    st, j, t = call("GET", "/v1/collab/whiteboards")
    out("ВСЁ ЕЩЁ" if "SIGNATURE_INVALID" in t else ("НЕ ПРОВЕРЕНО" if "Just a moment" in t else "ИСПРАВЛЕНО"),
        "GET /v1/collab/whiteboards (401 SIGNATURE_INVALID)", f"HTTP {st} {t[:80]}")
    st, j, t = call("GET", "/v1/auth/api-keys")
    out("ВСЁ ЕЩЁ" if st == 403 and "forbidden" in t else ("НЕ ПРОВЕРЕНО" if "Just a moment" in t else "ИСПРАВЛЕНО"),
        "GET /v1/auth/api-keys (403 forbidden)", f"HTTP {st} {t[:80]}")

    path = os.path.join(ROOT, "docs", "recheck_2026-09-30_result.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") + "\n" + "\n".join(LINES) + "\n")
    print("\nИтог сохранён:", path)


if __name__ == "__main__":
    main()
