"""docs_sweep: GET-эндпоинты справочника REST против живого API (только чтение).
Для каждой страницы docs/polyester_docs/api-docs/rest__GET__v1__*.txt:
  1) пример из справочника дословно (URL из раздела ПРИМЕРЫ);
  2) вызов с реальными параметрами (подстановки из SUBS);
  3) пробы: без обязательного параметра, лишний параметр, неверный limit;
  4) сверка полей ответа с примером ответа справочника (имя, тип JSON, есть/нет).
Сырые ответы — docs/raw_sweep_<раздел>.jsonl. Запуск: python3 tests/docs_sweep.py <раздел>
С компьютера автора (из облака закрыто Cloudflare): python tests/docs_sweep.py local > docs/sweep_local.txt
Нужны ключи в .env (как для бота). Только GET, ничего не создаёт и не меняет."""
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qsl, quote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api_client import PolyesterClient, _canonical_query  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DOCS = os.path.join(ROOT, "docs/polyester_docs/api-docs")

SECTIONS = {
    "spot": ["spot__config", "spot__fee-rates", "spot__markets__overview", "spot__markets__symbol__candles",
             "spot__markets__symbol__trades", "spot__markets__volume-history", "spot__orders__order_id",
             "spot__trades", "orders__history", "orders__open", "orderbook__symbol",
             "market__currency-conversion__config", "market__currency-conversion__rates",
             "trading__daily-claim", "trading__rate-limits"],
    "account": ["balances", "balances__history", "transfers", "equity__history__series",
                "equity__portfolio__history__series", "equity__portfolio__snapshot", "vip__status", "vip__tiers",
                "rate-limits"],
    "triggers": ["triggers", "triggers__trigger_id", "triggers__trigger_id__events"],
    "chain": ["chain__analytics__unified-asset-balances", "chain__analytics__zipped-asset-supply",
              "chain__analytics__zipped-asset-supply__group", "chain__deposit-addresses",
              "chain__deposit-withdraw__config", "chain__flows", "chain__flows__by-tx__tx_hash__matches",
              "chain__flows__flow_id", "chain__guard-signer__status"],
    # всё, что из облака закрыто Cloudflare («Just a moment…»): запускать с компьютера автора
    "local": ["trading__daily-claim", "trading__rate-limits", "vip__status", "vip__tiers", "rate-limits",
              "chain__deposit-withdraw__config", "chain__deposit-addresses", "chain__flows", "chain__flows__flow_id",
              "chain__flows__by-tx__tx_hash__matches", "chain__guard-signer__status",
              "chain__analytics__unified-asset-balances", "chain__analytics__zipped-asset-supply",
              "chain__analytics__zipped-asset-supply__group",
              "polychart__markets__engine_symbol_id__layers", "polychart__markets__engine_symbol_id__layers__inbox",
              "layouts", "layouts__templates__subscriptions", "collab__whiteboards",
              "auth__profile", "auth__api-keys", "auth__subaccounts", "auth__policies__api-keys", "auth__mfa__factors",
              "auth__address-books", "auth__transfer-destinations"],
    "rest": ["polychart__markets__engine_symbol_id__layers", "polychart__markets__engine_symbol_id__layers__inbox",
             "polychart__owners__owner_id__published__layers", "layouts", "layouts__layout_id",
             "layouts__owners__owner_id__published", "layouts__templates__owner_id__template_id__versions",
             "layouts__templates__owner_id__template_id__versions__version", "layouts__templates__subscriptions",
             "share__layer__token", "share__layout__token", "collab__whiteboards", "collab__whiteboards__board_id",
             "auth__me", "auth__profile", "auth__api-keys", "auth__subaccounts", "auth__policies__api-keys",
             "auth__mfa__factors", "auth__address-books", "auth__transfer-destinations"],
}

