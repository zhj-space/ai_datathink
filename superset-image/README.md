# Superset 派生镜像

> **为什么需要**：官方 `apache/superset` 镜像是 **lean** 变体，**不含 PostgreSQL 驱动**（实测 6.1.0：`psycopg2` 与 `psycopg` 均缺失）。Superset 既要连自己的**元数据库**（本项目统一走 PgBouncer 6432），又要连**只读数据源 `gov_metrics`**，因此必须补 PG 驱动。
> **依据**：Superset 官方文档做法（自定义镜像 `pip install psycopg2-binary`）；AGENTS §7「自建镜像固定 digest，不用 latest」。

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 基于 `apache/superset:6.1.0@sha256:16b50bbef664…`（**按 digest 固定**）安装 `psycopg2-binary==2.9.13`（**钉版本**） |

**构建与使用**：

```bash
# 服务器侧
docker build -t ai-governance/superset:6.1.0-pg /data/ai-governance/superset-image/
docker inspect --format='{{index .RepoDigests 0}}' ai-governance/superset:6.1.0-pg   # 记录 digest
```

> ⚠️ **两个实测踩坑（2026-09-19）**：
> 1. **本镜像是手工 `docker build`，compose 里没有 `build` 段** —— 用 `docker compose build superset` 会返回
>    `No services to build`（**no-op**，看起来成功其实没重建）；改完 Dockerfile 必须用上面的 `docker build`。
> 2. **基础 tag 与依赖版本都要钉**：`apache/superset:6.1.0` 是浮动 tag、`psycopg2-binary` 原先未钉版本 ⇒
>    重建可能**静默换基础/依赖**（同类问题已在 GA/门户镜像实测踩到）。已分别钉为
>    `@sha256:16b50bbef664…` 与 `==2.9.13`。

**重建的实测结论（2026-09-19）**：**重建不降漏洞**——重建前 100（HIGH 100 / CRIT 0）→ 重建后 **100**，逐目标一致
（debian 67 + python-pkg 33）。因为 `docker pull apache/superset:6.1.0` 返回 **"Image is up to date"**（基础已最新）。
**构成**：64 条来自 `linux-libc-dev`（内核**头文件**包，容器不加载内核）、33 条 Python 依赖（pillow 13 / pyasn1 4 / cryptography 3 …）、3 条 `libpcre2-8-0`。
**要真正降只有两条路**（均需人工批准）：① 升级 Superset 版本；② 在派生镜像里显式升级个别 Python 依赖（须重跑验收）。
**重建后验收复跑已通过**（容器 healthy、`/health` 200、`superset_ro → gov_metrics` 可读、反向连 OM 库被拒、API 登录取到看板）。

**纪律**：基础 tag 变更（如升到 6.2.x）属组件版本变更，须同步《…S2-6Superset与指标看板.md》版本记录并重跑验收（**Superset 全部查询落在 gov_metrics**）。
