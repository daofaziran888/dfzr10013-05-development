"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 核算批次、日期切片、报告附件等派生数据放在这里。
        # 它们不参与概览页的“业务模块”统计，避免把中间结果当成业务对象。
        self._datasets: dict[str, list[dict[str, Any]]] = {}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def dataset_rows(self, name: str) -> list[dict[str, Any]]:
        """派生数据集：核算批次、人效明细、报告附件等。"""
        return self._datasets.setdefault(name, [])

    def replace_dataset(self, name: str, rows: list[dict[str, Any]]) -> None:
        """整体替换一份派生数据集；写入时深拷贝，调用方之后改原对象不会影响已落库内容。"""
        self._datasets[name] = deepcopy(rows)

    def append_dataset(self, name: str, row: dict[str, Any]) -> dict[str, Any]:
        """追加一条不可变派生记录，返回落库副本。"""
        stored = deepcopy(row)
        self.dataset_rows(name).append(stored)
        return stored

    def reset_datasets(self) -> None:
        """清空派生数据；原始业务表和台账不动。"""
        self._datasets.clear()

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
