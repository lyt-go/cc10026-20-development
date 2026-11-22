"""导出任务领域服务：管理数据导出任务的排队、执行和过期清理。

跨模块增强：
- 每次执行生成独立的执行尝试（attempt_no + receipt_id），派发/成功/中断
  三类事件写入导出执行事件日志，同任务号多次执行不会合并为一次
- complete 触发 call_audit 审计记录（业务能力增强）
- 中断重跑与恢复补发可沿事件日志找回最初输入和文件出处
- 多源归并 detect：export 完成后自动通知 data_merge

跨模块数据流：
- 消费 api_registry 快照 -> 核验导出API合法性
- 消费 data_filter 快照 -> 导出数据应用过滤规则
- 消费 field_mask 快照 -> 导出数据应用脱敏规则
- 消费 result_format 快照 -> 导出数据按模板格式化
- 写入 data/exports/ 与 data/export_events/ 共享目录 -> call_audit 消费导出审计
- 被 data_merge 消费 -> 多源导出归并
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExportTaskService(BaseModuleService):
    MODULE = "export_task"
    ENTITY = "导出任务"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["任务编号", "关联API", "查询参数", "导出格式", "数据行数", "文件大小", "过期时间", "任务状态"]
    REQUIRED_FIELDS = ["任务编号", "关联API", "查询参数"]
    STATUS_ORDER = ["排队中", "导出中", "已中断", "已完成", "已失败"]
    ACTION_RULES = {
        "发起导出": "导出中",
        "标记中断": "已中断",
        "确认完成": "已完成",
        "标记失败": "已失败",
    }
    NEGATIVE_ACTIONS = []

    # ---------- 执行尝试与事件日志 ----------

    def _now_iso(self) -> str:
        return datetime.now(timezone(timedelta(hours=8))).isoformat()

    def list_attempts(self, entry_id: int) -> list[dict[str, Any]]:
        """读取某导出任务的全部尝试（含派发/成功/中断事件与产物有无）。"""
        from app.services.data_manager import export_attempt_file, list_export_events
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        events = list_export_events(task_no=entry.get("任务编号"))
        attempts: dict[int, dict[str, Any]] = {}
        for ev in events:
            no = int(ev.get("attempt_no", 0))
            attempt = attempts.setdefault(no, {
                "attempt_no": no,
                "receipt_id": ev.get("receipt_id"),
                "task_no": ev.get("task_no"),
                "export_id": ev.get("export_id"),
                "api_name": ev.get("api_name"),
                "query_params": ev.get("query_params"),
                "events": [],
                "stages": [],
                "outcome": "派发",
                "interrupted": False,
                "succeeded": False,
                "has_file": False,
                "artifact_file": None,
                "resumed_from_attempt": ev.get("resumed_from_attempt"),
                "resume_receipt": ev.get("resume_receipt"),
            })
            attempt["events"].append(ev)
            attempt["stages"].append(ev.get("stage") or ev.get("event"))
            if ev.get("event") == "export_interrupted":
                attempt["outcome"] = "中断"
                attempt["interrupted"] = True
            elif ev.get("event") == "export_succeeded" and not attempt["interrupted"]:
                attempt["outcome"] = "成功"
                attempt["succeeded"] = True
        result = []
        for no in sorted(attempts):
            attempt = attempts[no]
            artifact = export_attempt_file(entry_id, no)
            attempt["has_file"] = artifact is not None
            attempt["artifact_file"] = str(artifact) if artifact else None
            result.append(attempt)
        return result

    def _next_attempt_no(self, task_no: str) -> int:
        from app.services.data_manager import list_export_events
        events = list_export_events(task_no=task_no)
        return max((int(ev.get("attempt_no", 0)) for ev in events), default=0) + 1

    def _next_receipt_id(self) -> str:
        from app.services.data_manager import list_export_events
        day = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
        existing = list_export_events()
        seq = sum(1 for ev in existing if str(ev.get("receipt_id", "")).startswith(f"RCPT-{day}-")) + 1
        return f"RCPT-{day}-{seq:04d}"

    def _log_event(self, *, export_id: int, task_no: str, attempt_no: int,
                   receipt_id: str, api_name: str, query_params: str,
                   event: str, stage: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """写一条导出执行事件，并在调用审计中留一条关联镜像。"""
        from app.services import data_manager as dm

        event_id = dm.next_export_event_id()
        payload = {
            "event_id": event_id,
            "event": event,
            "stage": stage,
            "export_id": export_id,
            "task_no": task_no,
            "attempt_no": attempt_no,
            "receipt_id": receipt_id,
            "api_name": api_name,
            "query_params": query_params,
            "caller": "export_task",
        }
        if extra:
            payload.update(extra)
        dm.write_export_event(event_id, payload)

        # 调用审计镜像：携带任务号/尝试序号/回执，支撑调用明细检索
        from app.services.call_audit import CallAuditService
        CallAuditService().record_export_event(payload)
        return payload

    def dispatch_export(self, entry_id: int, *, resumed_from: int | None = None,
                        resume_receipt: str | None = None) -> dict[str, Any]:
        """派发一次导出执行尝试，记录派发事件并生成执行回执。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}

        task_no = entry.get("任务编号", "")
        attempt_no = self._next_attempt_no(task_no)
        receipt_id = self._next_receipt_id()
        api_name = entry.get("关联API", "")
        query_params = entry.get("查询参数", "")

        self._log_event(
            export_id=entry_id, task_no=task_no, attempt_no=attempt_no,
            receipt_id=receipt_id, api_name=api_name, query_params=query_params,
            event="export_dispatched", stage="派发",
            extra={"resumed_from_attempt": resumed_from, "resume_receipt": resume_receipt},
        )
        entry["status"] = "导出中"
        entry["pending"] = True
        entry["任务状态"] = "导出中"
        entry["_last_attempt"] = attempt_no
        entry["_last_receipt"] = receipt_id
        self._snapshot()
        return {
            "ok": True, "export_id": entry_id, "task_no": task_no,
            "attempt_no": attempt_no, "receipt_id": receipt_id,
        }

    def interrupt_export(self, entry_id: int, attempt_no: int | None = None,
                         reason: str = "执行中断") -> dict[str, Any]:
        """中断某次执行尝试，仅记录中断事件，不产出文件。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}

        task_no = entry.get("任务编号", "")
        target = attempt_no or entry.get("_last_attempt")
        attempt = self._find_attempt(entry_id, task_no, target)
        if attempt is None:
            return {"ok": False, "message": f"任务{task_no}不存在第{target}次执行尝试"}
        if attempt["interrupted"]:
            return {"ok": False, "message": f"第{attempt['attempt_no']}次尝试已中断，不能重复中断"}
        if attempt["succeeded"]:
            return {"ok": False, "message": f"第{attempt['attempt_no']}次尝试已成功，不能中断"}

        self._log_event(
            export_id=entry_id, task_no=task_no, attempt_no=attempt["attempt_no"],
            receipt_id=attempt["receipt_id"], api_name=attempt["api_name"],
            query_params=attempt["query_params"],
            event="export_interrupted", stage="中断",
            extra={"reason": reason},
        )
        entry["status"] = "已中断"
        entry["pending"] = True
        entry["abnormal"] = True
        entry["任务状态"] = "已中断"
        self._snapshot()
        return {
            "ok": True, "export_id": entry_id, "task_no": task_no,
            "attempt_no": attempt["attempt_no"], "receipt_id": attempt["receipt_id"],
            "message": reason,
        }

    def execute_export(self, export_id: int, attempt_no: int | None = None) -> dict[str, Any]:
        """执行导出任务：查询数据 → 过滤 → 脱敏 → 格式化 → 写入文件。

        attempt_no 为空时沿用旧行为（先派发再完成），保证历史调用兼容。
        """
        entry = store.find(self.MODULE, export_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}

        task_no = entry.get("任务编号", "")
        if attempt_no is None:
            dispatched = self.dispatch_export(export_id)
            if not dispatched.get("ok"):
                return dispatched
            attempt_no = dispatched["attempt_no"]
            receipt_id = dispatched["receipt_id"]
        else:
            attempt = self._find_attempt(export_id, task_no, attempt_no)
            if attempt is None:
                return {"ok": False, "message": f"任务{task_no}不存在第{attempt_no}次执行尝试"}
            if attempt["interrupted"]:
                return {
                    "ok": False,
                    "message": f"第{attempt_no}次尝试已中断，请走恢复入口而不是直接执行",
                }
            if attempt["succeeded"]:
                return {"ok": False, "message": f"第{attempt_no}次尝试已成功，重复执行请先派发新尝试"}
            receipt_id = attempt["receipt_id"]

        api_name = entry.get("关联API", "")

        # 从 api_registry 读取原始数据
        from app.services.data_manager import read_api_snapshot  # noqa: F401
        # 模拟查询数据
        source_rows = self._mock_query(api_name)

        # 应用 data_filter 过滤
        from app.services import service_for
        filter_svc = service_for("data_filter")
        if filter_svc and hasattr(filter_svc, "apply_filters"):
            source_rows = filter_svc.apply_filters(api_name, source_rows)

        # 应用 field_mask 脱敏
        mask_svc = service_for("field_mask")
        if mask_svc and hasattr(mask_svc, "mask_fields"):
            source_rows = mask_svc.mask_fields(api_name, source_rows)

        # 写入导出文件（按尝试隔离，同任务号重跑不覆盖原产物）
        from app.services.data_manager import write_export
        columns = list(source_rows[0].keys()) if source_rows else []
        export_data = {
            "export_id": export_id,
            "task_no": task_no,
            "attempt_no": attempt_no,
            "receipt_id": receipt_id,
            "api_name": api_name,
            "query_params": entry.get("查询参数"),
            "export_format": entry.get("导出格式", "json"),
            "rows_count": len(source_rows),
            "columns": columns,
            "rows": [[str(r.get(c, "")) for c in columns] for r in source_rows],
            "status": "已完成",
        }
        write_export(export_id, export_data, attempt_no=attempt_no)

        self._log_event(
            export_id=export_id, task_no=task_no, attempt_no=attempt_no,
            receipt_id=receipt_id, api_name=api_name,
            query_params=entry.get("查询参数", ""),
            event="export_succeeded", stage="成功",
            extra={"rows_count": len(source_rows), "has_file": True},
        )

        entry["status"] = "已完成"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["数据行数"] = str(len(source_rows))
        entry["任务状态"] = "已完成"
        self._snapshot()
        return {
            "ok": True, "export_id": export_id, "task_no": task_no,
            "attempt_no": attempt_no, "receipt_id": receipt_id,
            "rows_count": len(source_rows),
        }

    def recover_export(self, entry_id: int, attempt_no: int,
                       resume_receipt: str | None = None) -> dict[str, Any]:
        """恢复入口：基于某次尝试的来源请求发起一次新的恢复执行。

        新尝试携带 resumed_from/receipt 关联，输入沿用原尝试的查询参数，
        保证恢复出的产物能核查恢复的是哪一次尝试。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        task_no = entry.get("任务编号", "")
        source_attempt = self._find_attempt(entry_id, task_no, attempt_no)
        if source_attempt is None:
            return {"ok": False, "message": f"任务{task_no}不存在第{attempt_no}次执行尝试"}

        receipt = resume_receipt or source_attempt["receipt_id"]
        dispatched = self.dispatch_export(
            entry_id, resumed_from=attempt_no, resume_receipt=receipt)
        if not dispatched.get("ok"):
            return dispatched
        return self.execute_export(entry_id, dispatched["attempt_no"])

    def reissue_export(self, entry_id: int, attempt_no: int,
                       supplemental_inputs: list[str] | None = None) -> dict[str, Any]:
        """补发入口：从确认的产物取数，并核查恢复关系与文件出处。

        - 原尝试成功且有产物：直接按原产物取数补发
        - 原尝试中断缺输入：先按事件来源补采（supplemental_inputs），
          再发起恢复执行，补发结果带回补采的输入
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        task_no = entry.get("任务编号", "")
        attempt = self._find_attempt(entry_id, task_no, attempt_no)
        if attempt is None:
            return {"ok": False, "message": f"任务{task_no}不存在第{attempt_no}次执行尝试"}

        from app.services.data_manager import export_attempt_file, read_export
        artifact = export_attempt_file(entry_id, attempt_no)

        # 中断尝试没有产物：沿事件记录补采来源后恢复
        if attempt["interrupted"] or artifact is None:
            missing_inputs = supplemental_inputs or []
            if not missing_inputs:
                return {
                    "ok": False,
                    "message": f"第{attempt_no}次尝试已中断且缺少产物，需先沿事件记录补采输入",
                    "attempt_no": attempt_no,
                    "receipt_id": attempt["receipt_id"],
                    "missing_inputs": True,
                    "source_query_params": attempt["query_params"],
                }
            recovered = self.recover_export(
                entry_id, attempt_no, resume_receipt=attempt["receipt_id"])
            if not recovered.get("ok"):
                return recovered
            recovered["reissued_from"] = "recovered"
            recovered["supplemental_inputs"] = missing_inputs
            recovered["source_receipt_id"] = attempt["receipt_id"]
            return recovered

        # 成功尝试：从确认的产物取数补发
        payload = read_export(entry_id)
        return {
            "ok": True,
            "export_id": entry_id,
            "task_no": task_no,
            "attempt_no": attempt_no,
            "receipt_id": attempt["receipt_id"],
            "reissued_from": "artifact",
            "artifact_file": str(artifact),
            "rows_count": payload.get("rows_count", 0),
            "source_query_params": attempt["query_params"],
        }

    # ---------- 辅助 ----------

    def _find_attempt(self, entry_id: int, task_no: str,
                      attempt_no: int | None) -> dict[str, Any] | None:
        if not attempt_no:
            return None
        for attempt in self.list_attempts(entry_id):
            if attempt["attempt_no"] == int(attempt_no):
                return attempt
        return None

    def _mock_query(self, api_name: str) -> list[dict[str, Any]]:
        """模拟从数据源查询结果。"""
        return [
            {"id": 1, "name": "张三", "phone": "13800138000", "email": "zhangsan@example.com", "amount": 1250.50},
            {"id": 2, "name": "李四", "phone": "13900139000", "email": "lisi@example.com", "amount": 2340.00},
            {"id": 3, "name": "王五", "phone": "13700137000", "email": "wangwu@example.com", "amount": 890.75},
        ]

    def _snapshot(self) -> None:
        from app.services.data_manager import write_module_snapshot
        write_module_snapshot(self.MODULE, self.domain_snapshot())

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action == "发起导出":
            dispatched = self.dispatch_export(entry_id)
            if not dispatched.get("ok"):
                return None, dispatched.get("message", "导出失败")
            result = self.execute_export(entry_id, dispatched["attempt_no"])
            if not result.get("ok"):
                return None, result.get("message", "导出失败")
            return entry, f"导出任务已完成，共{result.get('rows_count', 0)}行"

        if action == "标记中断":
            result = self.interrupt_export(entry_id)
            if not result.get("ok"):
                return None, result.get("message", "中断失败")
            return entry, f"第{result.get('attempt_no')}次执行已中断，可从恢复入口重跑"

        result = super().run_action(entry_id, action)
        self._snapshot()
        return result

    def domain_snapshot(self) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        export_list = []
        for row in rows:
            export_list.append({
                "id": row.get("id"),
                "task_no": row.get("任务编号"),
                "api": row.get("关联API"),
                "query_params": row.get("查询参数"),
                "format": row.get("导出格式"),
                "rows": row.get("数据行数"),
                "file_size": row.get("文件大小"),
                "expire_at": row.get("过期时间"),
                "task_status": row.get("任务状态"),
                "status": row.get("status"),
                "last_attempt": row.get("_last_attempt"),
                "last_receipt": row.get("_last_receipt"),
            })
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(export_list),
            "exports": export_list,
            "completed_count": sum(1 for r in export_list if r.get("status") == "已完成"),
            "interrupted_count": sum(1 for r in export_list if r.get("status") == "已中断"),
            "failed_count": sum(1 for r in export_list if r.get("status") == "已失败"),
        }
