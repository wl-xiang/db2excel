#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oracle2excel 日志配置: 统一输出到 stdout（Docker logs 可直接查看）。"""

from __future__ import annotations

import logging
import os
import sys


def setup_logging(level: str = "INFO") -> None:
    fmt = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    lvl = getattr(logging, level.strip().upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    root = logging.getLogger()
    root.setLevel(lvl)
    # 清掉可能存在的默认 handler，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    # openpyxl 等三方库日志降噪
    logging.getLogger("openpyxl").setLevel(logging.WARNING)


def log_level_from_env() -> str:
    return os.environ.get("LOG_LEVEL", "INFO")
