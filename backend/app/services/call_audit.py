"""调用审计领域服务：管理API调用的审计记录、归档和查询追溯。

跨模块数据流：
- 消费 usage_billing 快照 → 用量异常触发审计核查
- 消费 subscription 共享记录 → 审批通过的订阅记录审计
- 消费 export_task 快照 → 导出操作记录审计
- 审计记录定期归档 → 写入 data/audit/ 共享目录
- 被 error_classify 消费 → 异常调用触发错误归类
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CallAuditService(BaseModuleService):
    MODULE = "call_audit"
    ENTITY = "调用记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "调用方", "目标API", "请求参数", "响应码", "响应耗时", "返回行数", "时间戳"]
    REQUIRED_FIELDS = ["记录编号", "调用方", "目标API"]
    STATUS_ORDER = ["已记录", "已校验", "已归档", "已过期"]
    ACTION_RULES = {"确认校验": "已校验", "提交归档": "已归档", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    def record_api_call(self, caller: str, api_name: str, params: str = "",
                        response_code: str = "200", latency_ms: float = 0.0,
                        result_rows: int = 0) -> dict[str, Any]:
        """记录一次API调用到审计日志。"""
        from app.services.data_manager import write_audit
        from app.store import store as s

        audit_rows = s.rows(self.MODULE)
        new_id = max((int(r.get("id", 0)) for r in audit_rows), default=0) + 1
        record_no = f"CALL-{new_id:04d}"

        audit_data = {
            "audit_id": new_id,
            "record_no": record_no,
            "caller": caller,
            "api_name": api_name,
            "request_params": params,
            "response_code": response_code,
            "latency_ms": latency_ms,
            "result_rows": result_rows,
            "timestamp": self._now_iso(),
        }
        write_audit(new_id, audit_data)

        # 同时记录到内存
        entry = {
            "id": new_id,
            "记录编号": record_no,
            "调用方": caller,
            "目标API": api_name,
            "请求参数": params,
            "响应码": response_code,
            "响应耗时": str(latency_ms),
            "返回行数": str(result_rows),
            "时间戳": self._now_iso(),
            "status": "已记录",
            "pending": True,
            "abnormal": response_code != "200",
        }
        audit_rows.append(entry)
        return entry

    def record_export_event(self, event_payload: dict[str, Any]) -> dict[str, Any]:
        """把导出执行事件镜像为一条调用审计记录，保留任务号/尝试序号/回执关联。"""
        from app.services.data_manager import write_audit

        audit_rows = store.rows(self.MODULE)
        new_id = max((int(r.get("id", 0)) for r in audit_rows), default=0) + 1
        record_no = f"CALL-{new_id:04d}"
        stage = event_payload.get("stage") or event_payload.get("event", "")

        audit_data = {
            "audit_id": new_id,
            "record_no": record_no,
            "caller": event_payload.get("caller", "export_task"),
            "api_name": event_payload.get("api_name", ""),
            "event": event_payload.get("event"),
            "stage": stage,
            "export_id": event_payload.get("export_id"),
            "task_no": event_payload.get("task_no"),
            "attempt_no": event_payload.get("attempt_no"),
            "receipt_id": event_payload.get("receipt_id"),
            "request_params": event_payload.get("query_params", ""),
            "timestamp": self._now_iso(),
        }
        write_audit(new_id, audit_data)

        entry = {
            "id": new_id,
            "记录编号": record_no,
            "调用方": audit_data["caller"],
            "目标API": audit_data["api_name"],
            "请求参数": audit_data["request_params"],
            "响应码": "200" if event_payload.get("event") != "export_interrupted" else "500",
            "响应耗时": "0",
            "返回行数": str(event_payload.get("rows_count", 0) or 0),
            "时间戳": audit_data["timestamp"],
            "任务编号": audit_data["task_no"],
            "尝试序号": str(audit_data["attempt_no"]),
            "执行回执": audit_data["receipt_id"],
            "事件阶段": stage,
            "status": "已记录",
            "pending": True,
            "abnormal": event_payload.get("event") == "export_interrupted",
        }
        audit_rows.append(entry)
        return entry

    def trace_export_calls(self, *, task_no: str | None = None,
                           receipt_ids: list[str] | None = None) -> dict[str, Any]:
        """调用明细检索：从导出执行事件找回同任务号各次执行尝试。

        - 同任务号的多次尝试分别成组，绝不因任务号相同合并成一次成功
        - 区分派发、成功、中断各自对应的取数尝试
        - 成功尝试标注产物文件，中断尝试标注可恢复及其来源请求
        - 支持一次携带多个回执（含另一任务的回执）混合查询
        """
        from app.services.data_manager import (
            EXPORT_EVENT_STAGES,
            export_attempt_file,
            list_export_events,
        )

        receipts = [r.strip() for r in (receipt_ids or []) if r and r.strip()]
        events = list_export_events(task_no=task_no)
        if receipts:
            wanted = set(receipts)
            events.extend(ev for ev in list_export_events() if ev.get("receipt_id") in wanted)

        # 任务号与回执两条查询路径可能命中同一事件，按 event_id 去重
        deduped: dict[int, dict[str, Any]] = {}
        for ev in events:
            deduped.setdefault(int(ev.get("event_id", 0)), ev)
        events = list(deduped.values())

        # 去重并按 (task_no, attempt_no) 分组
        grouped: dict[tuple[str, int], dict[str, Any]] = {}
        unknown_receipts = set(receipts)
        for ev in events:
            key = (ev.get("task_no", ""), int(ev.get("attempt_no", 0)))
            attempt = grouped.get(key)
            if attempt is None:
                attempt = {
                    "task_no": key[0],
                    "attempt_no": key[1],
                    "export_id": ev.get("export_id"),
                    "api_name": ev.get("api_name"),
                    "receipt_id": ev.get("receipt_id"),
                    "query_params": ev.get("query_params", ""),
                    "stages": [],
                    "events": [],
                    "dispatched": False,
                    "succeeded": False,
                    "interrupted": False,
                    "outcome": "派发",
                    "has_file": False,
                    "artifact_file": None,
                    "recoverable": False,
                    "resume_request": None,
                    "resumed_from_attempt": ev.get("resumed_from_attempt"),
                    "resume_receipt": ev.get("resume_receipt"),
                }
                grouped[key] = attempt
            stage = ev.get("stage") or EXPORT_EVENT_STAGES.get(ev.get("event", ""), "")
            attempt["stages"].append(stage)
            attempt["events"].append({
                "event": ev.get("event"),
                "stage": stage,
                "receipt_id": ev.get("receipt_id"),
                "logged_at": ev.get("_logged_at"),
                "reason": ev.get("reason"),
                "rows_count": ev.get("rows_count"),
            })
            if ev.get("event") == "export_dispatched":
                attempt["dispatched"] = True
            if ev.get("event") == "export_succeeded":
                attempt["succeeded"] = True
            if ev.get("event") == "export_interrupted":
                attempt["interrupted"] = True
            unknown_receipts.discard(ev.get("receipt_id"))

        attempts: list[dict[str, Any]] = []
        for (t_no, _no), attempt in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
            # 中断优先于成功：中断的尝试不能被后续同号事件算成成功
            if attempt["interrupted"]:
                attempt["outcome"] = "中断"
            elif attempt["succeeded"]:
                attempt["outcome"] = "成功"
            else:
                attempt["outcome"] = "派发"

            artifact = export_attempt_file(attempt["export_id"], attempt["attempt_no"])
            attempt["has_file"] = artifact is not None
            attempt["artifact_file"] = str(artifact) if artifact else None
            # 中断或缺文件的尝试仍可沿来源请求恢复
            attempt["recoverable"] = not attempt["has_file"]
            attempt["resume_request"] = {
                "export_id": attempt["export_id"],
                "task_no": t_no,
                "attempt_no": attempt["attempt_no"],
                "receipt_id": attempt["receipt_id"],
                "query_params": attempt["query_params"],
            }
            attempts.append(attempt)

        tasks: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            bucket = tasks.setdefault(attempt["task_no"], {
                "task_no": attempt["task_no"],
                "attempts": [],
                "dispatched_count": 0,
                "succeeded_count": 0,
                "interrupted_count": 0,
            })
            bucket["attempts"].append(attempt)
            bucket["dispatched_count"] += 1 if attempt["dispatched"] else 0
            bucket["succeeded_count"] += 1 if attempt["succeeded"] and not attempt["interrupted"] else 0
            bucket["interrupted_count"] += 1 if attempt["interrupted"] else 0

        return {
            "ok": True,
            "filter": {"task_no": task_no, "receipt_ids": receipts},
            "task_count": len(tasks),
            "attempt_count": len(attempts),
            "tasks": [tasks[key] for key in sorted(tasks)],
            "attempts": attempts,
            "unknown_receipts": sorted(unknown_receipts),
        }

    def _now_iso(self) -> str:
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone(timedelta(hours=8))).isoformat()

    def verify_audits(self, caller: str, api_name: str) -> list[dict[str, Any]]:
        """校验调用方对指定API的审计记录。"""
        from app.services.data_manager import list_audit, read_api_snapshot

        # 检查API是否已注册
        registry = read_api_snapshot()
        api_exists = any(
            isinstance(v, dict) and v.get("api_name") == api_name
            for v in (registry.values() if isinstance(registry, dict) else []))

        if not api_exists:
            return []

        audits = list_audit(api_name=api_name, caller=caller)
        # 校验订阅状态
        from app.services.data_manager import list_subscriptions
        subs = list_subscriptions()
        valid_sub = any(
            s.get("api_name") == api_name and s.get("applicant") == caller and s.get("approval_status") == "已通过"
            for s in subs
        )

        for audit in audits:
            audit["subscription_valid"] = valid_sub
            audit["verified"] = valid_sub
        return audits

    def archive_old_records(self, before_date: str) -> dict[str, Any]:
        """归档指定日期之前的审计记录。"""
        from app.services.data_manager import archive_audit_records
        return archive_audit_records(before_date)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "提交归档":
            self.archive_old_records(self._today_str())

        result = super().run_action(entry_id, action)
        from app.services.data_manager import write_module_snapshot
        write_module_snapshot(self.MODULE, self.domain_snapshot())
        return result

    def _today_str(self) -> str:
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    def domain_snapshot(self) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        audit_list = []
        for row in rows:
            audit_list.append({
                "id": row.get("id"),
                "record_no": row.get("记录编号"),
                "caller": row.get("调用方"),
                "api": row.get("目标API"),
                "params": row.get("请求参数"),
                "response_code": row.get("响应码"),
                "latency": row.get("响应耗时"),
                "result_rows": row.get("返回行数"),
                "timestamp": row.get("时间戳"),
                "status": row.get("status"),
            })
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(audit_list),
            "audits": audit_list,
            "archived_count": sum(1 for r in audit_list if r.get("status") == "已归档"),
            "abnormal_count": sum(1 for r in rows if r.get("abnormal")),
        }