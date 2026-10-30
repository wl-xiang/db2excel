#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oracle2excel 服务入口。

用法（容器内由 ENTRYPOINT 调用）:
    python -m app.main [--config /app/config/oracle_conn.json]

运行模式:
    once - 完成一次全量导出后退出（默认）
    loop - 按配置的间隔周期性导出（可通过 SIGTERM/SIGINT 优雅退出）

退出码:
    0 成功 | 2 配置错误 | 3 数据库连接错误 | 4 导出失败
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time

from . import db
from .config import ConfigError, load_config
from .exporter import run_export
from .logger import log_level_from_env, setup_logging

_EXIT = threading.Event()
log = logging.getLogger("oracle2excel.main")


def _handle_signal(signum, _frame):  # pragma: no cover
    log.warning("收到信号 %s，准备优雅退出...", signum)
    _EXIT.set()


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="oracle2excel", description="Oracle -> Excel 导出服务")
    p.add_argument(
        "--config",
        default=None,
        help="oracle_conn.json 路径（默认读取环境变量 CONFIG_PATH，再退回 /app/config/oracle_conn.json）",
    )
    return p.parse_args(argv)


def resolve_config_path(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    return os.environ.get("CONFIG_PATH", "/app/config/oracle_conn.json")


def run_once(cfg) -> int:
    """执行一轮导出，返回退出码。"""
    conn = db.connect(cfg)
    try:
        user_tables = db.list_user_tables(conn) if cfg.export_all_user_tables else []
        jobs = cfg.build_jobs(user_tables)
        if not jobs:
            log.warning(
                "没有任何导出任务: 请在配置中填写 tables / queries，或设置 export_all_user_tables=true"
            )
            return 0
        results = run_export(conn, cfg, jobs, stop_check=_EXIT.is_set)
        if _EXIT.is_set():  # 被信号打断，视为优雅结束
            return 0
        return 0 if results else 4
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    setup_logging(log_level_from_env())

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle_signal)

    args = parse_args(argv)
    try:
        cfg = load_config(resolve_config_path(args.config))
    except ConfigError as e:
        log.error("配置错误: %s", e)
        return 2

    if cfg.run_mode == "once":
        try:
            return run_once(cfg)
        except ConnectionError as e:
            log.error("数据库连接失败: %s", e)
            return 3
        except Exception as e:  # noqa: BLE001
            log.error("导出失败: %s", e, exc_info=True)
            return 4

    # loop 模式
    log.info(
        "进入 loop 模式，每 %d 秒导出一轮（%d 个任务）", cfg.loop_interval_seconds,
        len(cfg.tables) + len(cfg.queries),
    )
    while not _EXIT.is_set():
        started = time.monotonic()
        try:
            rc = run_once(cfg)
            if rc != 0:
                log.warning("本轮导出未完全成功（退出码 %d），将继续下一轮", rc)
        except ConnectionError as e:
            log.error("本轮数据库连接失败: %s", e)
        except Exception as e:  # noqa: BLE001
            log.error("本轮导出失败: %s", e, exc_info=True)
        # 按“间隔 - 本轮耗时”等待，避免任务变慢导致周期漂移
        wait = max(1.0, cfg.loop_interval_seconds - (time.monotonic() - started))
        log.info("下一轮将在 %.0f 秒后开始", wait)
        if _EXIT.wait(wait):
            break
    log.info("loop 模式已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
