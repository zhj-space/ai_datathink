#!/usr/bin/env python3
"""S4-2 最小 RAG 闭环：OM 元数据 → 向量（pgvector）→ 检索 → 生成 → **引用程序化校验**。

依据：《设计方案 v3.2》S4-2（pgvector 向量索引 / 元数据摘要向量化管道 / RAG 链路 / 引用必须可核验）
边界：只走 **OpenMetadata REST API**（不读 OM 内表）；向量与文档存 **GA 自持库**的 `rag` schema。

实现形态（2026-09-18 按设计对齐）：**LangGraph 编排**（节点：retrieve → generate → verify → END），
LLM 客户端用 `langchain-ollama` 的 `ChatOllama`；嵌入沿用 Ollama HTTP 接口（`/api/embed`）。
历史：首版曾以"直连三步函数"落地（未引入 langgraph），后按人工要求改为本实现；
**接口未变**（`ingest()` / `answer()` / `retrieve()` 语义一致）。

用法：
  python3 rag.py --ingest       # 离线刷新向量库（OM → rag.doc）
  python3 rag.py --ask "问题"    # 命令行问答（排障用）
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import TypedDict

import psycopg2
import psycopg2.extras

from langchain_ollama import ChatOllama          # S4-2：按设计引入 langchain-ollama
from langgraph.graph import END, StateGraph      # S4-2：按设计引入 LangGraph

OM_BASE = os.getenv("GA_OM_BASE", os.getenv("OM_BASE", "http://openmetadata-server:8585/api/v1"))
OLLAMA = os.getenv("GA_OLLAMA_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("GA_RAG_MODEL", "qwen2.5:1.5b")
EMBED_MODEL = os.getenv("GA_EMBED_MODEL", "nomic-embed-text")
TOP_K = int(os.getenv("GA_RAG_TOP_K", "4"))
ENVF = "/data/ai-governance/.env"

# ------------------------------------------------------------------ 上下文预算（2026-09-19 新增，B 项）
# 为什么必须有：S4-1 的门禁判据是「流式首 token < 10s」，而 prefill 耗时近似随上下文长度线性增长
#   ⇒ **没有上下文预算，10s 门禁就没有稳定口径**（S4-1 §4 第 3 项）。
# 实测事实（2026-09-19，本环境 qwen2.5:1.5b）：
#   * Ollama **默认 num_ctx 只有 2050 tokens**，超出部分被**静默截断**：
#     8,158 字符与 24,558 字符的 prompt，`prompt_eval_count` **都恰好是 2050**（没报错、没提示）。
#   * 本平台文档格式（中文描述 + 英文 FQN/字段名）实测 **1.70 字符/token**；这里取 **1.5 保守值**
#     ⇒ 估算 token 数偏大，宁可少喂上下文，也不冒"实际超预算"的风险。
# 口径：`GA_RAG_CTX_BUDGET_TOKENS` 是**整条 prompt（模板 + 上下文 + 问题）**的上限，
#   不是仅上下文部分；且默认 num_ctx(4096) > 预算 + 输出(256)，避免被 Ollama 再截一次。
# **取值 1800（2026-09-19 人工决定：档位保持 1.5b + 压预算）**，实测依据（`qwen2.5:1.5b`、num_ctx=4096、每样本换上下文规避 prompt cache）：
#   实际 1,576 tokens → 冷启动 6.88s / 热态 4.64s；1,819 tokens → 6.93s / 4.85s；2,062 tokens → 7.70s / 5.66s
#   （判据：冷启动首 token < 8.0s，即对 M0 的 10s 门禁留 20% 余量 ⇒ 取范围内**最大且余量充足**的 1800）。
#   背景：3b 在 3,000 预算下冷启动 20.68s、热态 17.3~17.4s（明确不通过）；1.5b 在 3,000 预算下冷启动 11.31s（越线）。
CTX_BUDGET_TOKENS = int(os.getenv("GA_RAG_CTX_BUDGET_TOKENS", "1800"))
NUM_CTX = int(os.getenv("GA_RAG_NUM_CTX", "4096"))
CHARS_PER_TOKEN = float(os.getenv("GA_RAG_CHARS_PER_TOKEN", "1.5"))
MIN_CTX_TOKENS = int(os.getenv("GA_RAG_MIN_CTX_TOKENS", "200"))   # 预算再小也给上下文留这些
TRUNCATE_MIN_CHARS = int(os.getenv("GA_RAG_TRUNCATE_MIN_CHARS", "200"))  # 单篇截断后碎于此刻则整篇丢弃

DB_CONF = {
    "host": os.getenv("GA_DB_HOST", "pgbouncer"),
    "port": int(os.getenv("GA_DB_PORT", "6432")),
    "dbname": os.getenv("GA_DB_NAME", "governance_agent"),
    "user": os.getenv("GA_DB_USER", "ga_writer"),
    "password": os.getenv("GA_DB_PASSWORD", ""),
}

ENTITY_INDEX = {
    "table": "table_search_index",
    "database": "database_search_index",
    "databaseSchema": "database_schema_search_index",
    "dashboard": "dashboard_search_index",
    "pipeline": "pipeline_search_index",
    "topic": "topic_search_index",
    "mlmodel": "mlmodel_search_index",
    "container": "container_search_index",
    "glossaryTerm": "glossary_term_search_index",
}


def env(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    try:
        with open(ENVF, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def db():
    conn = psycopg2.connect(**DB_CONF)
    conn.autocommit = True
    return conn


def om_login() -> str:
    pw = env("OPENMETADATA_ADMIN_PASSWORD")
    body = json.dumps({"email": "admin@open-metadata.org",
                       "password": base64.b64encode(pw.encode()).decode()}).encode()
    req = urllib.request.Request(OM_BASE + "/users/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}").get("accessToken", "")


def om_get(token: str, path: str):
    req = urllib.request.Request(OM_BASE + path)
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read() or b"{}")


def collect_docs(token: str, per_type_limit: int = 200) -> list:
    """从 OM 搜索 API 收集实体 → 文档（只走 REST API，不读 OM 内表）。"""
    docs = []
    for entity_type, index in ENTITY_INDEX.items():
        try:
            page = om_get(token, f"/search/query?q=*&index={index}&size={per_type_limit}&from=0")
        except urllib.error.HTTPError:
            continue
        for hit in page.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            fqn = src.get("fullyQualifiedName") or src.get("name") or ""
            if not fqn:
                continue
            owners = ", ".join(o.get("name", "") for o in (src.get("owners") or []) if isinstance(o, dict))
            tags = ", ".join(t.get("tagFQN", "") for t in (src.get("tags") or []) if isinstance(t, dict))
            cols = src.get("columns") or []
            col_txt = ", ".join(c.get("name", "") for c in cols if isinstance(c, dict))[:2000]
            parts = [
                f"类型：{entity_type}",
                f"名称：{src.get('name', '')}",
                f"限定名（FQN）：{fqn}",
                f"描述：{src.get('description') or '（无）'}",
                f"负责人：{owners or '（无）'}",
                f"标签：{tags or '（无）'}",
            ]
            if col_txt:
                parts.append(f"字段：{col_txt}")
            docs.append({"doc_id": f"{entity_type}:{fqn}", "doc_type": entity_type, "fqn": fqn,
                         "title": src.get("name", fqn), "content": "\n".join(parts)})
    return docs


def embed(text: str) -> list:
    body = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    req = urllib.request.Request(OLLAMA + "/api/embed", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            vec = (json.loads(resp.read() or b"{}").get("embeddings") or [[]])[0]
    except urllib.error.HTTPError:
        body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
        req = urllib.request.Request(OLLAMA + "/api/embeddings", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=300) as resp:
            vec = json.loads(resp.read() or b"{}").get("embedding", [])
    if not vec:
        raise RuntimeError("嵌入返回为空")
    return vec


def vec_literal(vec: list) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def ingest() -> int:
    token = om_login()
    docs = collect_docs(token)
    written = 0
    with db() as conn, conn.cursor() as cur:
        for d in docs:
            cur.execute(
                "INSERT INTO rag.doc (doc_id, doc_type, fqn, title, content, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s::vector) "
                "ON CONFLICT (doc_id) DO UPDATE SET content=EXCLUDED.content, embedding=EXCLUDED.embedding, "
                "title=EXCLUDED.title, doc_type=EXCLUDED.doc_type, updated_at=now()",
                (d["doc_id"], d["doc_type"], d["fqn"], d["title"], d["content"], vec_literal(embed(d["content"]))))
            written += 1
        if docs:
            cur.execute("DELETE FROM rag.doc WHERE doc_id <> ALL(%s)", ([d["doc_id"] for d in docs],))
    return written


def retrieve(question: str, k: int = TOP_K) -> list:
    qv = vec_literal(embed(question))
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT doc_id, doc_type, fqn, title, content, 1 - (embedding <=> %s::vector) AS score "
            "FROM rag.doc ORDER BY embedding <=> %s::vector LIMIT %s", (qv, qv, k))
        return [dict(r) for r in cur.fetchall()]


def est_tokens(text: str) -> int:
    """字符数 → token 的**保守估算**（仅用于上下文预算裁剪；真实值以 Ollama 的 prompt_eval_count 为准）。"""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def fit_contexts(question: str, contexts: list, budget: int = CTX_BUDGET_TOKENS) -> tuple:
    """按预算裁剪检索上下文：按相关度顺序装入，装不下的**截断或丢弃**，并如实记录（宁缺勿爆）。

    为什么不是"全量喂进去"：超出 Ollama `num_ctx` 会被**静默截断**（模型看不到、日志也不报），
    而 RAG 的回答又必须可核验 ⇒ 宁可少喂、并把"丢了几篇/截了几篇"显式暴露出来。
    返回 `(contexts, stats)`；`stats` 会随 `/chat` 返回，供验收与排障核对。
    """
    skeleton_tokens = est_tokens(PROMPT_TEMPLATE.format(ctx="", question=question))
    remaining = max(budget - skeleton_tokens, MIN_CTX_TOKENS)
    kept, used, dropped, truncated = [], 0, 0, False
    for c in contexts:
        block_tokens = est_tokens(f"[{c['fqn']}]\n{c['content']}")
        if used + block_tokens <= remaining:
            kept.append(c)
            used += block_tokens
            continue
        room_chars = int(max(remaining - used, 0) * CHARS_PER_TOKEN)
        if room_chars >= TRUNCATE_MIN_CHARS:
            cut = dict(c)
            cut["content"] = c["content"][:room_chars] + "\n…（已按上下文预算截断）"
            kept.append(cut)
            used += est_tokens(cut["content"])
            truncated = True
        # 相关度排序在前的都装不下/截断后无余地 ⇒ 后面的（更不相关）一律不再尝试
        dropped = len(contexts) - len(kept)
        break
    stats = {
        "budget_tokens": budget,
        "num_ctx": NUM_CTX,
        "prompt_tokens_est": skeleton_tokens + used,
        "skeleton_tokens_est": skeleton_tokens,
        "context_tokens_est": used,
        "docs_total": len(contexts),
        "docs_used": len(kept),
        "docs_dropped": dropped,
        "truncated": truncated,
        "chars_per_token": CHARS_PER_TOKEN,
    }
    return kept, stats


_LLM = None


def llm() -> ChatOllama:
    """惰性构造 ChatOllama（按设计使用 langchain-ollama 客户端）。"""
    global _LLM
    if _LLM is None:
        # num_ctx 必须显式给：Ollama 默认仅 2050 tokens（实测），不设会静默截断上下文
        _LLM = ChatOllama(model=LLM_MODEL, base_url=OLLAMA, temperature=0.1,
                          num_predict=256, num_ctx=NUM_CTX)
    return _LLM


PROMPT_TEMPLATE = (
    "你是数据治理助手。**只能依据下面提供的元数据上下文回答**；上下文没有的信息必须回答"
    "「元数据中未找到」，不得编造。引用资产时必须用方括号写出其限定名（FQN）。\n\n"
    "=== 元数据上下文 ===\n{ctx}\n=== 问题 ===\n{question}\n=== 回答 ===\n")


def build_prompt(question: str, contexts: list) -> str:
    ctx = "\n\n".join(f"[{c['fqn']}]\n{c['content']}" for c in contexts) or "（无可用上下文）"
    return PROMPT_TEMPLATE.format(ctx=ctx, question=question)


def generate(question: str, contexts: list) -> str:
    """单步生成（保留为函数，便于单独调用/测试）。"""
    return (llm().invoke(build_prompt(question, contexts)).content or "").strip()


CITE_RE = re.compile(r"\[([A-Za-z0-9_][A-Za-z0-9_.\-]{2,})\]")


def verify_citations(answer_text: str) -> tuple:
    """引用程序化校验：回答中方括号引用的 FQN 必须在 OpenMetadata 真实存在（依据 rag.doc，其数据来自 OM API），
    否则**标注未核实**（不静默丢弃、也不直接放行）。"""
    cited = sorted(set(CITE_RE.findall(answer_text)))
    if not cited:
        return answer_text, []
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT fqn FROM rag.doc")
        known = {r[0] for r in cur.fetchall()}
    unverified = [c for c in cited if c not in known]
    if not unverified:
        return answer_text, []
    warn = ("⚠ **引用未核实**（以下标识在 OpenMetadata 中不存在，可能为模型编造）："
            + "、".join(f"`{u}`" for u in unverified))
    return warn + "\n\n" + answer_text, unverified


class RagState(TypedDict, total=False):
    """LangGraph 状态：问题 → 检索上下文 → 生成答案 → 校验后的答案。"""
    question: str
    contexts: list
    answer: str
    unverified: list
    nodes: list          # 记录实际执行的节点（供验收取证）
    ctx_stats: dict      # 上下文预算执行情况（估算/实际 token、丢弃与截断篇数）


def node_retrieve(state: RagState) -> RagState:
    contexts, stats = fit_contexts(state["question"], retrieve(state["question"]))
    return {"contexts": contexts, "ctx_stats": stats,
            "nodes": state.get("nodes", []) + ["retrieve"]}


def node_generate(state: RagState) -> RagState:
    msg = llm().invoke(build_prompt(state["question"], state.get("contexts", [])))
    stats = dict(state.get("ctx_stats") or {})
    usage = getattr(msg, "usage_metadata", None) or {}
    if usage:   # 实际 token 数由 Ollama 返回（prompt_eval_count），用来核对保守估算是否成立
        stats["prompt_tokens_actual"] = usage.get("input_tokens")
        stats["output_tokens_actual"] = usage.get("output_tokens")
    return {"answer": (msg.content or "").strip(), "ctx_stats": stats,
            "nodes": state.get("nodes", []) + ["generate"]}


def node_verify(state: RagState) -> RagState:
    text, unverified = verify_citations(state.get("answer", ""))
    return {"answer": text, "unverified": unverified,
            "nodes": state.get("nodes", []) + ["verify"]}


_GRAPH = None


def graph():
    """按设计构建 LangGraph RAG 链路：retrieve → generate → verify → END。"""
    global _GRAPH
    if _GRAPH is None:
        g = StateGraph(RagState)
        g.add_node("retrieve", node_retrieve)
        g.add_node("generate", node_generate)
        g.add_node("verify", node_verify)
        g.set_entry_point("retrieve")
        g.add_edge("retrieve", "generate")
        g.add_edge("generate", "verify")
        g.add_edge("verify", END)
        _GRAPH = g.compile()
    return _GRAPH


def answer(question: str) -> dict:
    """RAG 主链（LangGraph 编排）：retrieve → generate → verify。"""
    state = graph().invoke({"question": question, "nodes": []})
    nodes = state.get("nodes", [])
    contexts = state.get("contexts") or []
    ctx = state.get("ctx_stats") or {}
    if not contexts:
        return {"ok": False, "reason": "向量库为空（尚未摄入元数据）", "answer": "",
                "citations": [], "nodes": nodes, "ctx": ctx}
    return {"ok": True, "answer": state.get("answer", ""),
            "citations": [c["fqn"] for c in contexts],
            "unverified": state.get("unverified", []), "nodes": nodes, "ctx": ctx}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", action="store_true", help="刷新向量库（OM → rag.doc）")
    ap.add_argument("--ask", help="提问（排障用）")
    args = ap.parse_args()
    if args.ingest:
        n = ingest()
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM rag.doc")
            print(f"摄入完成：写入 {n} 篇；rag.doc 现有 {cur.fetchone()[0]} 篇")
        return 0
    if args.ask:
        print(json.dumps(answer(args.ask), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
