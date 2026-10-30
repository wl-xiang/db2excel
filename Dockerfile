# syntax=docker/dockerfile:1
# =============================================================================
# oracle2excel 镜像
# - 基础镜像: python:3.11-slim-bookworm (Debian 12)
# - 打包 Oracle Instant Client 19 (Basic)，供 python-oracledb Thick 模式连接 Oracle 11g
# - 系统依赖: libaio1（Instant Client 必需）
# - 环境变量: ORACLE_HOME / LD_LIBRARY_PATH / PATH / TNS_ADMIN / NLS_LANG
# =============================================================================
FROM python:3.11-slim-bookworm

ARG INSTANTCLIENT_ZIP=instantclient-basic-linux.x64-19.25.0.0.0dbru.zip
ARG INSTANTCLIENT_DIR=instantclient_19_25

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai

# ---------------------------------------------------------------------------
# 1. 系统库: libaio1 为 Instant Client 运行所必需; tzdata 支持 TZ=Asia/Shanghai
# ---------------------------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends libaio1 tzdata \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. 解压 Oracle Instant Client 19 到 /opt/oracle（用 Python zipfile，免装 unzip）
# ---------------------------------------------------------------------------
COPY docker/${INSTANTCLIENT_ZIP} /tmp/instantclient.zip
RUN mkdir -p /opt/oracle \
    && python -c "import zipfile; zipfile.ZipFile('/tmp/instantclient.zip').extractall('/opt/oracle')" \
    && rm -f /tmp/instantclient.zip \
    && test -d "/opt/oracle/${INSTANTCLIENT_DIR}" \
    && ln -s "/opt/oracle/${INSTANTCLIENT_DIR}/libclntsh.so.19.1" "/opt/oracle/${INSTANTCLIENT_DIR}/libclntsh.so" \
    && ln -s "/opt/oracle/${INSTANTCLIENT_DIR}/libocci.so.19.1" "/opt/oracle/${INSTANTCLIENT_DIR}/libocci.so"

# ---------------------------------------------------------------------------
# 3. Instant Client 相关环境变量（${INSTANTCLIENT_DIR} 在构建期由 ARG 替换）
# ---------------------------------------------------------------------------
ENV ORACLE_HOME=/opt/oracle/${INSTANTCLIENT_DIR} \
    LD_LIBRARY_PATH=/opt/oracle/${INSTANTCLIENT_DIR} \
    TNS_ADMIN=/app/config \
    NLS_LANG="SIMPLIFIED CHINESE_CHINA.AL32UTF8"
ENV PATH=/opt/oracle/${INSTANTCLIENT_DIR}:$PATH

# ---------------------------------------------------------------------------
# 4. Python 依赖
# ---------------------------------------------------------------------------
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---------------------------------------------------------------------------
# 5. 应用代码 + 非 root 运行用户
# ---------------------------------------------------------------------------
COPY app/ /app/app/
RUN groupadd -g 10001 appuser \
    && useradd -m -u 10001 -g appuser -s /usr/sbin/nologin appuser \
    && mkdir -p /app/output /app/config \
    && chown -R appuser:appuser /app

USER appuser

ENV CONFIG_PATH=/app/config/oracle_conn.json \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

VOLUME ["/app/config", "/app/output"]

ENTRYPOINT ["python", "-u", "-m", "app.main"]
