"""人力投入核算引擎：把任职区间、工时与部门产出切成日期切片，再聚合成部门人效。

核算口径（所有日期边界都含首尾两天）：

1. 员工某天有 N 条在岗任职时，当天投入比例合计 R = Σ比例。
2. 当天报工 H 由这些部门按比例分摊：比例合计 R ≤ 1 时，部门分得 H × 比例，
   剩余 H × (1-R) 记为“未分配工时”；R > 1 时按 H × 比例 / R 归一化，
   多出来的 (R-1)/R 部分记为“隔离工时”，当天分配总量仍然等于 H，绝不放大。
3. 报工不超过可用工时；超出部分截断并单独记为矛盾记录。
4. 部门人效 = 周期内部门产出数量 ÷ 周期内分配工时；产出与投入使用同一日期边界。
5. 只有任职没有工时、只有投入没有产出、没有人员只有产出，分别给出不同状态，
   不能混成“人效为 0”。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

EPS = 1e-6
DEFAULT_AVAILABLE_HOURS = 8.0
MAX_ROUND = 4      # 对外展示精度
SLICE_ROUND = 6    # 切片明细精度，避免逐日舍入累计出闭合误差


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def parse_day(value: Any) -> date:
    """把 YYYY-MM-DD 解析成日期；非法值抛出可读异常。"""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        raise ValueError(f"日期「{text or '空'}」不是 YYYY-MM-DD 格式")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:  # pragma: no cover - 由调用方转成业务提示
        raise ValueError(f"日期「{text}」不是 YYYY-MM-DD 格式") from exc


def day_list(start: date, end: date) -> list[date]:
    if end < start:
        return []
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unit_key(value: Any) -> str:
    return str(value or "").replace(" ", "").strip()


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), MAX_ROUND)


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _id_key(value: Any) -> str:
    """记录 ID 既可能是整数也可能是 T-1 这种字符串，排序统一成字符串。"""
    return str(value or "")


# ---------------------------------------------------------------------------
# 原始台账校验与索引
# ---------------------------------------------------------------------------

def prepare(source: dict[str, list[dict[str, Any]]], period_start: date, period_end: date) -> dict[str, Any]:
    """校验周期内的任职、工时、产出和行业均值，形成核算用索引。

    无效记录进入 issues（阻断或警告），重复记录进入 conflicts，不再参与计算。
    """
    issues: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    assignments: list[dict[str, Any]] = []
    for raw in source.get("assignments", []):
        employee = str(raw.get("员工号") or "").strip()
        department = str(raw.get("部门") or "").strip()
        try:
            start = parse_day(raw.get("任职开始"))
            end = parse_day(raw.get("任职结束")) if raw.get("任职结束") else None
        except ValueError as exc:
            issues.append({
                "code": "任职记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"员工「{employee or '未知'}」的任职日期无法解析：{exc}",
            })
            continue
        ratio = _num(raw.get("投入比例"))
        if not employee or not department:
            issues.append({
                "code": "任职记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": "任职记录缺少员工号或部门",
            })
            continue
        if end is not None and end < start:
            issues.append({
                "code": "任职记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"{employee} 在{department}的任职结束日早于开始日",
            })
            continue
        if ratio is None or ratio <= 0 or ratio > 1:
            issues.append({
                "code": "任职记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"{employee} 在{department}的投入比例「{raw.get('投入比例')}」应在 0 到 1 之间",
            })
            continue
        # 只保留与周期有交集的任职片段
        if (end is not None and end < period_start) or start > period_end:
            continue
        assignments.append({
            "id": raw.get("id"), "员工号": employee, "姓名": str(raw.get("姓名") or ""),
            "部门": department, "start": start, "end": end, "ratio": ratio,
            "更新时间": raw.get("更新时间"),
        })
    assignments.sort(key=lambda item: (item["员工号"], item["start"], item["部门"]))

    # 工时：同一员工同一天只认一条，重复上报按上报时间取最新，其余隔离。
    ts_groups: dict[tuple[str, date], list[dict[str, Any]]] = defaultdict(list)
    for raw in source.get("timesheets", []):
        if str(raw.get("状态") or "") == "已更正":
            continue
        employee = str(raw.get("员工号") or "").strip()
        try:
            day = parse_day(raw.get("日期"))
        except ValueError:
            issues.append({
                "code": "工时记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"员工「{employee or '未知'}」的工时日期无法解析",
            })
            continue
        if not employee:
            issues.append({
                "code": "工时记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": "工时记录缺少员工号",
            })
            continue
        if day < period_start or day > period_end:
            continue
        available = _num(raw.get("可用工时"), DEFAULT_AVAILABLE_HOURS)
        actual = _num(raw.get("实际上报工时"))
        if available is None or available <= 0:
            issues.append({
                "code": "工时记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"{employee} 在{day.isoformat()}的可用工时应大于 0",
            })
            continue
        if actual is None or actual < 0:
            issues.append({
                "code": "工时记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"{employee} 在{day.isoformat()}的实际上报工时缺失或为负",
            })
            continue
        ts_groups[(employee, day)].append({
            "id": raw.get("id"), "员工号": employee, "日期": day,
            "可用工时": float(available), "实际上报工时": float(actual),
            "上报时间": str(raw.get("上报时间") or ""), "补报": bool(raw.get("补报")),
        })

    timesheets: dict[tuple[str, date], dict[str, Any]] = {}
    duplicate_timesheets = 0
    for (employee, day), rows in ts_groups.items():
        rows.sort(key=lambda item: (item["上报时间"], _id_key(item["id"])))
        chosen = rows[-1]
        timesheets[(employee, day)] = chosen
        if len(rows) > 1:
            duplicate_timesheets += len(rows) - 1
            conflicts.append({
                "类型": "工时重复上报", "员工号": employee, "日期": day.isoformat(),
                "涉及部门": sorted({str(a["部门"]) for a in assignments if a["员工号"] == employee and a["start"] <= day and (a["end"] is None or a["end"] >= day)}),
                "隔离工时": _round(sum(max(row["实际上报工时"], 0) for row in rows[:-1])),
                "说明": f"{employee} 在{day.isoformat()}有 {len(rows)} 条工时，保留上报时间最新的一条，其余隔离",
            })

    # 产出：同一部门同一天只认一条，重复按上报时间取最新；单位必须一致。
    out_groups: dict[tuple[str, date], list[dict[str, Any]]] = defaultdict(list)
    for raw in source.get("outputs", []):
        if str(raw.get("状态") or "") == "已更正":
            continue
        department = str(raw.get("部门") or "").strip()
        try:
            day = parse_day(raw.get("日期"))
        except ValueError:
            issues.append({
                "code": "产出记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"部门「{department or '未知'}」的产出日期无法解析",
            })
            continue
        quantity = _num(raw.get("数量"))
        unit = str(raw.get("单位") or "").strip()
        if not department or not unit:
            issues.append({
                "code": "产出记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": "产出记录缺少部门或单位",
            })
            continue
        if quantity is None or quantity < 0:
            issues.append({
                "code": "产出记录无效", "level": "阻断", "记录": raw.get("id"),
                "说明": f"{department} 在{day.isoformat()}的产出数量缺失或为负",
            })
            continue
        if day < period_start or day > period_end:
            continue
        out_groups[(department, day)].append({
            "id": raw.get("id"), "部门": department, "日期": day,
            "数量": float(quantity), "单位": unit,
            "上报时间": str(raw.get("上报时间") or ""), "上报类型": str(raw.get("上报类型") or "正常"),
        })

    outputs: dict[tuple[str, date], dict[str, Any]] = {}
    for (department, day), rows in out_groups.items():
        rows.sort(key=lambda item: (item["上报时间"], _id_key(item["id"])))
        chosen = rows[-1]
        outputs[(department, day)] = chosen
        if len(rows) > 1:
            kept_qty = chosen["数量"]
            dropped_qty = sum(row["数量"] for row in rows[:-1])
            conflicts.append({
                "类型": "产出重复上报", "部门": department, "日期": day.isoformat(),
                "涉及部门": [department], "隔离产出": _round(dropped_qty),
                "说明": (
                    f"{department} 在{day.isoformat()}有 {len(rows)} 条产出，"
                    f"保留上报时间最新的一条（{kept_qty:g}{chosen['单位']}），其余 {dropped_qty:g} 隔离"
                ),
            })

    units_by_department: dict[str, set[str]] = defaultdict(set)
    for (department, _day), row in outputs.items():
        units_by_department[department].add(_unit_key(row["单位"]))
    for department, units in units_by_department.items():
        if len(units) > 1:
            issues.append({
                "code": "产出单位不一致", "level": "阻断", "部门": department,
                "说明": f"{department} 周期内出现多种产出单位：{'、'.join(sorted(units))}，无法直接求和",
            })

    industry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in source.get("industry_averages", []):
        department = str(raw.get("部门") or "").strip()
        try:
            start = parse_day(raw.get("适用开始"))
            end = parse_day(raw.get("适用结束"))
        except ValueError:
            issues.append({
                "code": "行业均值无效", "level": "警告", "记录": raw.get("id"),
                "说明": f"部门「{department or '未知'}」的行业均值适用周期无法解析",
            })
            continue
        average = _num(raw.get("行业均值"))
        if not department or average is None:
            issues.append({
                "code": "行业均值无效", "level": "警告", "记录": raw.get("id"),
                "说明": "行业均值记录缺少部门或数值",
            })
            continue
        industry[department].append({
            "id": raw.get("id"), "部门": department, "行业均值": float(average),
            "单位": str(raw.get("单位") or ""), "start": start, "end": end,
            "口径": str(raw.get("口径") or ""), "来源": str(raw.get("来源") or ""),
        })

    if duplicate_timesheets:
        issues.append({
            "code": "工时重复上报", "level": "警告",
            "说明": f"共有 {duplicate_timesheets} 条重复工时记录被隔离，只保留上报时间最新的一条",
        })

    return {
        "assignments": assignments,
        "timesheets": timesheets,
        "outputs": outputs,
        "units_by_department": units_by_department,
        "industry": industry,
        "issues": issues,
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# 按日切片
# ---------------------------------------------------------------------------

def build_slices(
    prepared: dict[str, Any],
    window_start: date,
    window_end: date,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    """把窗口内每一天的任职与工时切成部门日期切片。"""
    assignments: list[dict[str, Any]] = prepared["assignments"]
    timesheets: dict[tuple[str, date], dict[str, Any]] = prepared["timesheets"]
    by_employee: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        by_employee[assignment["员工号"]].append(assignment)

    slices: list[dict[str, Any]] = []
    actual_start = max(window_start, period_start)
    actual_end = min(window_end, period_end)
    for day in day_list(actual_start, actual_end):
        for employee in sorted(by_employee):
            active = [
                item for item in by_employee[employee]
                if item["start"] <= day and (item["end"] is None or item["end"] >= day)
            ]
            if not active:
                continue
            ratio_sum = round(sum(item["ratio"] for item in active), 6)
            factor = 1.0 if ratio_sum <= 1 + EPS else 1.0 / ratio_sum
            ts = timesheets.get((employee, day))
            available = ts["可用工时"] if ts else None
            reported = float(ts["实际上报工时"]) if ts else None
            actual = min(reported, available) if (reported is not None and available is not None) else None
            capped = (reported - available) if (reported is not None and available is not None) else 0.0
            allocated: list[float | None] = [None] * len(active)
            unallocated = 0.0
            quarantined = 0.0
            if actual is not None:
                allocated = [round(actual * item["ratio"] * factor, SLICE_ROUND) for item in active]
                if ratio_sum > 1 + EPS:
                    # 归一化：多出的比例不参与分配，换算成被隔离的工时。
                    quarantined = actual * (ratio_sum - 1.0) / ratio_sum
                    allocated[-1] = round(allocated[-1] + (actual - sum(allocated)), SLICE_ROUND)
                else:
                    unallocated = actual - sum(allocated)
                    unallocated = round(unallocated, SLICE_ROUND)
                    if abs(unallocated) < 1e-9:
                        unallocated = 0.0
            employee_day_status = "已上报" if ts else "未上报"
            if ts and capped > EPS:
                employee_day_status = "超报已截断"

            # 同员工前后紧接的另一段任职：用来区分“转岗调出/调入”和真正的“离职当日”。
            next_day = day + timedelta(days=1)
            previous_day = day - timedelta(days=1)
            successor_starts = {
                item["start"] for item in by_employee[employee]
                if item["start"] == next_day and item not in active
            }
            predecessor_ends = {
                item["end"] for item in by_employee[employee]
                if item["end"] == previous_day and item not in active
            }

            for index, assignment in enumerate(active):
                if assignment["end"] is not None and assignment["end"] == day:
                    node = "转岗调出" if successor_starts else "离职当日"
                elif assignment["start"] == day and day > period_start:
                    node = "转岗调入" if predecessor_ends else "任职首日"
                else:
                    node = "在职"
                slice_row: dict[str, Any] = {
                    "切片编号": f"{employee}-{day.isoformat()}-{assignment['部门']}-{assignment['id']}",
                    "员工号": employee,
                    "姓名": assignment["姓名"],
                    "部门": assignment["部门"],
                    "日期": day.isoformat(),
                    "任职片段": assignment["id"],
                    "任职开始": _iso(assignment["start"]),
                    "任职结束": _iso(assignment["end"]),
                    "投入比例": round(assignment["ratio"], MAX_ROUND),
                    "当日任职部门数": len(active),
                    "当日比例合计": ratio_sum,
                    "折算系数": round(factor, 6),
                    "可用工时": round(available, SLICE_ROUND) if available is not None else None,
                    "员工当日报工时": round(actual, SLICE_ROUND) if actual is not None else None,
                    "分配工时": round(allocated[index], SLICE_ROUND) if actual is not None else None,
                    "未分配工时": round(unallocated, SLICE_ROUND) if index == 0 else 0.0,
                    "折算差额工时": round(quarantined, SLICE_ROUND) if index == 0 else 0.0,
                    "工时状态": employee_day_status,
                    "任职节点": node,
                    "状态": "正常",
                    "来源依据": {
                        "任职记录": assignment["id"],
                        "任职更新时间": assignment["更新时间"],
                        "工时记录": ts["id"] if ts else None,
                        "工时上报时间": ts["上报时间"] if ts else None,
                        "任职区间": f"{_iso(assignment['start'])}~{_iso(assignment['end']) or '至今'}",
                    },
                }
                if ts is None:
                    slice_row["状态"] = "工时未上报"
                elif ratio_sum > 1 + EPS:
                    slice_row["状态"] = "区间交叠已折算"
                slices.append(slice_row)
    return slices


# ---------------------------------------------------------------------------
# 冲突压缩与区间交集
# ---------------------------------------------------------------------------

def _interval_intersections(prepared: dict[str, Any], period_start: date, period_end: date) -> list[dict[str, Any]]:
    by_employee: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in prepared["assignments"]:
        by_employee[assignment["员工号"]].append(assignment)
    records: list[dict[str, Any]] = []
    for employee, items in sorted(by_employee.items()):
        for left_index in range(len(items)):
            for right_index in range(left_index + 1, len(items)):
                left, right = items[left_index], items[right_index]
                start = max(left["start"], right["start"], period_start)
                left_end = left["end"] or period_end
                right_end = right["end"] or period_end
                end = min(left_end, right_end, period_end)
                if start > end:
                    continue
                ratio_sum = round(left["ratio"] + right["ratio"], 6)
                over = ratio_sum > 1 + EPS
                records.append({
                    "员工号": employee,
                    "姓名": left["姓名"] or right["姓名"],
                    "部门A": left["部门"],
                    "部门B": right["部门"],
                    "交集开始": start.isoformat(),
                    "交集结束": end.isoformat(),
                    "交集天数": len(day_list(start, end)),
                    "比例合计": ratio_sum,
                    "判定": "区间交叠超 1，按比例归一化并隔离多余投入" if over else "并行兼任，比例合计未超 1，按比例分摊",
                    "任职记录": [left["id"], right["id"]],
                })
    return records


def _compress_conflicts(
    slices: Iterable[dict[str, Any]],
    prepared: dict[str, Any],
) -> list[dict[str, Any]]:
    """把逐日矛盾压缩成连续区间；工时超报、区间交叠、工时缺失分别记录。"""
    rows = list(slices)
    by_employee: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_employee[str(row["员工号"])].append(row)

    conflicts: list[dict[str, Any]] = []
    for employee, employee_rows in sorted(by_employee.items()):
        days = sorted({row["日期"] for row in employee_rows})
        # 区间交叠：按比例合计与部门集合分组，把连续日期压成区间。
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for day in days:
            day_rows = [row for row in employee_rows if row["日期"] == day]
            if not day_rows:
                continue
            first = day_rows[0]
            if float(first["当日比例合计"]) > 1 + EPS:
                key = (
                    round(float(first["当日比例合计"]), 6),
                    tuple(sorted(str(row["部门"]) for row in day_rows)),
                )
                grouped[key].append(day_rows[0])
        for (ratio_sum, departments), first_rows in grouped.items():
            for chunk in _consecutive_chunks(first_rows):
                quarantined = 0.0
                for row in chunk:
                    day_rows = [item for item in employee_rows if item["日期"] == row["日期"]]
                    quarantined += float(day_rows[0].get("折算差额工时") or 0)
                conflicts.append({
                    "类型": "任职区间交叠",
                    "员工号": employee,
                    "姓名": employee_rows[0].get("姓名") or "",
                    "开始日期": chunk[0]["日期"],
                    "结束日期": chunk[-1]["日期"],
                    "天数": len(chunk),
                    "涉及部门": list(departments),
                    "比例合计": ratio_sum,
                    "归一化差额工时": _round(quarantined),
                    "说明": (
                        f"{employee} 在 {chunk[0]['日期']}~{chunk[-1]['日期']} 的投入比例合计为 {ratio_sum:g}，"
                        f"超过 1 的部分按比例归一化，{quarantined:.4f} 工时不再拆入任何部门，"
                        "当天各部门投入合计等于实际报工，未超过可用工时"
                    ),
                })
        # 工时超报：逐日记录，连续日期合并。
        over_rows = []
        seen_days: set[str] = set()
        for row in employee_rows:
            day = row["日期"]
            if row["工时状态"] != "超报已截断" or day in seen_days:
                continue
            seen_days.add(day)
            over_rows.append(row)
        for chunk in _consecutive_chunks(over_rows):
            over_hours = 0.0
            for row in chunk:
                ts = prepared["timesheets"].get((employee, parse_day(row["日期"])))
                if ts:
                    over_hours += max(ts["实际上报工时"] - ts["可用工时"], 0.0)
            conflicts.append({
                "类型": "上报工时超过可用工时",
                "员工号": employee,
                "姓名": chunk[0].get("姓名") or "",
                "开始日期": chunk[0]["日期"],
                "结束日期": chunk[-1]["日期"],
                "天数": len(chunk),
                "涉及部门": sorted({str(row["部门"]) for row in chunk}),
                "隔离工时": _round(over_hours),
                "说明": f"{employee} 上报工时超过可用工时，超出部分已截断，不计入人效分母",
            })
        # 工时缺失：按部门汇总连续日期。
        missing_days = sorted({
            row["日期"] for row in employee_rows if row["工时状态"] == "未上报"
        })
        if missing_days:
            department_set = sorted({
                str(row["部门"]) for row in employee_rows if row["工时状态"] == "未上报"
            })
            conflicts.append({
                "类型": "工时未上报",
                "员工号": employee,
                "姓名": employee_rows[0].get("姓名") or "",
                "开始日期": missing_days[0],
                "结束日期": missing_days[-1],
                "天数": len(missing_days),
                "涉及部门": department_set,
                "隔离工时": 0.0,
                "说明": f"{employee} 有 {len(missing_days)} 天任职但没有工时上报，这些天不进入人效分母",
            })
    return conflicts


def _consecutive_chunks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda item: item["日期"])
    chunks: list[list[dict[str, Any]]] = [[ordered[0]]]
    for row in ordered[1:]:
        previous = parse_day(chunks[-1][-1]["日期"])
        current = parse_day(row["日期"])
        if current == previous + timedelta(days=1):
            chunks[-1].append(row)
        else:
            chunks.append([row])
    return chunks


# ---------------------------------------------------------------------------
# 部门聚合与偏差
# ---------------------------------------------------------------------------

def _select_industry(
    prepared: dict[str, Any],
    department: str,
    period_start: date,
    period_end: date,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """选择覆盖整个核算周期的行业均值；过期或周期不匹配的一律不用。"""
    candidates = prepared["industry"].get(department, [])
    if not candidates:
        return None, "无适用行业均值", None
    covering = [
        item for item in candidates
        if item["start"] <= period_start and item["end"] >= period_end
    ]
    if not covering:
        latest = max(candidates, key=lambda item: item["start"])
        return None, "行业均值适用周期不匹配", (
            f"{department} 仅有 {latest['start'].isoformat()}~{latest['end'].isoformat()} 的行业均值，"
            f"未覆盖本次核算 {period_start.isoformat()}~{period_end.isoformat()}"
        )
    chosen = max(covering, key=lambda item: item["start"])
    return chosen, "已匹配适用周期", None


def summarize(
    prepared: dict[str, Any],
    slices: list[dict[str, Any]],
    period_start: date,
    period_end: date,
    range_start: date | None = None,
    range_end: date | None = None,
) -> dict[str, Any]:
    """把核算区间内的日期切片按部门聚合，并生成人效偏差与完整性结论。

    ``period_start/period_end`` 是报告声明的核算周期；
    ``range_start/range_end`` 是实际参与聚合的切片区间（默认等于周期）。
    两者都使用闭区间，产出、工时与任职切片取自同一区间。
    """
    range_start = range_start or period_start
    range_end = range_end or period_end
    period_slices = [
        row for row in slices
        if range_start <= parse_day(row["日期"]) <= range_end
    ]
    by_department: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in period_slices:
        by_department[str(row["部门"])].append(row)

    # 产出按部门汇总；投入只统计已上报工时换算出的分配工时。
    outputs: dict[tuple[str, date], dict[str, Any]] = prepared["outputs"]
    output_by_department: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (department, day), row in outputs.items():
        if range_start <= day <= range_end:
            output_by_department[department].append(row)

    departments = sorted(set(by_department) | set(output_by_department))
    summaries: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = list(prepared["issues"])

    for department in departments:
        rows = by_department.get(department, [])
        reported_rows = [row for row in rows if row["工时状态"] != "未上报"]
        missing_rows = [row for row in rows if row["工时状态"] == "未上报"]
        input_hours = round(sum(float(row["分配工时"] or 0) for row in reported_rows), MAX_ROUND)
        headcount = len({str(row["员工号"]) for row in rows})
        missing_employee_days = len({
            (row["员工号"], row["日期"]) for row in missing_rows
        })
        missing_days = len({row["日期"] for row in missing_rows})
        output_rows = output_by_department.get(department, [])
        output_unit = ""
        unit_conflict = len(prepared["units_by_department"].get(department, set())) > 1
        if output_rows:
            output_unit = str(output_rows[0]["单位"])
        output_quantity = round(sum(float(row["数量"]) for row in output_rows), MAX_ROUND)
        output_days = len({row["日期"] for row in output_rows})

        flags: list[str] = []
        industry, industry_status, industry_note = _select_industry(
            prepared, department, range_start, range_end
        )
        industry_value = industry["行业均值"] if industry else None
        industry_unit = industry["单位"] if industry else ""
        ratio: float | None = None
        ratio_unit = f"{output_unit}/工时" if output_unit else "数量/工时"
        assessment = "完整"
        status = "已核算"
        deviation: float | None = None
        deviation_pct: float | None = None
        deviation_status = "无法对标"

        if headcount == 0:
            assessment = "不可核算"
            status = "分母为零"
            flags.append("有产出但没有人员任职，人效分母为零，不计算人效")
            issues.append({
                "code": "分母为零", "level": "警告", "部门": department,
                "说明": (
                    f"{department} 在周期内有 {output_quantity:g}{output_unit or '单位'} 产出，"
                    f"但没有任何人员任职与投入，人效分母为零"
                ),
            })
        elif not reported_rows:
            assessment = "不完整"
            status = "未上报工时"
            flags.append(f"有 {headcount} 人任职但整周期没有任何工时上报")
            issues.append({
                "code": "未上报工时", "level": "阻断", "部门": department,
                "说明": (
                    f"{department} 有 {headcount} 人、{len(rows)} 个部门日期切片，"
                    f"但 {range_start.isoformat()}~{range_end.isoformat()} 没有任何工时上报，人效分母无法确认"
                ),
            })
        elif unit_conflict:
            assessment = "不完整"
            status = "产出单位不一致"
            flags.append("周期内产出单位不一致，不能直接求和")
        elif not output_rows:
            assessment = "不完整"
            status = "产出未上报"
            flags.append("有人员投入，但周期内没有产出记录（不等于真实零产出）")
            issues.append({
                "code": "产出未上报", "level": "阻断", "部门": department,
                "说明": (
                    f"{department} 有 {input_hours:g} 工时投入，但 "
                    f"{range_start.isoformat()}~{range_end.isoformat()} 没有产出记录，"
                    "不能按零产出处理"
                ),
            })
        elif input_hours <= EPS:
            # 有工时记录但折算后为 0：分母为零，不能拿 0 当分子去算人效。
            assessment = "不可核算"
            status = "分母为零"
            flags.append("已上报工时为 0，人效分母为零，不计算人效")
            issues.append({
                "code": "分母为零", "level": "警告", "部门": department,
                "说明": f"{department} 的已上报工时折算后为 0，无法计算人效",
            })
        else:
            ratio = round(output_quantity / input_hours, MAX_ROUND)
            if missing_rows:
                assessment = "部分核算"
                status = "已核算（工时部分缺失）"
                flags.append(
                    f"有 {missing_employee_days} 个员工日未上报工时，人效只按已上报部分计算"
                )
                issues.append({
                    "code": "工时部分缺失", "level": "阻断", "部门": department,
                    "说明": (
                        f"{department} 有 {missing_employee_days} 个员工日缺少工时上报，"
                        "已核算部分不能视为完整评估"
                    ),
                })
            if output_quantity == 0:
                status = "真实零产出"
                flags.append("产出记录明确上报为 0，是真实零产出，不是产出缺失")

        # 行业对标：周期、单位都要匹配；否则保留原值但不下偏差结论。
        if industry is not None:
            expected_unit = _unit_key(ratio_unit)
            if _unit_key(industry_unit) != expected_unit:
                flags.append(
                    f"行业均值单位「{industry_unit}」与人效单位「{ratio_unit}」不一致，不计算偏差"
                )
                industry_status = "行业均值单位不一致"
                industry_note = (
                    f"{department} 行业均值单位为 {industry_unit}，本次人效单位为 {ratio_unit}"
                )
                industry_value = industry["行业均值"]
            elif ratio is not None:
                deviation = round(ratio - industry["行业均值"], MAX_ROUND)
                if abs(industry["行业均值"]) > EPS:
                    deviation_pct = round(deviation / industry["行业均值"] * 100, 2)
                if abs(deviation) <= 1e-9:
                    deviation_status = "与行业均值持平"
                elif deviation > 0:
                    deviation_status = "高于行业均值"
                else:
                    deviation_status = "低于行业均值"
        if industry_status not in {"已匹配适用周期", "行业均值单位不一致"}:
            issues.append({
                "code": "行业均值不可用", "level": "警告", "部门": department,
                "说明": industry_note or f"{department} 缺少覆盖核算周期的行业均值，偏差暂不计算",
            })

        period_days = max((range_end - range_start).days + 1, 1)
        summaries.append({
            "部门": department,
            "核算状态": status,
            "评估完整性": assessment,
            "人数": headcount,
            "投入人天": round(input_hours / DEFAULT_AVAILABLE_HOURS, MAX_ROUND),
            "折合人数": round(input_hours / DEFAULT_AVAILABLE_HOURS / period_days, MAX_ROUND),
            "投入工时": input_hours,
            "产出数量": output_quantity,
            "产出单位": output_unit,
            "人效": ratio,
            "人效单位": ratio_unit,
            "行业均值": industry_value,
            "行业均值单位": industry_unit,
            "行业均值适用周期": (
                f"{industry['start'].isoformat()}~{industry['end'].isoformat()}" if industry else None
            ),
            "行业均值来源": industry["来源"] if industry else None,
            "行业均值口径": industry["口径"] if industry else None,
            "行业均值匹配状态": industry_status,
            "偏差": deviation,
            "偏差比例": deviation_pct,
            "偏差结论": deviation_status,
            "已上报工时切片数": len(reported_rows),
            "未上报工时员工日": missing_employee_days,
            "未上报工时天数": missing_days,
            "产出上报天数": output_days,
            "核算日期边界": f"{range_start.isoformat()}~{range_end.isoformat()}",
            "明细说明": flags,
            "人数口径": "部门内出现过的不同员工数；同一员工跨部门任职会在多个部门各计一次，不能直接相加",
        })

    # 全局闭合：员工日报工时 = 各部门分配工时 + 未分配工时；被截断/隔离部分单列。
    employee_days: dict[tuple[str, str], dict[str, Any]] = {}
    for row in period_slices:
        key = (str(row["员工号"]), str(row["日期"]))
        bucket = employee_days.setdefault(key, {
            "actual": float(row["员工当日报工时"]) if row["员工当日报工时"] is not None else None,
            "allocated": 0.0,
            "unallocated": 0.0,
            "normalized": 0.0,
            "missing": row["工时状态"] == "未上报",
        })
        bucket["allocated"] += float(row["分配工时"] or 0)
        bucket["unallocated"] += float(row["未分配工时"] or 0)
        bucket["normalized"] += float(row["折算差额工时"] or 0)

    reported_days = [item for item in employee_days.values() if item["actual"] is not None]
    ledger_hours = round(sum(float(item["actual"] or 0) for item in reported_days), MAX_ROUND)
    allocated_hours = round(sum(item["allocated"] for item in reported_days), MAX_ROUND)
    unallocated_hours = round(sum(item["unallocated"] for item in reported_days), MAX_ROUND)
    normalized_hours = round(sum(item["normalized"] for item in reported_days), MAX_ROUND)
    # 与原始台账闭合：每个员工日都拿台账工时做一次核对，而不是把切片简单相加。
    # 区间内的台账工时即使没有对应任职切片，也必须计入“无任职归属工时”，
    # 否则更正任职后工时会被悄悄漏掉，看起来仍然“闭合”。
    ledger_map = prepared["timesheets"]
    ledger_actual = 0.0
    orphan_hours = 0.0
    orphan_days = 0
    for (employee, day), ts in ledger_map.items():
        if not (range_start <= day <= range_end):
            continue
        hours = min(ts["实际上报工时"], ts["可用工时"])
        ledger_actual += hours
        if (employee, day.isoformat()) not in employee_days:
            orphan_hours += hours
            orphan_days += 1
    ledger_actual = round(ledger_actual, MAX_ROUND)
    orphan_hours = round(orphan_hours, MAX_ROUND)
    if orphan_days:
        issues.append({
            "code": "有工时但无任职", "level": "阻断",
            "说明": (
                f"有 {orphan_days} 个员工日上报了工时却没有对应任职切片，"
                f"共 {orphan_hours:g} 工时无法归属部门；请先补齐或更正任职记录"
            ),
        })
    closure = {
        "台账工时合计": ledger_actual,
        "已上报切片工时合计": ledger_hours,
        "分配工时合计": allocated_hours,
        "未分配工时合计": unallocated_hours,
        "归一化差额工时合计": normalized_hours,
        "差额": round(ledger_hours - allocated_hours - unallocated_hours, MAX_ROUND),
        "无任职归属工时": orphan_hours,
        "与台账差额": round(ledger_actual - ledger_hours - orphan_hours, MAX_ROUND),
        "闭合": abs(ledger_hours - allocated_hours - unallocated_hours) < 1e-4
        and abs(ledger_actual - ledger_hours - orphan_hours) < 1e-4,
        "员工日数": len(employee_days),
        "未上报员工日数": sum(1 for item in employee_days.values() if item["missing"]),
        "无任职工时员工日数": orphan_days,
        "说明": (
            "分配工时 + 未分配工时 = 员工当日报工时；"
            "台账工时 = 切片工时 + 无任职归属工时，两项差额都为 0 才视为与原始人力台账闭合"
        ),
    }

    blocking = [item for item in issues if item.get("level") == "阻断"]
    warnings = [item for item in issues if item.get("level") == "警告"]
    incomplete_departments = [
        item["部门"] for item in summaries if item["评估完整性"] in {"不完整", "部分核算"}
    ]
    unassessable_departments = [
        item["部门"] for item in summaries if item["评估完整性"] == "不可核算"
    ]
    # 有部门不可核算（例如分母为零）同样说明依据不齐，不能形成完整评估。
    complete = (
        not blocking
        and not incomplete_departments
        and not unassessable_departments
        and bool(summaries)
    )
    if not summaries:
        blocking.append({
            "code": "周期内无数据", "level": "阻断",
            "说明": f"{range_start.isoformat()}~{range_end.isoformat()} 没有任职、工时或产出数据，不能形成评估",
        })
        complete = False
    completeness = {
        "complete": complete,
        "conclusion": "可形成完整评估" if complete else "数据未齐，不能形成完整评估",
        "blocking": blocking,
        "warnings": warnings,
        "incomplete_departments": incomplete_departments,
        "unassessable_departments": unassessable_departments,
        "assessed_departments": [
            item["部门"] for item in summaries if item["评估完整性"] == "完整"
        ],
        "departments": [
            {"部门": item["部门"], "评估完整性": item["评估完整性"], "核算状态": item["核算状态"]}
            for item in summaries
        ],
    }

    conflicts = list(prepared["conflicts"])
    conflicts.extend(_compress_conflicts(period_slices, prepared))
    intersections = _interval_intersections(prepared, period_start, period_end)

    covered_employees = sorted({str(row["员工号"]) for row in period_slices})
    period_days = max((range_end - range_start).days + 1, 1)
    total_input = round(sum(float(item["投入工时"] or 0) for item in summaries), MAX_ROUND)
    headcount_summary = {
        "覆盖员工数": len(covered_employees),
        "员工号": covered_employees,
        "部门人数合计": sum(int(item["人数"]) for item in summaries),
        "折合投入人数": round(total_input / DEFAULT_AVAILABLE_HOURS / period_days, MAX_ROUND),
        "投入工时合计": total_input,
        "说明": "覆盖员工数按人去重；部门人数按部门统计，跨部门任职会在多个部门各计一次，不能相加当作总人数",
    }

    return {
        "headcount": headcount_summary,
        "departments": summaries,
        "issues": issues,
        "completeness": completeness,
        "closure": closure,
        "conflicts": conflicts,
        "interval_intersections": intersections,
    }


def conversion_notes() -> dict[str, Any]:
    """把核算口径写成可引用的说明，报告附件和接口都会带出去。"""
    return {
        "投入折算": "部门当日分配工时 = 员工当日报工时 × 该部门投入比例；同日比例合计超过 1 时先归一化",
        "日期边界": "任职、工时、产出统一按自然日闭区间 [开始日, 结束日] 切分，首尾两天都计入",
        "工时上限": "员工当日报工不超过可用工时，超出部分截断隔离；各部门分配工时合计不超过当日报工",
        "未分配工时": "同日投入比例合计小于 1 时，剩余报工计入未分配工时，不归属任何部门",
        "区间交叠": "同一员工多条任职在同一天同时生效即视为区间交叠，比例合计超过 1 的部分归一化隔离",
        "离职当日": "任职结束日当天仍在岗，计入投入；结束日次日不再产生切片",
        "零值语义": "人效分母为零、工时未上报、产出未上报、真实零产出是四种不同状态，不互相替代",
    }


def build_result(
    prepared: dict[str, Any],
    slices: list[dict[str, Any]],
    period_start: date,
    period_end: date,
    range_start: date | None = None,
    range_end: date | None = None,
) -> dict[str, Any]:
    range_start = range_start or period_start
    range_end = range_end or period_end
    summary = summarize(prepared, slices, period_start, period_end, range_start, range_end)
    return {
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "aggregation_range": {"start": range_start.isoformat(), "end": range_end.isoformat()},
        "slices": slices,
        "conversion": conversion_notes(),
        **summary,
    }
