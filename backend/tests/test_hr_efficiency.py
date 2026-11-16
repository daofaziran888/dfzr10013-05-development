"""人力投入核算与报告附件的回归测试（标准库 unittest，不依赖测试服务器）。

覆盖：
- 月中转岗、半日兼任、任职区间交叠、离职当天、无工时部门；
- 分母为零 / 未上报工时 / 产出未上报 / 真实零产出 四种零值语义；
- 补报与更正后重新核算，旧附件不变、新核算使用新依据；
- 跨部门投入总量与原始人力台账闭合。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import hr_calc, hr_efficiency, hr_source  # noqa: E402
from app.services.report import ReportService  # noqa: E402
from app.store import store  # noqa: E402


class CalculationTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_datasets()
        hr_source.reset()
        hr_efficiency.reset()
        ReportService().reset()

    def tearDown(self) -> None:
        store.reset_datasets()

    def _source(self):
        return hr_source.snapshot()

    def _run(self, start="2026-06-01", end="2026-06-30", **kwargs):
        batch, message = hr_efficiency.run(period_start=start, period_end=end, **kwargs)
        self.assertIsNotNone(batch, message)
        return batch

    # ------------------------------------------------------------------
    # 日期切片与投入折算
    # ------------------------------------------------------------------
    def test_month_mid_transfer_splits_slices(self) -> None:
        batch = self._run()
        by_department = {item["部门"]: item for item in batch["departments"]}
        # 销售一部 = E001 前半月 15 天 (15×8) + E002 半日兼任整月 (30×8×0.5)，
        # 再减去 E002 在 6/15 只报 4 小时造成的 4 小时差额。
        self.assertEqual(by_department["销售一部"]["投入工时"], 15 * 8 + 30 * 4 - 2)
        # 销售二部 = E001 15 天 + E003 30 天 + E003 交叠 2 天折算
        self.assertGreater(by_department["销售二部"]["投入工时"], 15 * 8)
        e001_transfer_day = self._slice(batch, "E001", "2026-06-15")
        self.assertEqual(len(e001_transfer_day), 1)
        self.assertEqual(e001_transfer_day[0]["部门"], "销售一部")
        self.assertEqual(e001_transfer_day[0]["任职节点"], "转岗调出")
        e001_next = self._slice(batch, "E001", "2026-06-16")
        self.assertEqual(e001_next[0]["部门"], "销售二部")
        self.assertEqual(e001_next[0]["任职节点"], "转岗调入")

    def test_half_day_concurrent_assignment(self) -> None:
        batch = self._run()
        rows = self._slice(batch, "E002", "2026-06-15")
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["分配工时"] for row in rows), 4.0)
        self.assertEqual(sum(row["投入比例"] for row in rows), 1.0)
        self.assertEqual({row["部门"] for row in rows}, {"销售一部", "交付中心"})
        # 未超过实际可用工时
        self.assertTrue(all(row["分配工时"] <= row["可用工时"] for row in rows))

    def test_overlapping_intervals_are_normalized_not_added(self) -> None:
        batch = self._run()
        rows = self._slice(batch, "E003", "2026-06-25")
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(sum(row["分配工时"] for row in rows), 8.0, places=4)
        self.assertAlmostEqual(sum(row["分配工时"] for row in rows), rows[0]["员工当日报工时"], places=4)
        conflict = next(item for item in batch["conflicts"] if item["类型"] == "任职区间交叠")
        self.assertEqual(conflict["开始日期"], "2026-06-25")
        self.assertEqual(conflict["结束日期"], "2026-06-26")
        self.assertEqual(conflict["比例合计"], 1.5)
        self.assertAlmostEqual(conflict["归一化差额工时"], 2 * 8 * (0.5 / 1.5), places=3)
        intersection = next(item for item in batch["interval_intersections"] if item["员工号"] == "E003")
        self.assertEqual(intersection["交集天数"], 2)
        self.assertIn("区间交叠", intersection["判定"])

    def test_last_working_day_is_counted(self) -> None:
        batch = self._run()
        rows = self._slice(batch, "E004", "2026-06-30")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["任职节点"], "离职当日")
        self.assertEqual(rows[0]["分配工时"], 8.0)
        # 离职次日不再有切片
        self.assertEqual(self._slice(batch, "E004", "2026-07-01"), [])

    def test_orphan_timesheet_after_correction_is_flagged(self) -> None:
        # 把 E001 在销售一部的任职结束日提前到 6/10，6/16~6/20 的工时就失去任职归属。
        hr_source.update_assignment("A-1", {"任职结束": "2026-06-10"})
        batch = self._run()
        closure = batch["closure"]
        self.assertEqual(closure["无任职工时员工日数"], 5)
        self.assertAlmostEqual(closure["无任职归属工时"], 40.0, places=4)
        self.assertTrue(closure["闭合"])
        codes = {item["code"] for item in batch["completeness"]["blocking"]}
        self.assertIn("有工时但无任职", codes)
        self.assertFalse(batch["completeness"]["complete"])
        # 这部分工时没有被摊进任何部门
        total_department = sum(item["投入工时"] for item in batch["departments"])
        self.assertAlmostEqual(total_department, batch["closure"]["分配工时合计"], places=4)

    def test_reported_hours_capped_by_available_hours(self) -> None:
        hr_source.list_timesheets()
        rows = store.dataset_rows(hr_source.TIMESHEETS)
        target = next(row for row in rows if row["员工号"] == "E001" and row["日期"] == "2026-06-01")
        target["实际上报工时"] = 12.0
        # 直接改内存台账，模拟前端提交超报
        store.dataset_rows(hr_source.TIMESHEETS)[
            next(
                index for index, row in enumerate(store.dataset_rows(hr_source.TIMESHEETS))
                if row["id"] == target["id"]
            )
        ] = target
        batch = self._run()
        slice_rows = self._slice(batch, "E001", "2026-06-01")
        self.assertEqual(slice_rows[0]["分配工时"], 8.0)
        conflict = next(item for item in batch["conflicts"] if item["类型"] == "上报工时超过可用工时")
        self.assertAlmostEqual(conflict["隔离工时"], 4.0, places=4)

    # ------------------------------------------------------------------
    # 零值语义与完整性
    # ------------------------------------------------------------------
    def test_zero_value_semantics_are_distinct(self) -> None:
        batch = self._run()
        by_department = {item["部门"]: item for item in batch["departments"]}
        # 分母为零：有产出没人员
        self.assertEqual(by_department["战略部"]["核算状态"], "分母为零")
        self.assertIsNone(by_department["战略部"]["人效"])
        self.assertEqual(by_department["战略部"]["评估完整性"], "不可核算")
        # 未上报工时：有人员没工时
        self.assertEqual(by_department["研发中心"]["核算状态"], "未上报工时")
        self.assertIsNone(by_department["研发中心"]["人效"])
        # 产出未上报：有投入没产出记录
        self.assertEqual(by_department["运营部"]["核算状态"], "产出未上报")
        self.assertIsNone(by_department["运营部"]["人效"])
        # 真实零产出：有明确 0 记录
        self.assertEqual(by_department["市场部"]["核算状态"], "真实零产出")
        self.assertEqual(by_department["市场部"]["人效"], 0.0)
        self.assertEqual(by_department["市场部"]["产出上报天数"], 5)

    def test_incomplete_data_blocks_complete_assessment(self) -> None:
        batch = self._run()
        self.assertFalse(batch["completeness"]["complete"])
        self.assertEqual(batch["完整性结论"], "数据未齐，不能形成完整评估")
        codes = {item["code"] for item in batch["completeness"]["blocking"]}
        self.assertIn("未上报工时", codes)
        self.assertIn("产出未上报", codes)
        self.assertIn("研发中心", batch["completeness"]["incomplete_departments"])
        self.assertIn("战略部", batch["completeness"]["unassessable_departments"])

    def test_complete_assessment_when_no_gaps(self) -> None:
        # 只核算销售一部：E001(15 天) + E002(0.5，整月) 都有工时和产出
        batch, message = hr_efficiency.run(
            period_start="2026-06-01", period_end="2026-06-15"
        )
        self.assertIsNotNone(batch, message)
        by_department = {item["部门"]: item for item in batch["departments"]}
        self.assertIn("销售一部", by_department)
        self.assertEqual(by_department["销售一部"]["评估完整性"], "完整")

    # ------------------------------------------------------------------
    # 行业均值口径
    # ------------------------------------------------------------------
    def test_zero_reported_hours_is_zero_denominator(self) -> None:
        hr_source.list_timesheets()  # 确保示例台账已经装载
        rows = store.dataset_rows(hr_source.TIMESHEETS)
        for row in rows:
            if row["员工号"] == "E004":
                row["实际上报工时"] = 0.0
        batch = self._run()
        customer = next(item for item in batch["departments"] if item["部门"] == "客服部")
        self.assertEqual(customer["核算状态"], "分母为零")
        self.assertIsNone(customer["人效"])
        self.assertEqual(customer["评估完整性"], "不可核算")
        self.assertFalse(batch["completeness"]["complete"])

    def test_industry_average_requires_matching_period_and_unit(self) -> None:
        batch = self._run()
        by_department = {item["部门"]: item for item in batch["departments"]}
        sales_one = by_department["销售一部"]
        self.assertEqual(sales_one["行业均值匹配状态"], "已匹配适用周期")
        self.assertEqual(sales_one["行业均值"], 2.2)  # 6 月口径而不是 5 月的 1.9
        self.assertEqual(sales_one["行业均值适用周期"], "2026-06-01~2026-06-30")
        # 单位不一致时不计算偏差
        customer = by_department["客服部"]
        self.assertEqual(customer["行业均值单位"], "单/工时")
        self.assertIsNotNone(customer["偏差"])

    def test_industry_average_out_of_period_is_not_used(self) -> None:
        batch, message = hr_efficiency.run(
            period_start="2026-06-01", period_end="2026-06-30",
            note="行业均值窗口测试",
        )
        self.assertIsNotNone(batch, message)
        # 7 月没有任何覆盖该周期的行业均值，只能明确拒绝，不能拿 6 月或 5 月的值顶替。
        prepared = hr_calc.prepare(hr_source.snapshot(), hr_calc.parse_day("2026-07-01"), hr_calc.parse_day("2026-07-31"))
        industry, status, note = hr_calc._select_industry(
            prepared, "销售一部", hr_calc.parse_day("2026-07-01"), hr_calc.parse_day("2026-07-31")
        )
        self.assertIsNone(industry)
        self.assertEqual(status, "行业均值适用周期不匹配")
        self.assertIn("未覆盖本次核算 2026-07-01~2026-07-31", note)

    # ------------------------------------------------------------------
    # 闭合
    # ------------------------------------------------------------------
    def test_cross_department_input_closes_with_ledger(self) -> None:
        batch = self._run()
        closure = batch["closure"]
        self.assertTrue(closure["闭合"])
        self.assertEqual(closure["差额"], 0)
        self.assertEqual(closure["与台账差额"], 0)
        self.assertEqual(
            closure["分配工时合计"] + closure["未分配工时合计"],
            closure["已上报切片工时合计"],
        )

    def test_department_hours_never_exceed_ledger(self) -> None:
        batch = self._run()
        total_department = sum(item["投入工时"] for item in batch["departments"])
        self.assertLessEqual(total_department, batch["closure"]["台账工时合计"] + 1e-6)
        self.assertEqual(total_department, batch["closure"]["分配工时合计"])

    def test_headcount_does_not_double_count_people(self) -> None:
        batch = self._run()
        self.assertEqual(batch["headcount"]["覆盖员工数"], 7)
        # 部门人数合计会重复计跨部门员工，应大于去重人数
        self.assertGreater(batch["headcount"]["部门人数合计"], batch["headcount"]["覆盖员工数"])

    # ------------------------------------------------------------------
    # 更正与重算
    # ------------------------------------------------------------------
    def test_recalculation_after_correction_keeps_old_batch(self) -> None:
        first = self._run()
        old_sales_two = next(item for item in first["departments"] if item["部门"] == "销售二部")
        row, message = hr_source.update_assignment("A-2", {"任职开始": "2026-06-21"})
        self.assertIsNotNone(row, message)
        second = self._run()
        self.assertEqual(second["前一批次"], first["批次号"])
        new_sales_two = next(item for item in second["departments"] if item["部门"] == "销售二部")
        self.assertLess(new_sales_two["投入工时"], old_sales_two["投入工时"])
        # 旧批次仍然保留原值
        stored = hr_efficiency.get_batch(first["批次号"])
        stored_sales_two = next(item for item in stored["departments"] if item["部门"] == "销售二部")
        self.assertEqual(stored_sales_two["投入工时"], old_sales_two["投入工时"])
        # 变化清单只列出真正变动的部门
        changed = {item["部门"]: item["变化"] for item in second["相对前批次变化"]}
        self.assertIn("销售二部", changed)
        self.assertIn("投入工时", changed["销售二部"])

    def test_backfill_does_not_double_count_hours(self) -> None:
        first = self._run()
        baseline = first["headcount"]["投入工时合计"]
        row, message = hr_source.add_timesheet({
            "员工号": "E006", "日期": "2026-06-01", "可用工时": 8, "实际上报工时": 8,
        })
        self.assertIsNotNone(row, message)
        # 重复补报被拒绝
        duplicate, duplicate_message = hr_source.add_timesheet({
            "员工号": "E006", "日期": "2026-06-01", "可用工时": 8, "实际上报工时": 8,
        })
        self.assertIsNone(duplicate)
        self.assertIn("已有工时记录", duplicate_message)
        second = self._run()
        self.assertAlmostEqual(second["headcount"]["投入工时合计"], baseline + 8.0, places=4)
        self.assertEqual(first["headcount"]["投入工时合计"], baseline)

    def test_source_records_mutation_is_tracked(self) -> None:
        hr_source.update_assignment("A-2", {"任职开始": "2026-06-21"})
        events = hr_source.list_events()
        self.assertTrue(events)
        self.assertEqual(events[-1]["类型"], "更正任职")
        self.assertIn("新值", events[-1]["变更"]["任职开始"])

    # ------------------------------------------------------------------
    # 报告附件
    # ------------------------------------------------------------------
    def test_report_attachment_is_immutable_and_recomputable(self) -> None:
        batch = self._run()
        service = ReportService()
        entry, message = service.run_action(1, "生成报告")
        self.assertIsNotNone(entry, message)
        attachment = service.get_attachment(1, 1)
        self.assertIsNotNone(attachment)
        self.assertEqual(attachment["批次号"], batch["批次号"])
        original_count = attachment["人数"]
        original_departments = {
            item["部门"]: (item["投入工时"], item["人效"]) for item in attachment["快照"]["部门汇总"]
        }

        hr_source.update_assignment("A-2", {"任职开始": "2026-06-21"})
        new_batch = self._run()

        # 原附件不变
        still = service.get_attachment(1, 1)
        self.assertEqual(still["人数"], original_count)
        self.assertEqual(
            {item["部门"]: (item["投入工时"], item["人效"]) for item in still["快照"]["部门汇总"]},
            original_departments,
        )
        # 新偏差数据集使用更正后的投入
        dataset = hr_efficiency.deviation_dataset()
        self.assertEqual(dataset["批次号"], new_batch["批次号"])
        new_sales_two = next(item for item in dataset["departments"] if item["部门"] == "销售二部")
        self.assertNotEqual(new_sales_two["投入工时"], original_departments["销售二部"][0])
        # 旧附件仍可复算
        result, message = service.recompute_attachment(1, 1)
        self.assertIsNotNone(result, message)
        self.assertTrue(result["一致"])
        self.assertEqual(result["切片数"]["附件"], result["切片数"]["复算"])

    def test_report_attachment_requires_batch(self) -> None:
        service = ReportService()
        entry, message = service.run_action(1, "生成报告")
        self.assertIsNone(entry)
        self.assertIn("尚未生成部门人效偏差数据集", message)

    def test_attachment_contains_slices_summary_and_industry(self) -> None:
        self._run()
        service = ReportService()
        service.run_action(1, "生成报告")
        attachment = service.public_attachment(1, 1)
        self.assertIn("人员日期切片", attachment)
        self.assertIn("部门汇总", attachment)
        self.assertIn("行业口径", attachment)
        self.assertTrue(attachment["人员日期切片"])
        industry = {item["部门"]: item for item in attachment["行业口径"]}
        self.assertEqual(industry["销售一部"]["适用周期"], "2026-06-01~2026-06-30")
        self.assertEqual(industry["销售一部"]["单位"], "件/工时")
        # 复算所需依据不对外暴露
        self.assertNotIn("依据快照", attachment)

    def test_second_attachment_version(self) -> None:
        self._run()
        service = ReportService()
        service.run_action(1, "生成报告")
        hr_source.update_assignment("A-2", {"任职开始": "2026-06-21"})
        self._run()
        service.run_action(1, "生成报告")
        versions = service.list_attachments(1)
        self.assertEqual([item["版本"] for item in versions], [1, 2])
        v1 = service.get_attachment(1, 1)
        v2 = service.get_attachment(1, 2)
        self.assertNotEqual(v1["批次号"], v2["批次号"])

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _slice(batch, employee: str, day: str) -> list[dict]:
        return [
            row for row in batch["slices"]
            if row["员工号"] == employee and row["日期"] == day
        ]


class DateBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_datasets()
        hr_source.reset()

    def test_inclusive_boundaries(self) -> None:
        batch, _ = hr_efficiency.run(period_start="2026-06-01", period_end="2026-06-30")
        days = {row["日期"] for row in batch["slices"]}
        self.assertIn("2026-06-01", days)
        self.assertIn("2026-06-30", days)
        self.assertEqual(len(days), 30)

    def test_empty_period_is_blocked(self) -> None:
        batch, _ = hr_efficiency.run(period_start="2026-09-01", period_end="2026-09-30")
        self.assertFalse(batch["completeness"]["complete"])
        # 未填结束日的任职会延续到 9 月，但没有工时和产出，只能给出未上报状态。
        self.assertTrue(batch["departments"])
        self.assertTrue(all(item["人效"] is None for item in batch["departments"]))
        codes = {item["code"] for item in batch["completeness"]["blocking"]}
        self.assertIn("未上报工时", codes)

    def test_invalid_period(self) -> None:
        batch, message = hr_efficiency.run(period_start="2026-06-30", period_end="2026-06-01")
        self.assertIsNone(batch)
        self.assertIn("结束日不能早于开始日", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)