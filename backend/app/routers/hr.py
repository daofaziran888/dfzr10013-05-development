"""人力效能评估接口：维护效能指标、人员台账，并执行人效核算。

静态路径（source/efficiency/export）必须注册在 ``/{entry_id}`` 之前，
否则 ``/api/hr/export`` 会被当成 entry_id=export 去做整数校验。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import hr_calc, hr_efficiency, hr_source
from app.services.hr import HrService

router = APIRouter(prefix="/api/hr", tags=["人力效能评估"])

service = HrService()

LIST_FIELDS = ["数据编号", "部门名称", "人效指标", "当前值", "行业均值", "偏差分析", "评估周期", "数据状态"]
STATUSES = ["正常", "低效", "高效", "已评估"]


# ---------------------------------------------------------------------------
# 人员投入台账
# ---------------------------------------------------------------------------

@router.get("/source/summary")
def source_summary() -> dict[str, Any]:
    """台账概览：任职、工时、产出与最近变更，用于确认核算依据是否齐全。"""
    assignments = hr_source.list_assignments()
    timesheets = hr_source.list_timesheets()
    outputs = hr_source.list_outputs()
    events = hr_source.list_events()
    return {
        "assignments": len(assignments),
        "timesheets": len(timesheets),
        "outputs": len(outputs),
        "employees": len({row.get("员工号") for row in assignments}),
        "departments": len({row.get("部门") for row in assignments}),
        "recent_events": events[-5:][::-1],
        "conversion": hr_calc.conversion_notes(),
    }


@router.get("/source/assignments", response_model=PageResult[dict])
def list_assignments(
    employee: str | None = Query(default=None, description="按员工号过滤"),
    department: str | None = Query(default=None, description="按部门过滤"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """员工任职区间：同一员工可以有多段、跨部门的任职。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    rows = hr_source.list_assignments()
    if employee:
        rows = [row for row in rows if employee in str(row.get("员工号", ""))]
    if department:
        rows = [row for row in rows if department in str(row.get("部门", ""))]
    total = len(rows)
    start = max(page - 1, 0) * size
    return PageResult(items=rows[start:start + size], total=total, page=page, size=size)


@router.put("/source/assignments/{assignment_id}", response_model=ActionResult)
def update_assignment(assignment_id: str, payload: EntryPayload) -> ActionResult:
    """更正任职记录。已落库的核算批次保留原快照，重新核算才使用新值。"""
    row, message = hr_source.update_assignment(assignment_id, payload.values)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/source/timesheets", response_model=PageResult[dict])
