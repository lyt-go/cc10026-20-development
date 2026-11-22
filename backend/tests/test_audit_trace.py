"""调用审计-导出执行事件关联与恢复补发的端到端测试。

场景（对应取数补发申请）：
- 同一任务号两次执行：原执行成功产出文件，重跑中断无文件
- 调用明细检索按 (任务号, 尝试序号) 区分派发/成功/中断，绝不合并成一次成功
- 成功尝试可回查最初输入与产物出处，中断尝试可查回来源并标记可恢复
- 从审计列表进入检索，带请求走恢复入口，再按补发入口取数
- 原执行回执 + 中断重跑回执 + 另一任务回执混合查询，确认恢复关系与文件出处
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

temp_data = tempfile.mkdtemp(prefix='cc10026_trace_')
os.environ['DATA_ROOT'] = temp_data


class TestExportTraceAndRecovery:
    def setup_method(self):
        from app.seed import SEED_ROWS
        from app.store import store as s
        s._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        if os.path.exists(temp_data):
            shutil.rmtree(temp_data, ignore_errors=True)

    def _make_task(self, svc, task_no: str, api: str = "客户订单查询API") -> int:
        entry, missing = svc.create_entry({
            "任务编号": task_no,
            "关联API": api,
            "查询参数": f"region=east&task={task_no}",
            "导出格式": "json",
        })
        assert missing == []
        return entry["id"]

    def test_same_task_two_attempts_not_merged(self):
        """同任务号原执行成功、重跑中断，检索结果是两次独立尝试而非一次成功。"""
        from app.services.export_task import ExportTaskService
        from app.services.call_audit import CallAuditService

        export_svc = ExportTaskService()
        audit_svc = CallAuditService()
        task_no = "EXP-DUP-001"
        export_id = self._make_task(export_svc, task_no)

        # 第一次执行：派发 -> 成功，有产物
        first = export_svc.dispatch_export(export_id)
        assert first["attempt_no"] == 1
        done = export_svc.execute_export(export_id, 1)
        assert done["ok"] and done["attempt_no"] == 1
        first_receipt = done["receipt_id"]

        # 第二次执行（重跑）：派发 -> 中断，无产物
        second = export_svc.dispatch_export(export_id)
        assert second["attempt_no"] == 2
        interrupted = export_svc.interrupt_export(export_id, 2, reason="重跑时连接中断")
        assert interrupted["ok"]
        second_receipt = interrupted["receipt_id"]
        assert first_receipt != second_receipt

        trace = audit_svc.trace_export_calls(task_no=task_no)
        assert trace["attempt_count"] == 2, "同任务号两次执行必须保留为两次尝试"
        a1, a2 = trace["attempts"]
        assert a1["attempt_no"] == 1 and a1["outcome"] == "成功"
        assert a2["attempt_no"] == 2 and a2["outcome"] == "中断"
        # 关键断言：不能因为任务号相同把两个尝试并成一次成功
        assert trace["tasks"][0]["succeeded_count"] == 1
        assert trace["tasks"][0]["interrupted_count"] == 1

        # 第一次仍有文件，第二次没有文件但可恢复
        assert a1["has_file"] is True and a1["artifact_file"]
        assert a2["has_file"] is False and a2["recoverable"] is True

        # 派发/成功/中断各阶段都能在尝试事件里区分
        assert "派发" in a1["stages"] and "成功" in a1["stages"]
        assert "派发" in a2["stages"] and "中断" in a2["stages"]

        # 成功产物能回查最初输入，中断尝试也能查回来源请求
        assert a1["query_params"].startswith("region=east")
        assert a2["resume_request"]["query_params"] == a1["query_params"]
        assert a2["resume_request"]["receipt_id"] == second_receipt

    def test_audit_list_carries_correlation_keys(self):
        """审计列表中的导出事件镜像带任务号、尝试序号和回执，可据此进入检索。"""
        from app.services.export_task import ExportTaskService
        from app.services.call_audit import CallAuditService
        from app.store import store

        export_svc = ExportTaskService()
        audit_svc = CallAuditService()
        export_id = self._make_task(export_svc, "EXP-AUDIT-001")
        export_svc.dispatch_export(export_id)
        export_svc.execute_export(export_id, 1)

        rows = store.rows("call_audit")
        mirrored = [r for r in rows if r.get("任务编号") == "EXP-AUDIT-001"]
        assert len(mirrored) == 2  # 派发 + 成功
        assert all(r.get("执行回执") for r in mirrored)
        assert {r.get("事件阶段") for r in mirrored} == {"派发", "成功"}
        # 从审计列表可带任务号进入调用明细检索
        trace = audit_svc.trace_export_calls(task_no="EXP-AUDIT-001")
        assert trace["attempts"][0]["receipt_id"] == mirrored[0]["执行回执"]

    def test_recovery_entry_then_reissue_from_artifact(self):
        """带检索到的请求进入恢复入口，随后从确认的产物按补发入口取数。"""
        from app.services.export_task import ExportTaskService
        from app.services.call_audit import CallAuditService
        from app.services.data_manager import export_attempt_file

        export_svc = ExportTaskService()
        audit_svc = CallAuditService()
        export_id = self._make_task(export_svc, "EXP-REC-001")

        first = export_svc.dispatch_export(export_id)
        export_svc.execute_export(export_id, first["attempt_no"])
        rerun = export_svc.dispatch_export(export_id)
        export_svc.interrupt_export(export_id, rerun["attempt_no"])

        trace = audit_svc.trace_export_calls(task_no="EXP-REC-001")
        interrupted_attempt = next(a for a in trace["attempts"] if a["outcome"] == "中断")

        # 恢复入口：带着中断尝试的请求重跑，生成第 3 次尝试
        recovered = export_svc.recover_export(
            export_id, interrupted_attempt["attempt_no"],
            resume_receipt=interrupted_attempt["receipt_id"])
        assert recovered["ok"]
        assert recovered["attempt_no"] == 3
        assert export_attempt_file(export_id, 3) is not None

        # 恢复关系可核查：第 3 次尝试记录了来源尝试与来源回执
        attempts = export_svc.list_attempts(export_id)
        third = next(a for a in attempts if a["attempt_no"] == 3)
        resume_event = next(e for e in third["events"] if e["event"] == "export_dispatched")
        assert resume_event.get("resumed_from_attempt") == 2
        assert resume_event.get("resume_receipt") == interrupted_attempt["receipt_id"]
        # 尝试列表能直接分辨哪次仍有文件、哪次无产物
        first_row = next(a for a in attempts if a["attempt_no"] == 1)
        broken_row = next(a for a in attempts if a["attempt_no"] == 2)
        assert first_row["has_file"] is True and first_row["artifact_file"]
        assert broken_row["has_file"] is False and broken_row["artifact_file"] is None
        assert third["resumed_from_attempt"] == 2

        # 补发入口：从第一次成功尝试的确认产物取数
        reissue = export_svc.reissue_export(export_id, 1)
        assert reissue["ok"]
        assert reissue["reissued_from"] == "artifact"
        assert reissue["rows_count"] == 3
        assert reissue["artifact_file"] == str(export_attempt_file(export_id, 1))

    def test_interrupted_reissue_requires_supplemental_inputs(self):
        """中断尝试缺输入时补发被拦，沿记录补采后恢复并带回补采输入。"""
        from app.services.export_task import ExportTaskService

        export_svc = ExportTaskService()
        export_id = self._make_task(export_svc, "EXP-MISS-001")
        first = export_svc.dispatch_export(export_id)
        export_svc.execute_export(export_id, first["attempt_no"])
        rerun = export_svc.dispatch_export(export_id)
        export_svc.interrupt_export(export_id, rerun["attempt_no"])

        # 未补采：拒绝并指明缺输入与来源
        blocked = export_svc.reissue_export(export_id, 2)
        assert blocked["ok"] is False
        assert blocked["missing_inputs"] is True
        assert blocked["source_query_params"]

        # 沿事件记录补采后再补发：走恢复并产出新文件
        reissue = export_svc.reissue_export(
            export_id, 2, supplemental_inputs=["补采:region=east&page=2"])
        assert reissue["ok"]
        assert reissue["reissued_from"] == "recovered"
        assert reissue["supplemental_inputs"] == ["补采:region=east&page=2"]
        assert reissue["source_receipt_id"] == blocked["receipt_id"]

    def test_mixed_receipts_query_isolates_tasks_and_files(self):
        """原执行、中断重跑和另一任务的回执一起查询，任务隔离且文件出处明确。"""
        from app.services.export_task import ExportTaskService
        from app.services.call_audit import CallAuditService

        export_svc = ExportTaskService()
        audit_svc = CallAuditService()

        # 任务 A：成功 + 中断重跑
        id_a = self._make_task(export_svc, "EXP-MIX-A")
        a1 = export_svc.dispatch_export(id_a)
        export_svc.execute_export(id_a, a1["attempt_no"])
        a2 = export_svc.dispatch_export(id_a)
        export_svc.interrupt_export(id_a, a2["attempt_no"])
        receipt_a1, receipt_a2 = a1["receipt_id"], a2["receipt_id"]

        # 任务 B：另一任务的成功回执
        id_b = self._make_task(export_svc, "EXP-MIX-B")
        b1 = export_svc.dispatch_export(id_b)
        export_svc.execute_export(id_b, b1["attempt_no"])
        receipt_b1 = b1["receipt_id"]

        trace = audit_svc.trace_export_calls(
            receipt_ids=[receipt_a1, receipt_a2, receipt_b1])
        assert trace["task_count"] == 2, "不同任务号的回执不能并到同一任务"
        task_nos = {t["task_no"] for t in trace["tasks"]}
        assert task_nos == {"EXP-MIX-A", "EXP-MIX-B"}

        by_receipt = {a["receipt_id"]: a for a in trace["attempts"]}
        assert by_receipt[receipt_a1]["outcome"] == "成功"
        assert by_receipt[receipt_a1]["has_file"] is True
        assert by_receipt[receipt_a2]["outcome"] == "中断"
        assert by_receipt[receipt_a2]["has_file"] is False
        assert by_receipt[receipt_b1]["outcome"] == "成功"
        assert by_receipt[receipt_b1]["export_id"] == id_b

        # 未知回执被显式标出而不是静默吞掉
        unknown = audit_svc.trace_export_calls(receipt_ids=["RCPT-NOPE-9999"])
        assert unknown["unknown_receipts"] == ["RCPT-NOPE-9999"]

        # 按 A 任务中断回执走补发入口（补采恢复），核查恢复关系与文件出处
        reissue = export_svc.reissue_export(
            id_a, by_receipt[receipt_a2]["attempt_no"],
            supplemental_inputs=["补采:delta_since=interrupt"])
        assert reissue["ok"] and reissue["reissued_from"] == "recovered"
        assert reissue["source_receipt_id"] == receipt_a2
        assert reissue["task_no"] == "EXP-MIX-A"

    def test_trace_dedupes_events_when_task_and_receipt_both_given(self):
        """任务号与回执两条查询路径命中同一事件时按 event_id 去重。"""
        from app.services.export_task import ExportTaskService
        from app.services.call_audit import CallAuditService

        export_svc = ExportTaskService()
        audit_svc = CallAuditService()
        export_id = self._make_task(export_svc, "EXP-DEDUP-001")
        d1 = export_svc.dispatch_export(export_id)
        export_svc.execute_export(export_id, d1["attempt_no"])

        trace = audit_svc.trace_export_calls(
            task_no="EXP-DEDUP-001", receipt_ids=[d1["receipt_id"]])
        attempt = trace["attempts"][0]
        event_ids = [e["event"] for e in attempt["events"]]
        assert event_ids == ["export_dispatched", "export_succeeded"], "事件不得重复"
        assert attempt["stages"] == ["派发", "成功"]

    def test_guards_prevent_duplicate_interrupt_and_direct_execute(self):
        """重复中断、直接执行已中断/已成功尝试都应被拦截，恢复只能走恢复入口。"""
        from app.services.export_task import ExportTaskService

        export_svc = ExportTaskService()
        export_id = self._make_task(export_svc, "EXP-GUARD-001")
        d1 = export_svc.dispatch_export(export_id)
        export_svc.execute_export(export_id, d1["attempt_no"])
        d2 = export_svc.dispatch_export(export_id)
        export_svc.interrupt_export(export_id, d2["attempt_no"])

        # 不能重复中断
        again = export_svc.interrupt_export(export_id, 2)
        assert again["ok"] is False and "已中断" in again["message"]
        # 不能中断已成功的尝试
        interrupt_ok = export_svc.interrupt_export(export_id, 1)
        assert interrupt_ok["ok"] is False and "已成功" in interrupt_ok["message"]
        # 已中断尝试不能直接补执行，必须走恢复入口
        direct = export_svc.execute_export(export_id, 2)
        assert direct["ok"] is False and "恢复入口" in direct["message"]
        # 已成功尝试不能重复执行
        repeat = export_svc.execute_export(export_id, 1)
        assert repeat["ok"] is False and "已成功" in repeat["message"]

    def test_router_endpoints_support_full_flow(self):
        """路由层：检索、尝试列表、恢复、补发四个入口的请求/响应结构可用。"""
        from app.routers import audit_trace
        from app.services.export_task import ExportTaskService

        export_svc = ExportTaskService()
        export_id = self._make_task(export_svc, "EXP-API-001")
        d1 = export_svc.dispatch_export(export_id)
        export_svc.execute_export(export_id, d1["attempt_no"])
        d2 = export_svc.dispatch_export(export_id)
        export_svc.interrupt_export(export_id, d2["attempt_no"])

        # GET 检索（多回执逗号分隔）
        trace = audit_trace.trace_export_calls(
            task_no="EXP-API-001", receipts=f"{d1['receipt_id']},{d2['receipt_id']}")
        assert trace["ok"] and trace["attempt_count"] == 2

        # GET 尝试列表
        listed = audit_trace.list_export_attempts(export_id)
        assert listed["attempt_count"] == 2

        # POST 恢复入口
        recovered = audit_trace.recover_export(
            export_id, audit_trace.RecoverPayload(attempt_no=2, receipt_id=d2["receipt_id"]))
        assert recovered["ok"] and recovered["attempt_no"] == 3

        # POST 补发入口：成功产物直接取数
        reissued = audit_trace.reissue_export(
            export_id, audit_trace.ReissuePayload(attempt_no=1))
        assert reissued["ok"] and reissued["reissued_from"] == "artifact"
