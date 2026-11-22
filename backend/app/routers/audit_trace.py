"""调用审计的调用明细检索与导出任务恢复/补发入口。

路由固定在通用资源路由 /api/{module} 之前注册，避免被动态模块路径抢占。
- GET  /api/call_audit/export-trace        按任务号/回执检索导出调用明细
- POST /api/export_task/{id}/recover       带检索到的请求进入恢复入口
- POST /api/export_task/{id}/reissue       按补发入口从产物取数或补采恢复
- GET  /api/export_task/{id}/attempts      查看任务的全部执行尝试
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import service_for

router = APIRouter(prefix="/api", tags=["调用审计-导出恢复"])


class TraceQuery(BaseModel):
    task_no: str | None = None
    receipt_ids: list[str] = Field(default_factory=list)


class DispatchPayload(BaseModel):
    resumed_from: int | None = None
    resume_receipt: str | None = None


class InterruptPayload(BaseModel):
    attempt_no: int | None = None
    reason: str = "执行中断"


class RecoverPayload(BaseModel):
    attempt_no: int
    receipt_id: str | None = None


class ReissuePayload(BaseModel):
    attempt_no: int
    receipt_id: str | None = None
    supplemental_inputs: list[str] = Field(default_factory=list)


@router.get("/call_audit/export-trace")
def trace_export_calls(task_no: str | None = None,
                       receipt_id: str | None = None,
                       receipts: str | None = None) -> dict[str, Any]:
    """调用明细检索：支持任务号或多个执行回执（逗号分隔）混合查询。"""
    receipt_ids: list[str] = []
    if receipt_id:
        receipt_ids.append(receipt_id)
    if receipts:
        receipt_ids.extend(part.strip() for part in receipts.split(",") if part.strip())
    service = service_for("call_audit")
    return service.trace_export_calls(task_no=task_no, receipt_ids=receipt_ids)


@router.get("/export_task/{entry_id}/attempts")
def list_export_attempts(entry_id: int) -> dict[str, Any]:
    """查看导出任务的每次执行尝试及派发/成功/中断事件。"""
    service = service_for("export_task")
    attempts = service.list_attempts(entry_id)
    return {"ok": True, "export_id": entry_id, "attempt_count": len(attempts), "attempts": attempts}


@router.post("/export_task/{entry_id}/dispatch")
def dispatch_export(entry_id: int, payload: DispatchPayload) -> dict[str, Any]:
    """派发一次新的执行尝试（重跑入口），生成尝试序号与执行回执。"""
    service = service_for("export_task")
    result = service.dispatch_export(
        entry_id, resumed_from=payload.resumed_from, resume_receipt=payload.resume_receipt)
    result.setdefault("ok", False)
    return result


@router.post("/export_task/{entry_id}/complete")
def complete_export(entry_id: int, payload: RecoverPayload) -> dict[str, Any]:
    """完成指定尝试：查询、脱敏后按尝试写产物文件并记录成功事件。"""
    service = service_for("export_task")
    result = service.execute_export(entry_id, payload.attempt_no)
    result.setdefault("ok", False)
    return result


@router.post("/export_task/{entry_id}/interrupt")
def interrupt_export(entry_id: int, payload: InterruptPayload) -> dict[str, Any]:
    """中断指定尝试：只记中断事件，不产出文件。"""
    service = service_for("export_task")
    result = service.interrupt_export(entry_id, payload.attempt_no, payload.reason)
    result.setdefault("ok", False)
    return result


@router.post("/export_task/{entry_id}/recover")
def recover_export(entry_id: int, payload: RecoverPayload) -> dict[str, Any]:
    """恢复入口：带着调用明细检索到的请求重跑指定尝试。"""
    service = service_for("export_task")
    result = service.recover_export(entry_id, payload.attempt_no, payload.receipt_id)
    result.setdefault("ok", False)
    return result


@router.post("/export_task/{entry_id}/reissue")
def reissue_export(entry_id: int, payload: ReissuePayload) -> dict[str, Any]:
    """补发入口：从确认的产物取数；中断缺输入时沿事件记录补采后恢复。"""
    service = service_for("export_task")
    result = service.reissue_export(
        entry_id, payload.attempt_no, payload.supplemental_inputs)
    result.setdefault("ok", False)
    return result
