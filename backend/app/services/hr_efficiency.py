"""部门人效核算批次：生成日期切片、部门汇总与偏差数据集。

一次核算 = 一个不可变批次：
- 保存原始台账快照（fingerprint 用于判断依据是否相同）；
- 保存人员日期切片与部门汇总；
- 后续台账更正/补报不会改写旧批次；
- 重新核算生成新批次，并标记与旧批次的差异，旧批次仍然可以复算。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.services import hr_calc
from app.services import hr_source
from app.store import store

BATCHES = "hr_efficiency_batches"


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _next_batch_no() -> str:
    rows = store.dataset_rows(BATCHES)
    numbers = []
    for row in rows:
        text = str(row.get("批次号") or "")
        if text.startswith("EFF-"):
            try:
                numbers.append(int(text.split("-", 1)[1]))
            except ValueError:
                continue
    return f"EFF-{max(numbers, default=0) + 1:04d}"


def _find_batch(batch_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in store.dataset_rows(BATCHES) if str(row.get("批次号")) == batch_id),
        None,
    )


def _diff_departments(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    if previous is None:
        return []
    before = {item["部门"]: item for item in previous.get("departments", [])}
    after = {item["部门"]: item for item in current.get("departments", [])}
    diffs: list[dict[str, Any]] = []
    for department in sorted(set(before) | set(after)):
        old = before.get(department) or {}
        new = after.get(department) or {}
        fields = {
            "投入工时": (old.get("投入工时"), new.get("投入工时")),
            "产出数量": (old.get("产出数量"), new.get("产出数量")),
            "人效": (old.get("人效"), new.get("人效")),
            "人数": (old.get("人数"), new.get("人数")),
        }
        changed = {
            name: {"原值": values[0], "新值": values[1]}
            for name, values in fields.items() if values[0] != values[1]
        }
        if changed:
            diffs.append({"部门": department, "变化": changed})
    return diffs


def run(
    *,
    period_start: str,
    period_end: str,
    window_start: str | None = None,
    window_end: str | None = None,
    note: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """执行一次核算，返回批次；日期非法或数据不可用时返回可读原因。"""
    try:
        start = hr_calc.parse_day(period_start)
        end = hr_calc.parse_day(period_end)
        window_begin = hr_calc.parse_day(window_start) if window_start else start
        window_finish = hr_calc.parse_day(window_end) if window_end else end
    except ValueError as exc:
        return None, str(exc)
    if end < start:
        return None, "核算周期的结束日不能早于开始日"
    if window_finish < window_begin:
        return None, "切片窗口的结束日不能早于开始日"

    source = hr_source.snapshot()
    prepared = hr_calc.prepare(source, start, end)
    slices = hr_calc.build_slices(prepared, window_begin, window_finish, start, end)
    agg_start = max(window_begin, start)
    agg_end = min(window_finish, end)
    result = hr_calc.build_result(prepared, slices, start, end, agg_start, agg_end)

    batches = store.dataset_rows(BATCHES)
    previous = batches[-1] if batches else None
    batch_no = _next_batch_no()
    created = _now()
    batch = {
        "批次号": batch_no,
        "核算周期": result["period"],
        "切片窗口": {
            "start": max(window_begin, start).isoformat(),
            "end": min(window_finish, end).isoformat(),
        },
        "聚合区间": result["aggregation_range"],
        "区间一致": agg_start == start and agg_end == end,
        "状态": "已核算" if result["completeness"]["complete"] else "未完整",
        "完整性结论": result["completeness"]["conclusion"],
        "创建时间": created,
        "依据摘要": hr_source.fingerprint(source),
        "备注": note or "",
        "部门数": len(result["departments"]),
        "切片数": len(result["slices"]),
        "headcount": result["headcount"],
        "可形成完整评估": result["completeness"]["complete"],
        "前一批次": previous["批次号"] if previous else None,
        "相对前批次变化": _diff_departments(previous, result),
        "departments": result["departments"],
        "issues": result["issues"],
        "conflicts": result["conflicts"],
        "interval_intersections": result["interval_intersections"],
        "closure": result["closure"],
        "completeness": result["completeness"],
        "conversion": result["conversion"],
        "slices": result["slices"],
        "source_snapshot": source,
    }
    # 部门汇总到人员日期切片的引用路径：核查人效时可以顺着部门回到明细。
    for department_row in batch["departments"]:
        department_row["关联切片接口"] = (
            f"/api/hr/efficiency/batches/{batch_no}/slices"
            f"?department={department_row['部门']}"
        )
    store.append_dataset(BATCHES, batch)
    return deepcopy(batch), "核算完成，已生成部门人效偏差数据集"


SUMMARY_FIELDS = (
    "批次号", "核算周期", "聚合区间", "切片窗口", "状态", "完整性结论", "可形成完整评估",
    "创建时间", "依据摘要", "前一批次", "部门数", "切片数", "headcount", "区间一致",
)


def list_batches() -> list[dict[str, Any]]:
    """批次摘要：人员切片、部门汇总等重数据走详情接口按需读取。"""
    return [
        {field: deepcopy(row.get(field)) for field in SUMMARY_FIELDS if field in row}
        for row in store.dataset_rows(BATCHES)
    ]


def get_batch(batch_id: str) -> dict[str, Any] | None:
    row = _find_batch(batch_id)
    return deepcopy(row) if row else None


def latest_batch() -> dict[str, Any] | None:
    rows = store.dataset_rows(BATCHES)
    return deepcopy(rows[-1]) if rows else None


def deviation_dataset(batch_id: str | None = None) -> dict[str, Any] | None:
    """供报告等模块引用的部门人效偏差数据集（不含人员明细）。"""
    batch = get_batch(batch_id) if batch_id else latest_batch()
    if batch is None:
        return None
    return {
        "批次号": batch["批次号"],
        "核算周期": batch["核算周期"],
        "聚合区间": batch.get("聚合区间"),
        "状态": batch["状态"],
        "完整性结论": batch["完整性结论"],
        "可形成完整评估": batch["可形成完整评估"],
        "创建时间": batch["创建时间"],
        "依据摘要": batch["依据摘要"],
        "headcount": batch.get("headcount"),
        "departments": batch["departments"],
        "closure": batch["closure"],
        "interval_intersections": batch["interval_intersections"],
        "conflicts": batch["conflicts"],
        "industry": [
            {
                "部门": item["部门"],
                "行业均值": item["行业均值"],
                "单位": item["行业均值单位"],
                "适用周期": item["行业均值适用周期"],
                "来源": item["行业均值来源"],
                "口径": item["行业均值口径"],
                "匹配状态": item["行业均值匹配状态"],
            }
            for item in batch["departments"]
        ],
        "conversion": batch["conversion"],
    }


def source_snapshot(batch_id: str) -> dict[str, Any] | None:
    """批次引用的原始台账快照：已落库的人效数据不会丢失来源。"""
    batch = get_batch(batch_id)
    if batch is None:
        return None
    snapshot = batch.get("source_snapshot") or {}
    return {
        "批次号": batch["批次号"],
        "依据摘要": batch["依据摘要"],
        "创建时间": batch["创建时间"],
        "records": {
            "assignments": len(snapshot.get("assignments", [])),
            "timesheets": len(snapshot.get("timesheets", [])),
            "outputs": len(snapshot.get("outputs", [])),
            "industry": len(snapshot.get("industry_averages", [])),
        },
        "snapshot": snapshot,
    }


def attach_snapshot(batch_id: str) -> dict[str, Any] | None:
    """报告附件使用的批次快照：人员日期切片 + 部门汇总 + 行业口径。"""
    batch = get_batch(batch_id)
    if batch is None:
        return None
    return {
        "批次号": batch["批次号"],
        "核算周期": batch["核算周期"],
        "聚合区间": batch.get("聚合区间"),
        "切片窗口": batch["切片窗口"],
        "依据摘要": batch["依据摘要"],
        "状态": batch["状态"],
        "完整性结论": batch["完整性结论"],
        "人员日期切片": batch["slices"],
        "人数口径": batch.get("headcount"),
        "部门汇总": batch["departments"],
        "行业口径": [
            {
                "部门": item["部门"],
                "行业均值": item["行业均值"],
                "单位": item["行业均值单位"],
                "适用周期": item["行业均值适用周期"],
                "来源": item["行业均值来源"],
                "口径": item["行业均值口径"],
                "匹配状态": item["行业均值匹配状态"],
            }
            for item in batch["departments"]
        ],
        "投入折算口径": batch["conversion"],
        "投入闭合核对": batch["closure"],
        "区间交集": batch["interval_intersections"],
        "矛盾记录": batch["conflicts"],
    }


def reset() -> None:
    store.replace_dataset(BATCHES, [])
