#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oracle2excel 导出核心模块。

将 Oracle 查询结果以流式方式写入 Excel（openpyxl write_only 模式，
内存中只保留当前批次，不会把整表加载进内存）。

特性:
- 自动从 cursor.description 生成表头;
- 超过单 Sheet 行上限时自动拆分 Sheet（DATA_1 / DATA_2 / ...）;
- 日期/时间统一格式化为字符串，避免 Excel 序列号问题;
- CLOB/BLOB 已在 db.py 的类型处理器中转为 str/bytes，此处做长度与转义兜底;
- 先写临时文件再原子 rename，避免中断留下半个 xlsx。
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any, Iterable, List, Optional, Sequence

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import AppConfig, TableJob

logger = logging.getLogger("oracle2excel.exporter")

_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(color="FFFFFF", bold=True)


def normalize_value(value: Any, date_fmt: str, cell_max: int, blob_max: int) -> Any:
    """把 Oracle 返回值转换为可安全写入 Excel 的值。

    - None        -> ""（空字符串）
    - datetime    -> 按 date_fmt 格式化为字符串
    - bytes       -> 十六进制字符串（截断到 blob_max）
    - str/数字    -> 原样（超长截断到 cell_max，Excel 单元格上限 32767）
    - 其他        -> str(value) 兜底
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # 必须在 int 之前判断（bool 是 int 子类）
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime, date)):
        return value.strftime(date_fmt)
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).hex()
        if len(text) > blob_max:
            text = text[:blob_max] + "...(truncated)"
        return text
    if isinstance(value, float):
        # NaN / Inf 在 openpyxl 中会写成非法值
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return value
    if isinstance(value, (int, str)):
        if isinstance(value, str) and len(value) > cell_max:
            return value[:cell_max] + "...(truncated)"
        return value
    # 兜底: Oracle 对象、ROWID 等极少出现的类型
    text = str(value)
    if len(text) > cell_max:
        text = text[:cell_max] + "...(truncated)"
    return text


class ExcelStreamWriter:
    """流式写出 Excel: 表头 + 多批次数据，自动分 Sheet，原子落盘。"""

    def __init__(self, cfg: AppConfig, job_name: str) -> None:
        self._cfg = cfg
        self._xcfg = cfg.excel
        self._job_name = job_name
        self._wb = Workbook(write_only=True)
        self._ws = None
        self._headers: List[str] = []
        self._sheet_idx = 0
        self._rows_in_sheet = 0
        self._total_rows = 0
        # 文件名清洗: 去掉路径分隔符等非法字符（与 TableJob.filename 保持一致）
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", job_name).strip() or "export"
        self._file_path = os.path.join(cfg.output_dir, safe_name + ".xlsx")
        self._tmp_path = self._file_path + ".tmp"
        self._started_at = time.monotonic()
        self._next_progress = cfg.log_every_rows

    # -- 内部 ---------------------------------------------------------------

    def _open_sheet(self, headers: Sequence[str]) -> None:
        self._sheet_idx += 1
        # Excel Sheet 名 ≤31 字符，且不能含 \ / ? * [ ] :
        base = f"DATA_{self._sheet_idx}"
        self._ws = self._wb.create_sheet(title=base)
        header_cells: List[WriteOnlyCell] = []
        for col_idx, h in enumerate(headers, start=1):
            c = WriteOnlyCell(self._ws, value=str(h))
            c.fill = _HEADER_FILL
            c.font = _HEADER_FONT
            c.alignment = Alignment(horizontal="center", vertical="center")
            header_cells.append(c)
            self._ws.column_dimensions[get_column_letter(col_idx)].width = min(
                max(12, len(str(h)) * 2), 60
            )
        self._ws.append(header_cells)
        self._rows_in_sheet = 0

    # -- 对外 ---------------------------------------------------------------

    def write_header(self, headers: Sequence[str]) -> None:
        if not headers:
            raise ValueError("查询结果没有任何列，无法导出")
        self._headers = list(headers)
        self._open_sheet(headers)

    def write_rows(self, rows: Iterable[Sequence[Any]]) -> None:
        """写入一批数据行（行内容为 cursor.description 对应的原始值序列）。"""
        if self._ws is None:
            raise RuntimeError("必须先调用 write_header()")
        max_rows = self._cfg.excel.max_rows_per_sheet
        nfmt = self._xcfg.date_format
        cmax = self._xcfg.cell_max_chars
        bmax = self._xcfg.blob_max_chars

        for row in rows:
            if self._rows_in_sheet >= max_rows:
                self._open_sheet(self._headers)  # 切分新 Sheet，复用表头
            values = [normalize_value(v, nfmt, cmax, bmax) for v in row]
            self._ws.append(values)
            self._rows_in_sheet += 1
            self._total_rows += 1
            if self._total_rows >= self._next_progress:
                logger.info("[%s] 已写入 %d 行...", self._job_name, self._total_rows)
                self._next_progress += self._cfg.log_every_rows

    def save(self) -> str:
        """保存文件（先临时文件再原子重命名），返回最终路径与统计信息。"""
        os.makedirs(self._cfg.output_dir, exist_ok=True)
        self._wb.save(self._tmp_path)
        os.replace(self._tmp_path, self._file_path)
        elapsed = time.monotonic() - self._started_at
        size = os.path.getsize(self._file_path)
        logger.info(
            "[%s] 导出完成: %s | 共 %d 行, %d 个 Sheet, 耗时 %.1fs, 文件大小 %.2f MB",
            self._job_name, self._file_path, self._total_rows,
            self._sheet_idx, elapsed, size / 1024 / 1024,
        )
        return self._file_path

    def abort(self) -> None:
        """清理半成品临时文件。"""
        try:
            if os.path.exists(self._tmp_path):
                os.remove(self._tmp_path)
        except OSError:
            pass

    @property
    def total_rows(self) -> int:
        return self._total_rows


def export_one(conn: Any, cfg: AppConfig, job: TableJob) -> str:
    """执行单个导出任务: job.sql -> output_dir/<job.name>.xlsx

    :raises Exception: 查询或写文件失败时抛出，由上层统一处理
    """
    writer = ExcelStreamWriter(cfg, job.name)
    try:
        cur = conn.cursor()
        try:
            cur.arraysize = cfg.oracle.fetch_arraysize
            cur.execute(job.sql)
            headers = [d[0] for d in cur.description]
            writer.write_header(headers)
            while True:
                rows = cur.fetchmany(cfg.batch_size)
                if not rows:
                    break
                writer.write_rows(rows)
        finally:
            cur.close()
        return writer.save()
    except BaseException:
        writer.abort()
        logger.error("[%s] 导出失败，已清理临时文件", job.name, exc_info=True)
        raise


def run_export(conn: Any, cfg: AppConfig, jobs: List[TableJob],
               stop_check: Optional[Any] = None) -> List[str]:
    """依次导出所有任务，返回成功生成的文件列表。

    单个任务失败不中断其他任务，最后汇总并抛出第一个异常。
    :param stop_check: 可选回调，返回 True 表示收到停止信号，提前结束
    """
    results: List[str] = []
    errors: List[str] = []
    for i, job in enumerate(jobs, start=1):
        if stop_check is not None and stop_check():
            logger.warning("收到停止信号，剩余 %d 个任务未执行", len(jobs) - i + 1)
            break
        logger.info("=== 任务 %d/%d: %s ===", i, len(jobs), job.name)
        try:
            results.append(export_one(conn, cfg, job))
        except Exception as e:  # noqa: BLE001 - 单任务失败继续下一任务
            errors.append(f"{job.name}: {e}")
    if errors:
        raise RuntimeError(f"{len(errors)} 个任务导出失败: " + "; ".join(errors))
    return results
