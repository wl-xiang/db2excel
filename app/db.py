#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oracle2excel 数据库连接模块。

负责:
1. 初始化 Thick 模式（加载 Docker 镜像内打包的 Oracle Instant Client 19）;
2. 通过 service_name 构造 DSN 并建立连接（带重试）;
3. 注册输出类型处理器，自动把 CLOB/NCLOB/BLOB 转为字符串，便于写入 Excel。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, List, Optional

import oracledb

from .config import AppConfig, OracleConfig

logger = logging.getLogger("oracle2excel.db")

_THICK_INITIALIZED = False


def init_thick_mode(cfg: OracleConfig) -> bool:
    """初始化 python-oracledb Thick 模式。

    读取环境变量 ORACLE_HOME（Dockerfile 中已指向 Instant Client 19 目录）。
    若驱动已是 Thick 模式则幂等跳过。

    :return: 是否成功启用 Thick 模式
    """
    global _THICK_INITIALIZED
    if _THICK_INITIALIZED or oracledb.is_thick_mode():
        _THICK_INITIALIZED = True
        return True

    lib_dir = os.environ.get("ORACLE_HOME", "/opt/oracle/instantclient_19_25")
    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
        _THICK_INITIALIZED = True
        client_ver = ".".join(str(v) for v in oracledb.clientversion())
        logger.info("Thick 模式初始化成功: lib_dir=%s, Instant Client 版本=%s", lib_dir, client_ver)
        return True
    except Exception as e:  # noqa: BLE001 - 需要把驱动初始化失败归入统一日志
        # DPI-1047: 已初始化 / 找不到库 等
        if "already initialized" in str(e).lower():
            _THICK_INITIALIZED = True
            return True
        logger.error("Thick 模式初始化失败（lib_dir=%s）: %s", lib_dir, e)
        raise


def build_dsn(cfg: OracleConfig) -> str:
    """用 service_name 构造 Oracle DSN（Oracle 11g 推荐方式）。"""
    return oracledb.makedsn(cfg.host, cfg.port, service_name=cfg.service_name)


def _output_type_handler(cursor: Any, metadata: Any) -> Any:
    """把 LOB 列直接取为字符串/字节，避免拿到 LOB locator 对象。"""
    if metadata.type_code in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if metadata.type_code == oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    return None


def connect(cfg: AppConfig) -> "oracledb.Connection":
    """建立 Oracle 连接（按配置重试），失败抛出最后一次异常。"""
    oc = cfg.oracle
    dsn = build_dsn(oc)

    if oc.driver_mode == "thick":
        init_thick_mode(oc)
    else:
        if not oracledb.is_thick_mode():
            logger.info("使用 Thin 模式（注意: Thin 仅支持 Oracle 12.1+，11g 必须用 thick）")

    last_err: Optional[Exception] = None
    for attempt in range(1, oc.connect_retry_attempts + 1):
        try:
            logger.info(
                "正在连接 Oracle (%s@%s:%s/%s, mode=%s) 第 %d/%d 次...",
                oc.user, oc.host, oc.port, oc.service_name, oc.driver_mode,
                attempt, oc.connect_retry_attempts,
            )
            conn = oracledb.connect(
                user=oc.user,
                password=oc.password,
                dsn=dsn,
                arraysize=oc.fetch_arraysize,
            )
            # 注册 LOB 输出类型处理器: CLOB/NCLOB/BLOB 直接取为 str/bytes
            conn.output_type_handler = _output_type_handler
            server_ver = conn.version
            logger.info("连接成功，Oracle 服务器版本: %s", server_ver)
            return conn
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("第 %d 次连接失败: %s", attempt, e)
            if attempt < oc.connect_retry_attempts:
                time.sleep(oc.connect_retry_delay_seconds)
    raise ConnectionError(f"Oracle 连接失败（已重试 {oc.connect_retry_attempts} 次）: {last_err}") from last_err


def list_user_tables(conn: "oracledb.Connection") -> List[str]:
    """查询当前用户下的全部表名（用于 export_all_user_tables）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        return [row[0] for row in cur.fetchall()]
