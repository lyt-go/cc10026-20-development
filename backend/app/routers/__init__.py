"""业务路由汇总：审计检索/恢复入口需在通用资源路由之前注册。"""
from __future__ import annotations

from app.routers import audit_trace, resource

ROUTERS = [audit_trace, resource]