def list_timesheets(
    employee: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """员工工时明细；日期按闭区间过滤。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    rows = hr_source.list_timesheets()
    if employee:
        rows = [row for row in rows if employee in str(row.get("员工号", ""))]
    if start:
        rows = [row for row in rows if str(row.get("日期", "")) >= start]
    if end:
        rows = [row for row in rows if str(row.get("日期", "")) <= end]
    total = len(rows)
    offset = max(page - 1, 0) * size
    return PageResult(items=rows[offset:offset + size], total=total, page=page, size=size)


@router.post("/source/timesheets", response_model=ActionResult)
def add_timesheet(payload: EntryPayload) -> ActionResult:
    """补报工时；同员工同一天重复补报会被拒绝。"""
    row, message = hr_source.add_timesheet(payload.values)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/source/outputs", response_model=PageResult[dict])
def list_outputs(
    department: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """部门产出明细；数量为 0 的记录是真实零产出，与“没有记录”不同。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    rows = hr_source.list_outputs()
    if department:
        rows = [row for row in rows if department in str(row.get("部门", ""))]
    if start:
        rows = [row for row in rows if str(row.get("日期", "")) >= start]
    if end:
        rows = [row for row in rows if str(row.get("日期", "")) <= end]
    total = len(rows)
    offset = max(page - 1, 0) * size
    return PageResult(items=rows[offset:offset + size], total=total, page=page, size=size)


@router.post("/source/outputs", response_model=ActionResult)
def add_output(payload: EntryPayload) -> ActionResult:
    """补报部门产出；部门与日期边界必须和投入使用同一口径。"""
    row, message = hr_source.add_output(payload.values)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/source/events", response_model=PageResult[dict])
def list_events(page: int = 1, size: int = 50) -> PageResult[dict]:
    """台账变更日志：更正任职、补报工时与产出都留痕。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    rows = hr_source.list_events()[::-1]
    total = len(rows)
    offset = max(page - 1, 0) * size
    return PageResult(items=rows[offset:offset + size], total=total, page=page, size=size)


@router.get("/source/industry")
def list_industry() -> dict[str, Any]:
    """行业均值口径：适用周期、单位与计算口径必须随记录一起给出。"""
    rows = hr_source.snapshot()["industry_averages"]
    return {"total": len(rows), "items": rows}


# ---------------------------------------------------------------------------
# 人效核算
# ---------------------------------------------------------------------------

@router.post("/efficiency/runs", response_model=ActionResult)
def run_efficiency(payload: EntryPayload) -> ActionResult:
    """按周期执行一次核算，生成部门人效偏差数据集；数据未齐时明确说明缺口。"""
    values = payload.values
    period_start = str(values.get("period_start") or values.get("开始日期") or "").strip()
    period_end = str(values.get("period_end") or values.get("结束日期") or "").strip()
    if not period_start or not period_end:
        return ActionResult(ok=False, message="核算必须提供 period_start 与 period_end（YYYY-MM-DD）")
    batch, message = hr_efficiency.run(
        period_start=period_start,
        period_end=period_end,
        window_start=values.get("window_start"),
        window_end=values.get("window_end"),
        note=values.get("备注"),
    )
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=_batch_summary(batch))


def _batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in batch.items() if key not in {"slices", "source_snapshot"}}


@router.get("/efficiency/batches", response_model=PageResult[dict])
def list_batches(
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """核算批次列表；每次核算都保留一套独立依据。"""
    rows = hr_efficiency.list_batches()[::-1]
    total = len(rows)
    offset = max(page - 1, 0) * size
    return PageResult(items=rows[offset:offset + size], total=total, page=page, size=size)


@router.get("/efficiency/deviations")
def deviation_dataset(batch: str | None = Query(default=None, description="批次号；缺省取最近一次")) -> dict:
    """部门人效偏差数据集：供报告模块等跨模块引用人员投入与产出依据。"""
    dataset = hr_efficiency.deviation_dataset(batch)
    if dataset is None:
        raise HTTPException(status_code=404, detail="尚未完成任何人效核算，请先执行核算")
    return dataset


@router.get("/efficiency/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: str) -> dict:
    """批次详情（不含人员切片，切片走独立分页接口）。"""
    batch = hr_efficiency.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"核算批次 {batch_id} 不存在")
    return _batch_summary(batch)


@router.get("/efficiency/batches/{batch_id}/slices", response_model=PageResult[dict])
def list_slices(
    batch_id: str,
    employee: str | None = Query(default=None),
    department: str | None = Query(default=None),
    day: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = 1,
    size: int = 100,
) -> PageResult[dict]:
    """人员日期切片：每条切片都关联任职日期与投入折算依据。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    batch = hr_efficiency.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"核算批次 {batch_id} 不存在")
    rows = batch.get("slices", [])
    if employee:
        rows = [row for row in rows if employee in str(row.get("员工号", ""))]
    if department:
        rows = [row for row in rows if department in str(row.get("部门", ""))]
    if day:
        rows = [row for row in rows if str(row.get("日期", "")) == day]
    if status:
        rows = [row for row in rows if row.get("状态") == status]
    total = len(rows)
    offset = max(page - 1, 0) * size
    return PageResult(items=rows[offset:offset + size], total=total, page=page, size=size)


@router.get("/efficiency/batches/{batch_id}/source", response_model=dict)
def get_batch_source(batch_id: str) -> dict:
    """批次引用的原始台账快照：复核依据、追溯来源。"""
    payload = hr_efficiency.source_snapshot(batch_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"核算批次 {batch_id} 不存在")
    return payload


@router.get("/efficiency/batches/{batch_id}/export")
def export_batch(batch_id: str) -> dict[str, Any]:
    """导出批次明细：人员日期切片 + 部门汇总 + 口径说明，便于归档复算。"""
    batch = hr_efficiency.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"核算批次 {batch_id} 不存在")
    return {
        "module": "hr",
        "batch": _batch_summary(batch),
        "slices": batch.get("slices", []),
        "departments": batch.get("departments", []),
        "completeness": batch.get("completeness"),
        "closure": batch.get("closure"),
        "conflicts": batch.get("conflicts"),
        "interval_intersections": batch.get("interval_intersections"),
        "conversion": batch.get("conversion"),
    }


# ---------------------------------------------------------------------------
# 效能指标（原有列表）
# ---------------------------------------------------------------------------

@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出人力效能评估清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "hr", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按数据编号检索"),
    status: str | None = Query(default=None, description="正常、低效、高效、已评估"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按数据编号与状态过滤人力效能评估列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    """读取单条效能数据明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"效能数据 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条效能数据，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="效能数据已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条效能数据执行发布评估、标记低效、归档数据；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
