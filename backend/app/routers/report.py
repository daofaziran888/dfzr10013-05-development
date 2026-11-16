"""经营分析报告接口：维护分析报告，覆盖生成报告、分发报告、归档报告等动作。

生成报告时可以引用人力效能评估的「部门人效偏差数据集」，把人员日期切片、
部门汇总与行业口径固化成附件版本；附件保存原始依据快照，组织调整后仍可复算。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import hr_efficiency
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["经营分析报告"])

service = ReportService()

LIST_FIELDS = ["报告编号", "报告主题", "分析周期", "报告模板", "关键发现", "数据附件", "分发对象", "报告状态"]
STATUSES = ["草稿", "已生成", "已分发", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按报告编号检索"),
    status: str | None = Query(default=None, description="草稿、已生成、已分发、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按报告编号与状态过滤经营分析报告列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出经营分析报告清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}


@router.get("/hr-datasets", response_model=PageResult[dict])
def list_hr_datasets() -> PageResult[dict]:
    """可引用的人效核算批次：只列出已经生成部门汇总的批次。"""
    rows = [
        {
            "批次号": row["批次号"],
            "核算周期": row["核算周期"],
            "状态": row["状态"],
            "完整性结论": row["完整性结论"],
            "可形成完整评估": row["可形成完整评估"],
            "部门数": row["部门数"],
            "创建时间": row["创建时间"],
        }
        for row in hr_efficiency.list_batches()[::-1]
        if row.get("部门数")
    ]
    return PageResult(items=rows, total=len(rows), page=1, size=len(rows) or 1)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析报告明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析报告 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析报告，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析报告已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析报告执行生成报告、分发报告、归档报告。

    生成报告时可传 ``hr_batch`` 指定人效核算批次；缺省引用最近一次核算。
    没有可用批次或批次不完整时明确拒绝，不生成空附件。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/attachments", response_model=PageResult[dict])
def list_attachments(entry_id: int) -> PageResult[dict]:
    """报告附件版本列表（不含正文）。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"分析报告 {entry_id} 不存在或已归档")
    rows = service.list_attachments(entry_id)
    return PageResult(items=rows, total=len(rows), page=1, size=len(rows) or 1)


@router.get("/{entry_id}/attachments/{version}", response_model=dict)
def get_attachment(entry_id: int, version: int) -> dict:
    """读取某个附件版本：人员日期切片、部门汇总、行业口径与投入闭合核对。"""
    row = service.public_attachment(entry_id, version)
    if row is None:
        raise HTTPException(status_code=404, detail=f"报告 {entry_id} 的附件 v{version} 不存在")
    return row


@router.post("/{entry_id}/attachments/{version}/recompute", response_model=ActionResult)
def recompute_attachment(entry_id: int, version: int) -> ActionResult:
    """用附件自带的依据快照复算，验证组织调整后旧附件口径仍然可复现。"""
    result, message = service.recompute_attachment(entry_id, version)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=result["一致"], message=result["结论"], entry=result)
