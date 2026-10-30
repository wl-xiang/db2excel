#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oracle2excel 配置加载与校验模块。

从 oracle_conn.json 读取上游 Oracle 连接信息与导出参数，
统一完成字段校验与默认值填充。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

# 表名白名单（防 SQL 注入：仅允许字母数字下划线 $ # . ，. 用于 schema.table）
_TABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)?$")

# Excel 单个 Worksheet 最大行数（1,048,576）
EXCEL_MAX_SHEET_ROWS = 1048576

VALID_DRIVER_MODES = ("thick", "thin")
VALID_RUN_MODES = ("once", "loop")


class ConfigError(Exception):
    """配置文件缺失、格式错误或字段非法。"""


@dataclass
class OracleConfig:
    """上游 Oracle 连接配置。"""

    user: str
    password: str
    host: str
    port: int = 1521
    service_name: str = ""
    # thick: 使用 Instant Client（Oracle 11g 必选，thin 仅支持 12.1+）
    # thin:  纯 Python 驱动，无需 Instant Client（仅 12.1+ 可用）
    driver_mode: str = "thick"
    connect_retry_attempts: int = 3
    connect_retry_delay_seconds: float = 5.0
    fetch_arraysize: int = 5000
    # Oracle 服务器字符集提示（thick 模式下 NLS_LANG 环境变量优先级更高）
    nls_lang: str = "SIMPLIFIED CHINESE_CHINA.AL32UTF8"

    def validate(self) -> None:
        if not self.user:
            raise ConfigError("oracle.user 不能为空")
        if self.password == "":
            raise ConfigError("oracle.password 不能为空（如确无密码请检查数据库配置）")
        if not self.host:
            raise ConfigError("oracle.host 不能为空")
        if not (1 <= int(self.port) <= 65535):
            raise ConfigError(f"oracle.port 非法: {self.port}，应在 1-65535 之间")
        if not self.service_name:
            raise ConfigError(
                "oracle.service_name 不能为空（Oracle 11g 请务必使用 service_name 而非 SID）"
            )
        if self.driver_mode not in VALID_DRIVER_MODES:
            raise ConfigError(
                f"oracle.driver_mode 非法: {self.driver_mode!r}，可选值: {VALID_DRIVER_MODES}"
            )
        if self.connect_retry_attempts < 1:
            self.connect_retry_attempts = 1
        if self.connect_retry_delay_seconds < 0:
            self.connect_retry_delay_seconds = 0.0
        if not (1 <= self.fetch_arraysize <= 100000):
            self.fetch_arraysize = 5000


@dataclass
class TableJob:
    """一个导出任务：一张表或一段自定义 SQL。"""

    name: str          # 输出文件名（不含 .xlsx）
    sql: str           # 实际执行的 SELECT 语句

    @property
    def filename(self) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', "_", self.name).strip() or "export"
        return f"{safe}.xlsx"


@dataclass
class ExcelConfig:
    max_rows_per_sheet: int = 1_000_000     # 单 Sheet 数据行上限（表头另占 1 行）
    date_format: str = "%Y-%m-%d %H:%M:%S"
    cell_max_chars: int = 32767             # Excel 单元格最大字符数
    blob_max_chars: int = 4096              # BLOB 转十六进制后保留的最大字符数

    def validate(self) -> None:
        if not (100 <= self.max_rows_per_sheet <= EXCEL_MAX_SHEET_ROWS - 1):
            raise ConfigError(
                f"excel.max_rows_per_sheet 应在 100 ~ {EXCEL_MAX_SHEET_ROWS - 1} 之间"
            )
        if self.cell_max_chars > 32767:
            self.cell_max_chars = 32767
        if self.cell_max_chars < 100:
            self.cell_max_chars = 100


