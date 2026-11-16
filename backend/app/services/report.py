"""经营分析报告业务规则：状态流转、字段校验与筛选口径都收在这里。

报告生成时可以引用「部门人效偏差数据集」：把核算批次的人员日期切片、部门汇总和
行业口径固化成附件版本。附件一经生成就不再随台账变化，旧附件可以在组织调整后
用自身快照复算。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.services import hr_calc, hr_efficiency, hr_source
from app.store import store

MODULE = "report"
REQUIRED_FIELDS = ["报告编号", "报告主题", "分析周期"]
STATUS_ORDER = ["草稿", "已生成", "已分发", "已归档"]
ACTION_RULES = {"生成报告": "草稿", "分发报告": "已生成", "归档报告": "已分发"}
NEGATIVE_ACTIONS = []
ATTACHMENTS = "report_attachments"


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _attachments_for(report_id: int) -> list[dict[str, Any]]:
    return [
        row for row in store.dataset_rows(ATTACHMENTS)
        if int(row.get("报告ID", 0)) == report_id
    ]


class ReportService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("报告编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析报告 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于经营分析报告可执行范围"
        values = values or {}
        if action == "生成报告":
            attachment, message = self._generate(entry, values)
            if attachment is None:
                return None, message
            entry["数据附件"] = f"人效偏差附件 v{attachment['版本']} / {attachment['批次号']}"
            entry["附件版本"] = attachment["版本"]
            entry["引用批次"] = attachment["批次号"]
            return entry, message
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分析报告已{action}"

    # ------------------------------------------------------------------
    # 人效数据附件
    # ------------------------------------------------------------------
    def _generate(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        batch_id = str(values.get("hr_batch") or values.get("批次号") or "").strip() or None
        if batch_id:
            batch = hr_efficiency.get_batch(batch_id)
            if batch is None:
                return None, f"人效核算批次 {batch_id} 不存在，无法生成报告附件"
        else:
            batch = hr_efficiency.latest_batch()
            if batch is None:
                return None, "尚未生成部门人效偏差数据集，请先在人力效能评估中完成一次核算"
        if not batch.get("部门数"):
            return None, f"人效核算批次 {batch['批次号']} 没有部门汇总，不能作为报告依据"

        snapshot = hr_efficiency.attach_snapshot(batch["批次号"])
        if snapshot is None:
            return None, f"人效核算批次 {batch['批次号']} 快照不可用"
        versions = _attachments_for(int(entry["id"]))
        version = max((int(row.get("版本", 0)) for row in versions), default=0) + 1
        public_snapshot = {key: value for key, value in snapshot.items() if key != "依据快照"}
        attachment = {
            "id": f"ATT-{entry['id']}-{version}",
            "报告ID": int(entry["id"]),
            "版本": version,
            "批次号": batch["批次号"],
            "生成时间": _now(),
            "核算周期": batch["核算周期"],
            "聚合区间": batch.get("聚合区间"),
            "切片窗口": batch["切片窗口"],
            "依据摘要": batch["依据摘要"],
            "状态": batch["状态"],
            "完整性结论": batch["完整性结论"],
            "人数": (batch.get("headcount") or {}).get("覆盖员工数", 0),
            "部门数": len(batch["departments"]),
            "快照": public_snapshot,
            # 复算用的原始依据，只留在服务端，不随附件正文返回。
            "依据快照": deepcopy(batch.get("source_snapshot") or {}),
            "报告主题": entry.get("报告主题"),
            "分析周期": entry.get("分析周期"),
        }
        store.append_dataset(ATTACHMENTS, attachment)
        entry["关键发现"] = self._finding_text(batch)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        return attachment, (
            f"报告附件 v{version} 已生成：引用人效核算批次 {batch['批次号']}，"
            f"含 {len(batch['slices'])} 条人员日期切片、{len(batch['departments'])} 个部门汇总，"
            f"覆盖 {int((batch.get('headcount') or {}).get('覆盖员工数', 0))} 名员工"
        )

    @staticmethod
    def _finding_text(batch: dict[str, Any]) -> str:
        departments = batch["departments"]
        complete = [item for item in departments if item["评估完整性"] == "完整"]
        headcount = batch.get("headcount") or {}
        parts = [
            f"人效核算 {batch['批次号']}（{batch['核算周期']['start']}~{batch['核算周期']['end']}）："
            f"覆盖 {int(headcount.get('覆盖员工数', 0))} 名员工，折合投入 "
            f"{float(headcount.get('折合投入人数', 0)):g} 人；"
            f"{len(departments)} 个部门，完整评估 {len(complete)} 个；{batch['完整性结论']}"
        ]
        for item in departments:
            if item["人效"] is None:
                continue
            parts.append(
                f"{item['部门']} 人效 {item['人效']:g}{item['人效单位']}，{item['偏差结论']}"
                + (f"（行业均值 {item['行业均值']:g}）" if item["行业均值"] is not None else "")
            )
        return "；".join(parts)

    def list_attachments(self, entry_id: int) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in row.items() if key not in {"快照", "依据快照"}}
            for row in _attachments_for(entry_id)
        ]

    def get_attachment(self, entry_id: int, version: int) -> dict[str, Any] | None:
        for row in _attachments_for(entry_id):
            if int(row.get("版本", 0)) == version:
                return deepcopy(row)
        return None

    def public_attachment(self, entry_id: int, version: int) -> dict[str, Any] | None:
        row = self.get_attachment(entry_id, version)
        if row is None:
            return None
        return {
            "报告ID": row["报告ID"],
            "版本": row["版本"],
            "批次号": row["批次号"],
            "生成时间": row["生成时间"],
            "核算周期": row["核算周期"],
            "聚合区间": row.get("聚合区间"),
            "切片窗口": row["切片窗口"],
            "依据摘要": row["依据摘要"],
            "状态": row["状态"],
            "完整性结论": row["完整性结论"],
            "人数": row["人数"],
            "部门数": row["部门数"],
            **row["快照"],
        }

    def recompute_attachment(self, entry_id: int, version: int) -> tuple[dict[str, Any] | None, str]:
        """用附件自带的原始依据重新核算，验证旧附件在组织调整后仍可复算。"""
        row = self.get_attachment(entry_id, version)
        if row is None:
            return None, f"报告 {entry_id} 的附件 v{version} 不存在"
        source = row.get("依据快照") or {}
        if not source:
            return None, "附件缺少原始依据快照，无法复算"
        period = row.get("核算周期") or {}
        window = row.get("切片窗口") or {}
        try:
            start = hr_calc.parse_day(period.get("start"))
            end = hr_calc.parse_day(period.get("end"))
            window_start = hr_calc.parse_day(window.get("start") or period.get("start"))
            window_end = hr_calc.parse_day(window.get("end") or period.get("end"))
        except ValueError as exc:
            return None, f"附件周期无法解析：{exc}"
        prepared = hr_calc.prepare(source, start, end)
        slices = hr_calc.build_slices(prepared, window_start, window_end, start, end)
        result = hr_calc.build_result(
            prepared, slices, start, end,
            max(window_start, start), min(window_end, end),
        )

        stored = row["快照"]
        stored_departments = {
            item["部门"]: item for item in stored.get("部门汇总", [])
        }
        comparisons: list[dict[str, Any]] = []
        all_match = True
        for item in result["departments"]:
            old = stored_departments.get(item["部门"])
            match = bool(old) and (
                old.get("投入工时") == item["投入工时"]
                and old.get("产出数量") == item["产出数量"]
                and old.get("人效") == item["人效"]
                and old.get("人数") == item["人数"]
            )
            all_match = all_match and match
            comparisons.append({
                "部门": item["部门"],
                "复算投入工时": item["投入工时"],
                "附件投入工时": old.get("投入工时") if old else None,
                "复算产出数量": item["产出数量"],
                "附件产出数量": old.get("产出数量") if old else None,
                "复算人效": item["人效"],
                "附件人效": old.get("人效") if old else None,
                "一致": match,
            })
        slices_match = len(slices) == len(stored.get("人员日期切片", []))
        all_match = all_match and slices_match and set(stored_departments) == {
            item["部门"] for item in result["departments"]
        }
        return {
            "报告ID": row["报告ID"],
            "版本": row["版本"],
            "批次号": row["批次号"],
            "依据摘要": row["依据摘要"],
            "复算依据摘要": hr_source.fingerprint(source),
            "复算时间": _now(),
            "部门比对": comparisons,
            "切片数": {"附件": len(stored.get("人员日期切片", [])), "复算": len(slices)},
            "切片数一致": slices_match,
            "投入闭合": result["closure"],
            "一致": all_match,
            "结论": "旧附件可用自身快照复算，结果与生成时一致" if all_match else "复算结果与附件不一致，请核对附件快照",
        }, "附件已用自身快照复算"

    def reset(self) -> None:
        store.replace_dataset(ATTACHMENTS, [])
