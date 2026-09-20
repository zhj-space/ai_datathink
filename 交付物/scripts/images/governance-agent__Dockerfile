# syntax=docker/dockerfile:1
# Governance Agent（自研，唯一自研组件）：python:3.12-slim + 固定版本依赖
# 依据：AGENTS §7（自建镜像固定基础 tag、构建后记录 digest、依赖版本固定）
#       《…S3-6治理Agent接口规格.md》§6
# 构建加速：阿里云内网 PyPI + --trusted-host（公网源在本环境构建容器内实测仅 ~100 kB/s）
# 基础镜像**按 digest 固定**（2026-09-19）：浮动 tag 会在重建时静默换基础版本——本环境实测踩到
# （debian 13.6 → 13.7，HIGH/CRIT 由 10/3 变 0/0，见《…S3-5血缘上报D1记录.md》§4.4）。
# ⚠️ 注意别钉错：`78387bc3881b…` 是**旧**基础（debian 13.6）；下面这个 `23b5dc88c7dd…` 才是 debian 13.7 的那个。
# 换基础 = 组件版本变更，必须显式记录（AGENTS §7）。
FROM python:3.12-slim@sha256:23b5dc88c7dd47fec3f960b51dc30d19df9875cfbfc60f3b62d3e5b88cbccf62

ARG PIP_INDEX_URL=http://mirrors.cloud.aliyuncs.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.cloud.aliyuncs.com

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip config set global.index-url "${PIP_INDEX_URL}" \
 && pip config set global.trusted-host "${PIP_TRUSTED_HOST}"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py rag.py lineage.py ./

# 非 root 运行
RUN useradd --system --no-create-home --shell /usr/sbin/nologin ga \
 && chown -R ga:ga /app
USER ga

EXPOSE 8080
CMD ["python", "agent.py"]