# реальные значения для подстановки в путь (ключ — имя сегмента {..} в пути справочника)
SUBS = {"symbol": "ETH-USDT", "order_id": "FJ2wTecwwgQ", "trigger_id": "ZfhZc1pbB5D", "engine_symbol_id": "2"}


SECTIONS["local312"] = SECTIONS["local"]  # тот же список, запуск на Python 3.12 — ответы в отдельный файл


def page(name):
    t = open(os.path.join(DOCS, f"rest__GET__v1__{name}.txt"), encoding="utf-8").read()
    body = t.split("=== ПРИМЕРЫ")[0]
    ex = t.split("=== ПРИМЕРЫ (дословно) ===")[1] if "=== ПРИМЕРЫ (дословно) ===" in t else ""
    url = re.search(r"--url '([^']+)'", ex or t)
    path = re.search(r"^Path\n(\S+)", body, re.M)
    blocks = ex.split("\n---\n")
    resp = None
    for b in blocks[1:]:
        try:
            resp = json.loads(b.strip())
            break
        except Exception:
            continue
    # параметры: блоки "Query parameters" / "Path parameters" до "Response"
    params = {}
    for sect in ("Path parameters", "Query parameters"):
        m = re.search(sect + r"\n(.*?)\n(?:Query parameters|Response|Request body|Possible errors)\n", body, re.S)
        if not m:
            continue
        lines = [l for l in m.group(1).split("\n") if not l.startswith(("span]", "svg]"))]
        i = 0
        while i < len(lines):
            n = lines[i]
            if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", n) and i + 1 < len(lines) and re.match(
                    r"^(string|integer|array|boolean|number|enum|object)", lines[i + 1]):
                req = False
                j = i + 2
                while j < len(lines) and not (re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", lines[j]) and j + 1 < len(lines)
                                              and re.match(r"^(string|integer|array|boolean|number|enum|object)", lines[j + 1])):
                    if lines[j] == "required":
                        req = True
                    j += 1
                params[n] = {"in": "path" if sect.startswith("Path") else "query", "type": lines[i + 1],
                             "required": req, "desc": " ".join(lines[i + 2:j])[:200]}
                i = j
            else:
                i += 1
    return {"path": path.group(1) if path else None, "url": url.group(1) if url else None,
            "resp": resp, "params": params}


class Sweep:
    def __init__(self, section):
        self.c = PolyesterClient()
        self.raw = os.path.join(ROOT, f"docs/raw_sweep_{section}.jsonl")

    def get(self, label, path, query=""):
        canon = _canonical_query(query)
        headers = self.c._sign("GET", path, canon, b"")
        url = self.c.base_url + path + (("?" + canon) if canon else "")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        try:
            r = self.c._session.get(url, headers=headers, timeout=20)
            status, text, ctype = r.status_code, r.text, r.headers.get("Content-Type", "")
        except Exception as e:
            status, text, ctype = None, f"EXC {type(e).__name__}: {e}", ""
        kid = self.c.creds.key_id
        if kid and kid in text:
            text = text.replace(kid, "ak_***скрыт***")
        with open(self.raw, "a", encoding="utf-8") as f:
            f.write(json.dumps({"label": label, "utc": ts, "method": "GET", "path": path, "query": canon,
                                "http": status, "content_type": ctype, "response_raw": text}, ensure_ascii=False) + "\n")
        try:
            j = json.loads(text)
        except Exception:
            j = None
        return status, j, text


def jtype(v):
    return {dict: "object", list: "array", str: "string", bool: "boolean", type(None): "null"}.get(
        type(v), "number")


def shape(v, pre="", out=None):
    """Плоская карта путь -> тип (массивы — по первому элементу)."""
    out = {} if out is None else out
    if isinstance(v, dict):
        for k, x in v.items():
            p = f"{pre}.{k}" if pre else k
            out[p] = jtype(x)
            shape(x, p, out)
    elif isinstance(v, list) and v:
        shape(v[0], pre + "[]", out)
    return out


