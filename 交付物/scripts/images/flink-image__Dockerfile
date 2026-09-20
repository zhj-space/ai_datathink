# syntax=docker/dockerfile:1
#
# 依据：AGENTS §7（自建镜像固定基础 tag/digest，不用 latest；构建后记录 digest）
#       《部署步骤_单机版》S3-3（CDC JAR 入 /opt/flink/lib、固定 digest、上线前扫描）
#
# 版本矩阵（2026-09-18 本环境实测可用；连接器后缀 -1.20 表示对应 Flink 1.20 线）：
#   Flink                 1.20.1 (scala_2.12 / java17)  ← 基础镜像按 digest 固定（见下方 ARG）
#   Flink CDC             3.6.0-1.20   （MySQL / PostgreSQL SQL 连接器，shaded fat jar）
#   flink-connector-kafka 3.4.0-1.20   + flink-connector-base 1.20.1（Kafka 连接器是瘦包，需 base 依赖）
#   kafka-clients         3.4.0        （Kafka 连接器运行依赖；**2026-09-18 冒烟实测发现缺失**：
#                                      ClassNotFoundException org.apache.kafka.clients.consumer.OffsetResetStrategy，
#                                      版本取自 flink-connector-kafka-parent 3.4.0-1.20 的 kafka.version 属性）
#
# 边界声明：
#   * Oracle CDC **未纳入**：需商业插件与授权评估（设计方案 3.3），接入前另行确认。
#   * OpenLineage 上报 JAR **未纳入**：属 S3-5 作业开发范围，届时按官方兼容矩阵选定版本再加入。
#   * 单机形态不存在跨节点恢复问题，**不得宣称 Flink HA**（部署步骤 S3-3）。

ARG FLINK_BASE_IMAGE=flink@sha256:d51a693da76581f2483d3f83d5997d3226b076a9f990a7f7d1eed144434b8e1e
FROM ${FLINK_BASE_IMAGE}

ARG CDC_VERSION=3.6.0-1.20
ARG KAFKA_CONNECTOR_VERSION=3.4.0-1.20
ARG BASE_CONNECTOR_VERSION=1.20.1
ARG KAFKA_CLIENTS_VERSION=3.4.0
ARG MAVEN_REPO=https://repo1.maven.org/maven2/org/apache/flink
ARG MAVEN_KAFKA=https://repo1.maven.org/maven2/org/apache/kafka

USER root
COPY jars.sha256 /tmp/jars.sha256

RUN set -eux; \
    cd /tmp; \
    curl -fsSL -O "${MAVEN_REPO}/flink-sql-connector-mysql-cdc/${CDC_VERSION}/flink-sql-connector-mysql-cdc-${CDC_VERSION}.jar"; \
    curl -fsSL -O "${MAVEN_REPO}/flink-sql-connector-postgres-cdc/${CDC_VERSION}/flink-sql-connector-postgres-cdc-${CDC_VERSION}.jar"; \
    curl -fsSL -O "${MAVEN_REPO}/flink-connector-kafka/${KAFKA_CONNECTOR_VERSION}/flink-connector-kafka-${KAFKA_CONNECTOR_VERSION}.jar"; \
    curl -fsSL -O "${MAVEN_REPO}/flink-connector-base/${BASE_CONNECTOR_VERSION}/flink-connector-base-${BASE_CONNECTOR_VERSION}.jar"; \
    curl -fsSL -O "${MAVEN_KAFKA}/kafka-clients/${KAFKA_CLIENTS_VERSION}/kafka-clients-${KAFKA_CLIENTS_VERSION}.jar"; \
    sha256sum -c /tmp/jars.sha256; \
    mv /tmp/*.jar /opt/flink/lib/; \
    chown flink:flink /opt/flink/lib/*.jar; \
    ls -l /opt/flink/lib/

USER flink
