# -*- coding: utf-8 -*-
"""
数据记录器

记录仿真过程中每一步的时间戳和信号值，支持 CSV 导出和 pandas 分析。
"""
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DataRecorder:
    """
    仿真数据记录器

    用法:
        recorder = DataRecorder()
        recorder.record(0.0, {"pressure": 16.7, "valve": 50.0})
        recorder.record(0.2, {"pressure": 16.8, "valve": 51.0})
        recorder.to_csv("data/run_001.csv")
    """

    def __init__(self, max_rows: int = 0):
        """
        Args:
            max_rows: 最大记录行数，0 = 不限制
        """
        self.max_rows = max_rows
        self._timestamps: List[float] = []
        self._data: List[Dict[str, float]] = []
        self._columns: List[str] = []        # 有序列名列表
        self._columns_set: set = set()       # 快速查重

    @property
    def count(self) -> int:
        """已记录行数"""
        return len(self._timestamps)

    @property
    def columns(self) -> List[str]:
        """所有信号列名（按首次出现顺序）"""
        return list(self._columns)

    def record(self, timestamp: float, data: Dict[str, float]):
        """
        记录一行数据

        Args:
            timestamp: 仿真时间, 秒
            data: {信号名: 值}
        """
        self._timestamps.append(timestamp)
        self._data.append(data)

        # 维护有序列名
        for key in data:
            if key not in self._columns_set:
                self._columns.append(key)
                self._columns_set.add(key)

        # 超出限制时丢弃最旧数据
        if self.max_rows > 0 and len(self._timestamps) > self.max_rows:
            self._timestamps.pop(0)
            self._data.pop(0)

    def to_csv(self, filepath: str):
        """
        导出为 CSV 文件

        Args:
            filepath: 输出文件路径
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            # 表头
            header = ["time_s"] + self._columns
            writer.writerow(header)
            # 数据
            for t, row in zip(self._timestamps, self._data):
                line = [f"{t:.3f}"]
                for col in self._columns:
                    val = row.get(col)
                    line.append(f"{val:.6f}" if val is not None else "")
                writer.writerow(line)

        logger.info(f"数据已导出: {filepath} ({self.count} 行, {len(self._columns)} 列)")

    def to_dataframe(self):
        """
        转为 pandas DataFrame

        Returns:
            DataFrame，index 为仿真时间
        """
        import pandas as pd
        records = []
        for t, row in zip(self._timestamps, self._data):
            records.append({"time_s": t, **row})
        df = pd.DataFrame(records)
        if not df.empty:
            df = df.set_index("time_s")
        return df

    def get_series(self, name: str) -> tuple:
        """
        获取单个信号的时间序列

        Args:
            name: 信号名
        Returns:
            (时间列表, 值列表)
        """
        times = []
        values = []
        for t, row in zip(self._timestamps, self._data):
            if name in row:
                times.append(t)
                values.append(row[name])
        return times, values

    def get_range(self, start: int, end: Optional[int] = None) -> tuple:
        """
        获取指定范围的数据

        Args:
            start: 起始索引
            end: 结束索引（不含），None = 到末尾
        Returns:
            (timestamps, data_rows, columns)
        """
        ts = self._timestamps[start:end]
        rows = self._data[start:end]
        return ts, rows, list(self._columns)

    def get_latest(self) -> Optional[Dict[str, float]]:
        """获取最新一行数据（含时间戳）"""
        if not self._data:
            return None
        return {"time_s": self._timestamps[-1], **self._data[-1]}

    def clear(self):
        """清空所有记录"""
        self._timestamps.clear()
        self._data.clear()
        self._columns.clear()
        self._columns_set.clear()

    def summary(self) -> str:
        """返回数据摘要文本"""
        if not self._data:
            return "无数据"
        duration = self._timestamps[-1] - self._timestamps[0]
        lines = [f"记录: {self.count} 行, 时长: {duration:.1f}s, 信号: {len(self._columns)} 个"]
        # 各信号的最新值
        latest = self._data[-1]
        for col in self._columns:
            val = latest.get(col)
            if val is not None:
                lines.append(f"  {col}: {val:.4f}")
        return "\n".join(lines)