def merged_shape(v):
    """Как shape, но по всем элементам массивов (чтобы не считать «нет поля» из-за одного элемента)."""
    out = {}

    def walk(x, pre):
        if isinstance(x, dict):
            for k, y in x.items():
                p = f"{pre}.{k}" if pre else k
                out.setdefault(p, set()).add(jtype(y))
                walk(y, p)
        elif isinstance(x, list):
            for y in x[:200]:
                walk(y, pre + "[]")
    walk(v, "")
    return out


def fill(path_tpl, url_path):
    """Путь с реальными значениями: {name} -> SUBS[name]."""
    def rep(m):
        return SUBS.get(m.group(1), m.group(0))
    return re.sub(r"\{([a-zA-Z_]+)\}", rep, path_tpl)


def main():
    section = sys.argv[1]
    names = sys.argv[2:] or SECTIONS[section]
    sw = Sweep(section)
    summary = {}
    if any(n.startswith("chain__flows__") for n in names):
        st, j, t = sw.get("chain__flows | для подстановки id", "/v1/chain/flows", "limit=5")
        for fl in (j or {}).get("flows", []) if isinstance(j, dict) else []:
            SUBS.setdefault("flow_id", fl.get("flowId") or fl.get("id"))
            for k in ("txHash", "sourceTxHash", "destinationTxHash"):
                if fl.get(k):
                    SUBS.setdefault("tx_hash", fl[k])
            for v in fl.values():
                if isinstance(v, dict):
                    for k in ("txHash", "hash"):
                        if v.get(k):
                            SUBS.setdefault("tx_hash", v[k])
        print(f"подстановка: flow_id={SUBS.get('flow_id')} tx_hash={SUBS.get('tx_hash')}")
    for name in names:
        pg = page(name)
        tpl = pg["path"]
        print(f"\n### {name}  ({tpl})")
        res = {"doc_params": pg["params"], "calls": {}}
        # 1. пример справочника дословно
        if pg["url"]:
            u = urlsplit(pg["url"])
            st, j, t = sw.get(f"{name} | пример справочника", u.path, u.query)
            res["calls"]["example"] = (st, u.path + ("?" + u.query if u.query else ""))
            print(f"  пример: {u.path}{'?' + u.query if u.query else ''} -> {st} {t[:160] if st != 200 else ''}")
        # 2. реальный вызов
        real_path = fill(tpl, None)
        q = []
        for pn, pd in pg["params"].items():
            if pd["in"] == "query" and pd["required"]:
                q.append((pn, SUBS.get(pn, "ETH-USDT" if pn == "symbol" else "")))
        if name == "spot__markets__symbol__candles":
            q = [("timeframe", "1m"), ("limit", "5")]
        qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in q)
        st, j, t = sw.get(f"{name} | реальный", real_path, qs)
        res["calls"]["real"] = (st, real_path + ("?" + qs if qs else ""))
        print(f"  реальный: {real_path}{'?' + qs if qs else ''} -> {st} {t[:200] if st != 200 else ''}")
        live = j if st == 200 else None
        # 3. сверка полей с примером ответа
        if live is not None and pg["resp"] is not None:
            doc = shape(pg["resp"])
            got = merged_shape(live)
            missing = [k for k in doc if k not in got]
            extra = [k for k in got if k not in doc]
            tdiff = [(k, doc[k], sorted(got[k])) for k in doc if k in got and doc[k] not in got[k]
                     and not (got[k] == {"null"})]
            res["missing"], res["extra"], res["types"] = missing, extra, tdiff
            if missing:
                print(f"  НЕТ в ответе (есть в примере): {missing[:12]}{' …' if len(missing) > 12 else ''}")
            if extra:
                print(f"  ЛИШНИЕ в ответе (нет в примере): {extra[:12]}{' …' if len(extra) > 12 else ''}")
            if tdiff:
                print(f"  ТИП отличается: {tdiff[:8]}")
            if not (missing or extra or tdiff):
                print("  поля совпадают с примером")
        # 4. пробы параметров
        req_q = [pn for pn, pd in pg["params"].items() if pd["in"] == "query" and pd["required"]]
        if req_q and qs:
            st2, j2, t2 = sw.get(f"{name} | без обязательного {req_q[0]}", real_path,
                                 "&".join(f"{k}={quote(str(v), safe='')}" for k, v in q if k != req_q[0]))
            print(f"  без обязательного {req_q[0]}: {st2} {t2[:140]}")
            res["calls"]["no_required"] = (st2, t2[:300])
        st3, j3, t3 = sw.get(f"{name} | лишний параметр foo=1", real_path, (qs + "&" if qs else "") + "foo=1")
        print(f"  лишний foo=1: {st3} {t3[:120] if st3 != 200 else ''}")
        res["calls"]["extra_param"] = (st3, t3[:300] if st3 != 200 else "")
        if "limit" in pg["params"]:
            for lim in ("0", "100000", "-1", "abc"):
                st4, j4, t4 = sw.get(f"{name} | limit={lim}", real_path, (qs + "&" if qs else "") + f"limit={lim}")
                n = None
                if isinstance(j4, dict):
                    arrs = [v for v in j4.values() if isinstance(v, list)]
                    n = len(arrs[0]) if arrs else None
                print(f"  limit={lim}: {st4} {('n=' + str(n)) if st4 == 200 else t4[:120]}")
                res["calls"][f"limit_{lim}"] = (st4, n if st4 == 200 else t4[:300])
            print(f"  справочник limit: {pg['params']['limit']['type']} {pg['params']['limit']['desc'][:120]}")
        # 5. фильтры: действительно ли фильтруют
        FV = {"symbol": ["ETH-USDT"], "side": ["BUY"], "status": ["CANCELED", "FILLED"]}
        for fp, vals in FV.items():
            if fp not in pg["params"] or pg["params"][fp]["in"] != "query" or pg["params"][fp]["required"]:
                continue
            for v in vals:
                st5, j5, t5 = sw.get(f"{name} | фильтр {fp}={v}", real_path, (qs + "&" if qs else "") + f"{fp}={v}")
                items = []
                if isinstance(j5, dict):
                    arrs = [x for x in j5.values() if isinstance(x, list)]
                    items = arrs[0] if arrs else []
                vals_got = sorted({str(it.get(fp)) for it in items if isinstance(it, dict)})
                ok = all(g == v for g in vals_got) if vals_got else None
                print(f"  фильтр {fp}={v}: {st5} n={len(items)} значения={vals_got[:6]} {'OK' if ok else ('ПУСТО' if ok is None else 'НЕ ФИЛЬТРУЕТ')}"
                      + ("" if st5 == 200 else " " + t5[:120]))
                res["calls"][f"filter_{fp}_{v}"] = (st5, len(items), vals_got[:10])
            if pg["params"][fp]["type"].startswith("array") and fp == "symbol":
                for form, qv in (("повтор", "symbol=ETH-USDT&symbol=AVAX-USDT"), ("запятая", "symbol=ETH-USDT,AVAX-USDT")):
                    st6, j6, t6 = sw.get(f"{name} | symbol {form}", real_path, (qs + "&" if qs else "") + qv)
                    items = []
                    if isinstance(j6, dict):
                        arrs = [x for x in j6.values() if isinstance(x, list)]
                        items = arrs[0] if arrs else []
                    got = sorted({str(it.get("symbol")) for it in items if isinstance(it, dict)})
                    print(f"  symbol {form}: {st6} n={len(items)} символы={got[:6]}" + ("" if st6 == 200 else " " + t6[:120]))
        summary[name] = res
    out = os.path.join(ROOT, f"docs/sweep_{section}_summary.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
