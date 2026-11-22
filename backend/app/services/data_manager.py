"""跨模块共享数据管理器：读写 data/ 目录下的业务数据产物。

职责：
- 每个服务产出 domain_snapshot 后序列化到 data/{module}/snapshot.json
- 下游服务从 data/ 目录读取上游的标准化数据产物
- 审计记录写入 data/audit/
- 用量记录写入 data/usage/
- 导出任务结果写入 data/exports/
- API 注册快照写入 data/api/
- 订阅审批记录写入 data/subscriptions/
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 数据目录相对于仓库根；用函数动态解析，测试可通过 DATA_ROOT 环境变量切换。
_DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


def _data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", str(_DEFAULT_DATA_ROOT)))


# 兼容历史引用：模块级常量仍指向默认目录
DATA_ROOT = _DEFAULT_DATA_ROOT

# 东八区
TZ = timezone(timedelta(hours=8))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(TZ).isoformat()


# ========== API 注册快照 ==========


def write_api_snapshot(api_id: int, api_data: dict[str, Any]) -> None:
    """将单个 API 注册数据写入共享快照，供路由、缓存、限流等模块消费。"""
    snapshot = _read_json(_data_root() / "api" / "registry.json", {})
    key = str(api_id)
    api_data["_synced_at"] = _now_iso()
    snapshot[key] = api_data
    _write_json(_data_root() / "api" / "registry.json", snapshot)


def read_api_snapshot(api_id: int | None = None) -> dict[str, Any] | dict[str, dict[str, Any]]:
    """读取 API 注册快照。返回单个或全部。"""
    snapshot = _read_json(_data_root() / "api" / "registry.json", {})
    if api_id is not None:
        return snapshot.get(str(api_id), {})
    return snapshot


def remove_api_snapshot(api_id: int) -> None:
    """标记 API 为下线。"""
    snapshot = _read_json(_data_root() / "api" / "registry.json", {})
    key = str(api_id)
    if key in snapshot:
        snapshot[key]["status"] = "已下线"
        snapshot[key]["_deleted_at"] = _now_iso()
        _write_json(_data_root() / "api" / "registry.json", snapshot)


# ========== 订阅审批 ==========


def write_subscription(sub_id: int, sub_data: dict[str, Any]) -> None:
    """写入订阅申请记录到共享目录。"""
    record = {
        "subscription_id": sub_id,
        **sub_data,
        "_created_at": sub_data.get("_created_at", _now_iso()),
        "_updated_at": _now_iso(),
    }
    _write_json(_data_root() / "subscriptions" / f"{sub_id:06d}.json", record)


def read_subscription(sub_id: int) -> dict[str, Any]:
    return _read_json(_data_root() / "subscriptions" / f"{sub_id:06d}.json", {})


def list_subscriptions() -> list[dict[str, Any]]:
    """列出所有订阅记录。"""
    _ensure_dir(_data_root() / "subscriptions")
    records = []
    for f in sorted(_data_root().glob("subscriptions/*.json")):
        record = _read_json(f)
        if record:
            records.append(record)
    return records


def approve_subscription(sub_id: int, approved: bool, approver: str = "system") -> dict[str, Any]:
    """审批订阅，记录审批流程。"""
    record = read_subscription(sub_id)
    if not record:
        return {}
    record["approval_status"] = "已通过" if approved else "已驳回"
    record["approver"] = approver
    record["_approved_at"] = _now_iso()
    write_subscription(sub_id, record)
    return record


# ========== 用量计费 ==========


def write_usage(usage_id: int, usage_data: dict[str, Any]) -> None:
    """写入用量记录到共享目录。"""
    usage_data["_created_at"] = usage_data.get("_created_at", _now_iso())
    usage_data["_updated_at"] = _now_iso()
    _write_json(_data_root() / "usage" / f"{usage_id:06d}.json", usage_data)


def read_usage(usage_id: int) -> dict[str, Any]:
    return _read_json(_data_root() / "usage" / f"{usage_id:06d}.json", {})


def list_usage(billing_cycle: str | None = None) -> list[dict[str, Any]]:
    """列出用量记录，支持按计费周期过滤。"""
    _ensure_dir(_data_root() / "usage")
    records = []
    for f in sorted(_data_root().glob("usage/*.json")):
        record = _read_json(f)
        if record:
            if billing_cycle is None or record.get("billing_cycle") == billing_cycle:
                records.append(record)
    return records


def settle_billing(billing_cycle: str, use_auto_settlement: bool = True) -> dict[str, Any]:
    """结算指定计费周期，生成结算汇总。"""
    records = list_usage(billing_cycle)
    total_amount = sum(float(r.get("amount", 0)) for r in records)
    total_calls = sum(int(r.get("call_count", 0)) for r in records)
    settlement = {
        "billing_cycle": billing_cycle,
        "total_amount": total_amount,
        "total_calls": total_calls,
        "record_count": len(records),
        "settled_at": _now_iso(),
        "auto_settlement": use_auto_settlement,
    }
    _write_json(_data_root() / "usage" / f"_settlement_{billing_cycle}.json", settlement)

    for r in records:
        r["settlement_status"] = "已结算"
        r["_settled_at"] = _now_iso()
        write_usage(r.get("subscription_id", 0), r)
    return settlement


# ========== 调用审计 ==========


def write_audit(audit_id: int, audit_data: dict[str, Any]) -> None:
    """写入审计记录。"""
    audit_data["_logged_at"] = _now_iso()
    _write_json(_data_root() / "audit" / f"{audit_id:06d}.json", audit_data)


def read_audit(audit_id: int) -> dict[str, Any]:
    return _read_json(_data_root() / "audit" / f"{audit_id:06d}.json", {})


def list_audit(api_name: str | None = None, caller: str | None = None,
               start_time: str | None = None, end_time: str | None = None) -> list[dict[str, Any]]:
    """按条件查询审计记录。"""
    _ensure_dir(_data_root() / "audit")
    records = []
    for f in sorted(_data_root().glob("audit/*.json")):
        record = _read_json(f)
        if not record:
            continue
        if api_name and record.get("api_name") != api_name:
            continue
        if caller and record.get("caller") != caller:
            continue
        if start_time and record.get("_logged_at", "") < start_time:
            continue
        if end_time and record.get("_logged_at", "") > end_time:
            continue
        records.append(record)
    return records


def archive_audit_records(before_date: str) -> dict[str, Any]:
    """归档指定日期之前的审计记录。"""
    records = list_audit()
    archived = [r for r in records if r.get("_logged_at", "")[:10] < before_date]
    archive_data = {
        "archive_date": _now_iso(),
        "before_date": before_date,
        "archived_count": len(archived),
        "records": archived,
    }
    _write_json(_data_root() / "audit" / f"_archive_{before_date}.json", archive_data)
    # 标记已归档
    for r in archived:
        rid = r.get("audit_id", 0)
        r["status"] = "已归档"
        write_audit(rid, r)
    return archive_data


# ========== 导出任务 ==========


def write_export(export_id: int, export_data: dict[str, Any],
                 attempt_no: int | None = None) -> None:
    """写入导出任务结果。

    attempt_no 不为空时，额外写一份按执行尝试隔离的产物文件，
    避免同一任务号多次执行互相覆盖、无法分辨哪次执行仍有文件。
    """
    export_data["_created_at"] = export_data.get("_created_at", _now_iso())
    export_data["_updated_at"] = _now_iso()
    _write_json(_data_root() / "exports" / f"{export_id:06d}.json", export_data)
    # 同时生成一份导出 CSV
    _write_export_csv(export_id, export_data)
    if attempt_no is not None:
        export_data = dict(export_data)
        export_data["attempt_no"] = attempt_no
        attempt_stem = f"{export_id:06d}_A{attempt_no}"
        _write_json(_data_root() / "exports" / f"{attempt_stem}.json", export_data)
        _write_export_csv(attempt_stem, export_data, attempt_stem=attempt_stem)


def _write_export_csv(export_id: int, export_data: dict[str, Any],
                      attempt_stem: str | None = None) -> None:
    """根据导出数据生成 CSV 文件头。"""
    columns = export_data.get("columns", [])
    rows = export_data.get("rows", [])
    stem = attempt_stem or f"{export_id:06d}"
    csv_path = _data_root() / "exports" / f"{stem}.csv"
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(v) for v in row))
    csv_path.write_text("\n".join(lines), encoding="utf-8")


def read_export(export_id: int) -> dict[str, Any]:
    return _read_json(_data_root() / "exports" / f"{export_id:06d}.json", {})


def export_attempt_file(export_id: int, attempt_no: int) -> Path | None:
    """定位某次执行尝试的产物文件，文件不存在返回 None。"""
    path = _data_root() / "exports" / f"{export_id:06d}_A{attempt_no}.json"
    return path if path.exists() else None


def list_exports() -> list[dict[str, Any]]:
    _ensure_dir(_data_root() / "exports")
    records = []
    for f in sorted(_data_root().glob("exports/[0-9]*.json")):
        record = _read_json(f)
        if record:
            records.append(record)
    return records


# ========== 导出执行事件 ==========
#
# 同一任务号可能经历原执行、中断重跑、恢复等多次执行尝试。
# 每次尝试都留下独立事件（派发/成功/中断），事件按
# (task_no, attempt_no) 关联，并携带执行回执 receipt_id，
# 供调用审计做调用明细检索和恢复定位，禁止按任务号合并尝试。

EXPORT_EVENT_DISPATCHED = "export_dispatched"
EXPORT_EVENT_SUCCEEDED = "export_succeeded"
EXPORT_EVENT_INTERRUPTED = "export_interrupted"

EXPORT_EVENT_STAGES = {
    EXPORT_EVENT_DISPATCHED: "派发",
    EXPORT_EVENT_SUCCEEDED: "成功",
    EXPORT_EVENT_INTERRUPTED: "中断",
}


def next_export_event_id() -> int:
    existing = list(_data_root().glob("export_events/[0-9]*.json"))
    return max((int(f.stem) for f in existing), default=0) + 1


def write_export_event(event_id: int, event_data: dict[str, Any]) -> None:
    """写入一条导出执行事件。"""
    event_data["_logged_at"] = event_data.get("_logged_at", _now_iso())
    _write_json(_data_root() / "export_events" / f"{event_id:06d}.json", event_data)


def list_export_events(task_no: str | None = None,
                       receipt_id: str | None = None,
                       event: str | None = None) -> list[dict[str, Any]]:
    """按任务号、回执编号或事件类型查询导出执行事件。"""
    _ensure_dir(_data_root() / "export_events")
    records = []
    for f in sorted(_data_root().glob("export_events/[0-9]*.json")):
        record = _read_json(f)
        if not record:
            continue
        if task_no and record.get("task_no") != task_no:
            continue
        if receipt_id and record.get("receipt_id") != receipt_id:
            continue
        if event and record.get("event") != event:
            continue
        records.append(record)
    return records


# ========== 模块数据快照 ==========


def write_module_snapshot(module: str, snapshot: dict[str, Any]) -> None:
    """各服务将自己的 domain_snapshot 写入共享目录，供跨模块消费。"""
    snapshot["_snapshot_at"] = _now_iso()
    _write_json(_data_root() / module / "snapshot.json", snapshot)


def read_module_snapshot(module: str) -> dict[str, Any]:
    return _read_json(_data_root() / module / "snapshot.json", {})


def list_module_snapshots() -> list[dict[str, Any]]:
    """读取所有模块的快照汇总。"""
    snapshots = []
    for module_dir in _data_root().iterdir():
        if module_dir.is_dir() and not module_dir.name.startswith("_"):
            snap = _read_json(module_dir / "snapshot.json")
            if snap:
                snapshots.append(snap)
    return snapshots