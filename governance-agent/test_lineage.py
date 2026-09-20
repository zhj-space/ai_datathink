#!/usr/bin/env python3
"""作业图级血缘（D1）解析准确性测试 —— ADR-A7「血缘定准确率验收」的可复跑用例。

用法：python3 test_lineage.py            # 只跑解析/映射（不需要 OM、不写任何数据）

纪律：
  * 样例里 **必须有一个负例**（D1 认识不了的结构）——证明"认识不了就报错、不猜"这条纪律真的生效；
  * 断言写死期望边，改解析器就必须同步改期望（否则测试失去意义）。
"""

from __future__ import annotations

import sys

import lineage

MAPPING = {"table_service": "probe_pg", "topic_service": "probe_kafka", "default_schema": "public"}

# 样例 1：S3-5 骨架同构（datagen 造流 → Kafka sink）
SQL_DATAGEN_KAFKA = """
CREATE TABLE datagen_orders (
  order_id BIGINT,
  amount   DECIMAL(10,2),
  ts       TIMESTAMP(3),
  WATERMARK FOR ts AS ts - INTERVAL '5' SECOND
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '50'
);

CREATE TABLE alerts_out (
  window_start TIMESTAMP(3),
  cnt          BIGINT
) WITH (
  'connector' = 'kafka',
  'topic' = 'governance.alerts',
  'properties.bootstrap.servers' = 'kafka:9092',
  'format' = 'json'
);

INSERT INTO alerts_out
SELECT window_start, COUNT(*) FROM TABLE(TUMBLE(TABLE datagen_orders, DESCRIPTOR(ts), INTERVAL '5' SECOND))
GROUP BY window_start;
"""

# 样例 2：接源后的真实形态（PostgreSQL CDC → Kafka）
SQL_CDC_KAFKA = """
CREATE TABLE src_orders (
  order_id BIGINT,
  customer_id BIGINT,
  amount DECIMAL(10,2)
) WITH (
  'connector' = 'postgres-cdc',
  'hostname' = 'pg-source',
  'port' = '5432',
  'database-name' = 'app_db',
  'schema-name' = 'public',
  'table-name' = 'orders',
  'username' = 'cdc_reader',
  'password' = '***'
);

CREATE TABLE orders_alerts (
  order_id BIGINT,
  reason   STRING
) WITH (
  'connector' = 'kafka',
  'topic' = 'governance.alerts'
);

INSERT INTO orders_alerts SELECT order_id, 'amount_out_of_range' FROM src_orders WHERE amount > 100000;
"""

# 样例 3：CDC → JDBC 落库（表→表边）
SQL_CDC_JDBC = """
CREATE TABLE src_orders (
  order_id BIGINT,
  amount DECIMAL(10,2)
) WITH (
  'connector' = 'postgres-cdc',
  'database-name' = 'app_db',
  'schema-name' = 'public',
  'table-name' = 'orders'
);

CREATE TABLE orders_agg (
  order_id BIGINT,
  amount DECIMAL(10,2)
) WITH (
  'connector' = 'jdbc',
  'url' = 'jdbc:postgresql://pgbouncer:6432/app_db',
  'table-name' = 'orders_agg'
);

INSERT INTO orders_agg SELECT order_id, amount FROM src_orders;
"""

# 样例 4（负例）：含子查询 —— D1 必须报错而不是猜
SQL_SUBQUERY = """
CREATE TABLE src_a (id BIGINT) WITH ('connector' = 'postgres-cdc', 'database-name' = 'd', 'table-name' = 'a');
CREATE TABLE sink_b (id BIGINT) WITH ('connector' = 'kafka', 'topic' = 't');
INSERT INTO sink_b SELECT id FROM (SELECT id FROM src_a);
"""


def expect_edges(name, sql, expected):
    plan = lineage.build_plan(sql, MAPPING)
    got = sorted((e["from"]["type"], e["from"]["fqn"], e["to"]["type"], e["to"]["fqn"]) for e in plan["edges"])
    ok = got == sorted(expected) and not plan["errors"]
    print("[%s] %s" % ("PASS" if ok else "FAIL", name))
    for g in got:
        print("      edge: %s %s  ->  %s %s" % g)
    if plan["errors"]:
        print("      errors:", plan["errors"])
    return ok


def expect_errors(name, sql, must_contain):
    plan = lineage.build_plan(sql, MAPPING)
    hit = any(must_contain in err for err in plan["errors"])
    ok = bool(plan["errors"]) and hit
    print("[%s] %s" % ("PASS" if ok else "FAIL", name))
    print("      errors:", plan["errors"][:3])
    return ok


def main() -> int:
    results = []
    # 样例 1：窗口 TVF 必须**能解析出源表**（否则会误杀我们自己的窗口聚合作业）；
    # 但 datagen 源**没有真实数据集**、映射不到 OM 实体 ⇒ 应报"不认识的 connector"且**不产出任何边**（宁缺勿错）。
    plan1 = lineage.build_plan(SQL_DATAGEN_KAFKA, MAPPING)
    ok1 = (bool(plan1["errors"]) and "datagen" in " ".join(plan1["errors"]) and not plan1["edges"])
    results.append(("负例：datagen 源应报错且不产边（不猜）", ok1))
    print("[%s] 负例：datagen 源应报错且不产边" % ("PASS" if ok1 else "FAIL"))
    print("      errors:", plan1["errors"][:2])
    print("      edges :", len(plan1["edges"]))

    results.append(("CDC → Kafka（表→主题）", expect_edges(
        "CDC → Kafka（表→主题）", SQL_CDC_KAFKA,
        [("table", "probe_pg.app_db.public.orders", "topic", "probe_kafka.governance.alerts")])))

    results.append(("CDC → JDBC（表→表）", expect_edges(
        "CDC → JDBC（表→表）", SQL_CDC_JDBC,
        [("table", "probe_pg.app_db.public.orders", "table", "probe_pg.app_db.public.orders_agg")])))

    results.append(("负例：子查询应报错", expect_errors("负例：子查询应报错", SQL_SUBQUERY, "子查询")))

    print()
    bad = [n for n, ok in results if not ok]
    print("结果：%d/%d 通过%s" % (len(results) - len(bad), len(results), ("  失败：" + "; ".join(bad)) if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
