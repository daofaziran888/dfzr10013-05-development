"""人力投入原始台账：员工任职、工时上报、部门产出与行业均值。

台账是可修改的（补报、更正任职），每次修改都会写入变更日志并带更新时间。
已完成的核算批次保存自己的数据快照，不会因为台账后续修改而被改写。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.seed_hr import seed_source
from app.store import store

ASSIGNMENTS = "hr_source_assignments"
TIMESHEETS = "hr_source_timesheets"
OUTPUTS = "hr_source_outputs"
INDUSTRY = "hr_source_industry"
EVENTS = "hr_source_events"


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _next_id(prefix: str, rows: list[dict[str, Any]]) -> str:
    numbers = []
    for row in rows:
        value = str(row.get("id") or "")
        if value.startswith(f"{prefix}-"):
            try:
                numbers.append(int(value.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{max(numbers, default=0) + 1}"


def _ensure_loaded() -> None:
    if store.dataset_rows(ASSIGNMENTS):
        return
    source = seed_source()
    assignments = store.dataset_rows(ASSIGNMENTS)
    for index, row in enumerate(source["assignments"], start=1):
        assignments.append({
            "id": f"A-{index}",
            **row,
            "更新时间": "2026-06-01T00:00:00",
            "变更次数": 0,
        })
    timesheets = store.dataset_rows(TIMESHEETS)
    for index, row in enumerate(source["timesheets"], start=1):
        timesheets.append({
            "id": f"T-{index}",
            **row,
            "补报": False,
        })
    outputs = store.dataset_rows(OUTPUTS)
    for index, row in enumerate(source["outputs"], start=1):
        outputs.append({
            "id": f"O-{index}",
            **row,
            "补报": False,
        })
    industry = store.dataset_rows(INDUSTRY)
    for index, row in enumerate(source["industry_averages"], start=1):
        industry.append({"id": f"I-{index}", **row})


def snapshot() -> dict[str, list[dict[str, Any]]]:
    """返回台账的只读副本，用于核算批次快照。"""
    _ensure_loaded()
    return {
        "assignments": deepcopy(store.dataset_rows(ASSIGNMENTS)),
        "timesheets": deepcopy(store.dataset_rows(TIMESHEETS)),
        "outputs": deepcopy(store.dataset_rows(OUTPUTS)),
        "industry_averages": deepcopy(store.dataset_rows(INDUSTRY)),
    }


def list_assignments() -> list[dict[str, Any]]:
    _ensure_loaded()
    return deepcopy(store.dataset_rows(ASSIGNMENTS))


def list_timesheets() -> list[dict[str, Any]]:
    _ensure_loaded()
    return deepcopy(store.dataset_rows(TIMESHEETS))


def list_outputs() -> list[dict[str, Any]]:
    _ensure_loaded()
    return deepcopy(store.dataset_rows(OUTPUTS))


def list_events() -> list[dict[str, Any]]:
    _ensure_loaded()
    return deepcopy(store.dataset_rows(EVENTS))


def update_assignment(assignment_id: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """更正任职记录。只改台账，不动已生成核算批次；下一次核算才使用新值。"""
    _ensure_loaded()
    rows = store.dataset_rows(ASSIGNMENTS)
    target = next((row for row in rows if str(row.get("id")) == assignment_id), None)
    if target is None:
        return None, f"任职记录 {assignment_id} 不存在"
    editable = ["部门", "任职开始", "任职结束", "投入比例", "姓名"]
    changed: dict[str, Any] = {}
    for field in editable:
        if field not in values:
            continue
        new_value = values[field]
        if field == "投入比例":
            try:
                new_value = float(new_value)
            except (TypeError, ValueError):
                return None, "投入比例必须是 0 到 1 之间的数字"
            if not 0 < new_value <= 1:
                return None, "投入比例必须在 0 到 1 之间（不含 0）"
        if field == "任职结束" and new_value in ("", None):
            new_value = None
        if target.get(field) != new_value:
            changed[field] = {"原值": target.get(field), "新值": new_value}
    if not changed:
        return target, "任职记录未发生变化"
    target.update({key: value["新值"] for key, value in changed.items()})
    target["更新时间"] = _now()
    target["变更次数"] = int(target.get("变更次数") or 0) + 1
    store.append_dataset(EVENTS, {
        "id": _next_id("EV", store.dataset_rows(EVENTS)),
        "类型": "更正任职",
        "对象": assignment_id,
        "员工号": target.get("员工号"),
        "发生时间": target["更新时间"],
        "变更": changed,
        "说明": "任职记录已更正；已完成的核算批次保留原快照，重新核算后生效",
    })
    return deepcopy(target), "任职记录已更正，下一次核算使用新值"


def add_timesheet(values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """补报工时。同一员工同一天重复补报会被拒绝，避免继续核算时重复累计。"""
    _ensure_loaded()
    employee = str(values.get("员工号") or "").strip()
    day = str(values.get("日期") or "").strip()
    if not employee or not day:
        return None, "补报工时必须提供员工号与日期"
    rows = store.dataset_rows(TIMESHEETS)
    if any(str(row.get("员工号")) == employee and str(row.get("日期")).startswith(day) for row in rows):
        return None, f"{employee} 在 {day} 已有工时记录，请使用更正而不是重复补报"
    try:
        available = float(values.get("可用工时", 8.0))
        actual = float(values.get("实际上报工时"))
    except (TypeError, ValueError):
        return None, "可用工时与实际上报工时必须是数字"
    if available <= 0 or actual < 0:
        return None, "可用工时应大于 0，实际上报工时应不小于 0"
    row = {
        "id": _next_id("T", rows),
        "员工号": employee,
        "日期": day,
        "可用工时": available,
        "实际上报工时": actual,
        "上报时间": _now(),
        "补报": True,
    }
    store.append_dataset(TIMESHEETS, row)
    store.append_dataset(EVENTS, {
        "id": _next_id("EV", store.dataset_rows(EVENTS)),
        "类型": "补报工时",
        "对象": row["id"],
        "员工号": employee,
        "发生时间": row["上报时间"],
        "变更": {"日期": day, "实际上报工时": actual},
        "说明": "补报工时可能改变原区间归属，重新核算保留原依据",
    })
    return deepcopy(row), "工时已补报，重新核算后计入"


def add_output(values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """补报部门产出；同部门同一天已有记录时拒绝重复补报。"""
    _ensure_loaded()
    department = str(values.get("部门") or "").strip()
    day = str(values.get("日期") or "").strip()
    unit = str(values.get("单位") or "").strip()
    if not department or not day or not unit:
        return None, "补报产出必须提供部门、日期与单位"
    try:
        quantity = float(values.get("数量"))
    except (TypeError, ValueError):
        return None, "产出数量必须是数字"
    if quantity < 0:
        return None, "产出数量不能为负"
    rows = store.dataset_rows(OUTPUTS)
    if any(
        str(row.get("部门")) == department
        and str(row.get("日期")).startswith(day)
        and str(row.get("单位")).strip() == unit
        for row in rows
    ):
        return None, f"{department} 在 {day} 已有相同单位的产出记录，请勿重复补报"
    row = {
        "id": _next_id("O", rows),
        "部门": department,
        "日期": day,
        "数量": quantity,
        "单位": unit,
        "上报时间": _now(),
        "上报类型": "补报",
        "补报": True,
    }
    store.append_dataset(OUTPUTS, row)
    store.append_dataset(EVENTS, {
        "id": _next_id("EV", store.dataset_rows(EVENTS)),
        "类型": "补报产出",
        "对象": row["id"],
        "员工号": None,
        "发生时间": row["上报时间"],
        "变更": {"部门": department, "日期": day, "数量": quantity, "单位": unit},
        "说明": "补报产出只影响之后的核算批次",
    })
    return deepcopy(row), "部门产出已补报"


def reset() -> None:
    """测试辅助：清空台账，下次访问重新装载示例数据。"""
    for name in (ASSIGNMENTS, TIMESHEETS, OUTPUTS, INDUSTRY, EVENTS):
        store.replace_dataset(name, [])


def fingerprint(source: dict[str, list[dict[str, Any]]]) -> str:
    """给台账快照一个稳定的短摘要，用于判断两次核算依据是否相同。"""
    import hashlib
    import json

    payload = json.dumps(
        {
            "assignments": sorted(source["assignments"], key=lambda row: str(row.get("id"))),
            "timesheets": sorted(source["timesheets"], key=lambda row: str(row.get("id"))),
            "outputs": sorted(source["outputs"], key=lambda row: str(row.get("id"))),
            "industry": sorted(source["industry_averages"], key=lambda row: str(row.get("id"))),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
