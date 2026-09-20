#!/usr/bin/env python3
# OpenSearch 写入基线（在 opensearch 容器内运行；避免多层 shell 引号把 NDJSON 写坏）
#
# 环境变量：OS_PWD（必需）、OS_URL/OS_USER/OS_DOCS/OS_IDX（可选）
# 输出：一行 JSON 指标，供宿主侧解析

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("OS_URL", "https://localhost:9200")
USER = os.environ.get("OS_USER", "admin")
PWD = os.environ["OS_PWD"]
DOCS = int(os.environ.get("OS_DOCS", "50000"))
IDX = os.environ.get("OS_IDX", "bench-baseline") + "-" + str(int(time.time()))

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
AUTH = base64.b64encode(("%s:%s" % (USER, PWD)).encode()).decode()


def req(method, path, data=None, ctype="application/json"):
    body = data
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode()
    r = urllib.request.Request(BASE + path, data=body, method=method)
    r.add_header("Authorization", "Basic " + AUTH)
    if body is not None:
        r.add_header("Content-Type", ctype)
    with urllib.request.urlopen(r, timeout=180, context=CTX) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


out = {"index": IDX, "docs": DOCS}
try:
    req("PUT", "/" + IDX, {"settings": {"number_of_shards": 1, "number_of_replicas": 0}})

    lines = []
    for i in range(DOCS):
        lines.append('{"index":{}}')
        lines.append(json.dumps({"msg": "baseline payload %d" % i, "n": i}, separators=(",", ":")))
    payload = ("\n".join(lines) + "\n").encode()
    out["bytes"] = len(payload)

    t0 = time.time()
    # 索引必须出现在 URL 或每条 action 元数据里；用 {"index":{}} 的写法时 URL 必须带索引，
    # 否则 OpenSearch 报 "Validation Failed: index is missing"（实测踩坑）。
    resp = req("POST", "/%s/_bulk?refresh=false" % IDX, payload, "application/x-ndjson")
    out["elapsed"] = round(time.time() - t0, 3)
    out["errors"] = bool(resp.get("errors"))
    out["took"] = resp.get("took")
    items = resp.get("items") or []
    out["ok_items"] = sum(1 for it in items if it.get("index", {}).get("status") in (200, 201))
    out["first_error"] = next((it["index"].get("error") for it in items if it.get("index", {}).get("status", 0) >= 300), None)
    # bulk 用 refresh=false ⇒ 文档在 translog、尚未可搜，直接 _count 会是 0（实测踩坑）。
    # 显式 refresh 后再计数，才是真实的"落库计数"；refresh 耗时不计入写入吞吐。
    t0 = time.time()
    req("POST", "/%s/_refresh" % IDX)
    out["refresh_s"] = round(time.time() - t0, 3)
    out["count"] = req("GET", "/%s/_count" % IDX).get("count")
except urllib.error.HTTPError as e:  # 带上响应体，否则只看到 "400 Bad Request" 无从定位
    try:
        out["fatal"] = "HTTPError %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:600])
    except Exception:  # noqa: BLE001
        out["fatal"] = "HTTPError %s" % e.code
except Exception as e:  # noqa: BLE001
    out["fatal"] = "%s: %s" % (type(e).__name__, e)
finally:
    try:
        req("DELETE", "/" + IDX)
        out["deleted"] = True
    except Exception:  # noqa: BLE001
        out["deleted"] = False

print(json.dumps(out, ensure_ascii=False))
sys.exit(0)
