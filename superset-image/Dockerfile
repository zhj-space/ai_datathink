# Superset 派生镜像：官方 lean 镜像不含 PostgreSQL 驱动，按官方做法自行安装。
#
# 纪律（AGENTS §7）：自建镜像**固定基础 tag**（不用 latest）；构建后须记录镜像 digest 到
# 《AI数据治理平台_单机版_S2-6Superset与指标看板.md》。
#
# 基础镜像：apache/superset:6.1.0（实测该版本 lean 变体无 psycopg2/psycopg）
# 2026-09-19：按 digest 固定（浮动 tag 会在重建时静默换基础——同类问题在 GA/门户上已实测踩到）。
# 实测该 digest 与远端一致（`docker pull` 返回 "Image is up to date"），故此处固定属"把现状写死"，不是版本升级。
FROM apache/superset:6.1.0@sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88

USER root

# PostgreSQL 驱动：Superset 元数据库与只读数据源（gov_metrics）都需要。
# 注意：必须装进镜像自带的 venv（/app/.venv）——Superset 的 CLI 与 gunicorn 都跑在该 venv 里，
# 直接 `pip install` 会装到系统 Python，运行期仍报 ModuleNotFoundError: No module named 'psycopg2'
# （2026-09-17 实测踩到）。
# 且该 venv **不含 pip**，故使用镜像自带的 uv 安装（uv 位于 /usr/local/bin/uv）。
# 版本**必须钉住**（2026-09-19）：原先不钉版本 ⇒ 每次重建都可能静默换依赖版本
# （与浮动基础 tag 同一类可复现性问题）。2.9.13 = 现行镜像里实际安装的版本。
RUN uv pip install --python /app/.venv/bin/python --no-cache "psycopg2-binary==2.9.13"

USER superset
