# 治理门户（自研 Streamlit 前端）
#
# 纪律（AGENTS §7）：基础镜像**固定 tag**；构建后记录镜像 digest 到
# 《AI数据治理平台_单机版_S2-7治理门户.md》。
# 基础镜像**按 digest 固定**（2026-09-19）：浮动 tag 会在重建时静默换基础版本（GA 实测踩到：
# debian 13.6 → 13.7，HIGH/CRIT 由 10/3 变 0/0）。
# ⚠️ 别钉错：`78387bc3881b…` 是**旧**基础（debian 13.6）；`23b5dc88c7dd…` 才是 debian 13.7。
# 换基础 = 组件版本变更，必须显式记录（AGENTS §7）。
FROM python:3.12-slim@sha256:23b5dc88c7dd47fec3f960b51dc30d19df9875cfbfc60f3b62d3e5b88cbccf62

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# pip 源参数化：默认官方源；国内网络可用 build arg 指向镜像（与 Docker registry 加速器同类环境适配）
ARG PIP_INDEX_URL=https://pypi.org/simple
# 若镜像为 HTTP（如阿里云内网源 http://mirrors.cloud.aliyuncs.com/pypi/simple/），需显式信任该主机
ARG PIP_TRUSTED_HOST=

COPY requirements.txt /app/requirements.txt
RUN if [ -n "${PIP_TRUSTED_HOST}" ]; then \
      pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
                  --trusted-host "${PIP_TRUSTED_HOST}" -r /app/requirements.txt; \
    else \
      pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r /app/requirements.txt; \
    fi

COPY . /app

# 非 root 运行
RUN useradd -m -u 10001 portal && chown -R portal:portal /app
USER portal

EXPOSE 8501
CMD ["streamlit", "run", "/app/app.py"]