@dataclass
class AppConfig:
    oracle: OracleConfig
    excel: ExcelConfig = field(default_factory=ExcelConfig)
    run_mode: str = "once"                    # once: 导完退出; loop: 周期导出
    loop_interval_seconds: int = 3600         # loop 模式间隔
    output_dir: str = "/app/output"
    batch_size: int = 5000                    # 每批 fetch 的行数
    log_every_rows: int = 10000               # 每 N 行打印一次进度
    tables: List[str] = field(default_factory=list)          # mode=tables 时的表清单
    queries: List[Dict[str, str]] = field(default_factory=list)  # mode=queries 时 [{name, sql}]
    export_all_user_tables: bool = False      # true: 自动导出当前用户全部表

    def validate(self) -> None:
        self.oracle.validate()
        self.excel.validate()
        if self.run_mode not in VALID_RUN_MODES:
            raise ConfigError(f"run_mode 非法: {self.run_mode!r}，可选值: {VALID_RUN_MODES}")
        if self.loop_interval_seconds < 1:
            self.loop_interval_seconds = 1
        if not (1 <= self.batch_size <= 100000):
            raise ConfigError(f"batch_size 应在 1 ~ 100000 之间，当前: {self.batch_size}")
        if self.log_every_rows < 1:
            self.log_every_rows = 1
        for t in self.tables:
            if not _TABLE_NAME_RE.match(t):
                raise ConfigError(f"表名非法（仅允许 schema.table 形式的字母数字）: {t!r}")
        for q in self.queries:
            if not q.get("name"):
                raise ConfigError("queries 中每一项都必须有 name 字段")
            if not (q.get("sql") or "").strip().lower().startswith("select"):
                raise ConfigError(
                    f"queries[{q.get('name')!r}].sql 必须以 SELECT 开头（本服务仅导出，不执行写操作）"
                )

    def build_jobs(self, user_tables: List[str]) -> List[TableJob]:
        """根据配置生成导出任务列表。user_tables 为数据库侧查到的当前用户全部表名。"""
        if self.export_all_user_tables:
            return [TableJob(name=t, sql=f'SELECT * FROM "{t}"') for t in user_tables]
        jobs: List[TableJob] = []
        for t in self.tables:
            # 表名已通过白名单校验，无注入风险；不加引号以保持大小写不敏感语义
            sql = f"SELECT * FROM {t}"
            jobs.append(TableJob(name=t.split(".")[-1], sql=sql))
        for q in self.queries:
            jobs.append(TableJob(name=q["name"], sql=q["sql"]))
        return jobs


def load_config(path: str) -> AppConfig:
    """加载并校验配置文件。

    :param path: oracle_conn.json 路径
    :raises ConfigError: 配置缺失或非法
    """
    if not os.path.isfile(path):
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置文件不是合法 JSON: {e}") from e

    oracle_raw = raw.get("oracle") or {}
    excel_raw = raw.get("excel") or {}

    oracle = OracleConfig(
        user=str(oracle_raw.get("user", "")),
        password=str(oracle_raw.get("password", "")),
        host=str(oracle_raw.get("host", "")),
        port=int(oracle_raw.get("port", 1521)),
        service_name=str(oracle_raw.get("service_name", "")),
        driver_mode=str(oracle_raw.get("driver_mode", "thick")).strip().lower(),
        connect_retry_attempts=int(oracle_raw.get("connect_retry_attempts", 3)),
        connect_retry_delay_seconds=float(oracle_raw.get("connect_retry_delay_seconds", 5.0)),
        fetch_arraysize=int(oracle_raw.get("fetch_arraysize", 5000)),
        nls_lang=str(oracle_raw.get("nls_lang", "SIMPLIFIED CHINESE_CHINA.AL32UTF8")),
    )
    excel = ExcelConfig(
        max_rows_per_sheet=int(excel_raw.get("max_rows_per_sheet", 1_000_000)),
        date_format=str(excel_raw.get("date_format", "%Y-%m-%d %H:%M:%S")),
        cell_max_chars=int(excel_raw.get("cell_max_chars", 32767)),
        blob_max_chars=int(excel_raw.get("blob_max_chars", 4096)),
    )
    cfg = AppConfig(
        oracle=oracle,
        excel=excel,
        run_mode=str(raw.get("run_mode", "once")).strip().lower(),
        loop_interval_seconds=int(raw.get("loop_interval_seconds", 3600)),
        output_dir=str(raw.get("output_dir", "/app/output")),
        batch_size=int(raw.get("batch_size", 5000)),
        log_every_rows=int(raw.get("log_every_rows", 10000)),
        tables=[str(t) for t in (raw.get("tables") or [])],
        queries=[dict(q) for q in (raw.get("queries") or [])],
        export_all_user_tables=bool(raw.get("export_all_user_tables", False)),
    )
    cfg.validate()
    return cfg
