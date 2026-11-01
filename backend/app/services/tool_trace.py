"""Allowlisted public projection of executor events; no LLM-text inference."""

from app.schemas.agent import ToolExecutionEvent
from app.schemas.purchase_requests import ToolTraceEntry

TOOL_NAMES = frozenset({"buscar_produtos", "detalhar_produto", "criar_requisicao"})
ERROR_CODES = frozenset({
    "api_timeout", "api_unavailable", "api_http_error", "invalid_response", "product_not_found",
    "database_unavailable", "database_error", "idempotency_conflict", "invalid_amount",
    "unknown_tool", "invalid_tool_arguments", "tool_timeout", "tool_cancelled",
    "tool_execution_failed", "mcp_unavailable", "invalid_tool_result",
    "budget_exceeded", "purchase_constraints_violated", "purchase_constraints_unclear",
})


def event_failed(event: ToolExecutionEvent) -> bool:
    data = event.result or {}
    return event.outcome != "ok" or "error" in data or data.get("isError") is True


def error_code(event: ToolExecutionEvent) -> str:
    if event.outcome != "ok":
        return event.outcome if event.outcome in ERROR_CODES else "tool_execution_failed"
    error = (event.result or {}).get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in ERROR_CODES else "tool_execution_failed"


def safe_summary(event: ToolExecutionEvent) -> dict | None:
    if event_failed(event):
        return None
    data = event.result or {}
    if event.name == "buscar_produtos" and isinstance(data.get("products"), list):
        return {"product_count": len(data["products"])}
    if event.name == "detalhar_produto" and isinstance(data.get("product"), dict):
        return {"product_count": 1}
    if event.name == "criar_requisicao" and isinstance(data.get("items"), list):
        summary = {"item_count": len(data["items"])}
        if type(data.get("replayed")) is bool:
            summary["replayed"] = data["replayed"]
        return summary
    return None


def build_tool_trace(events: list[ToolExecutionEvent]) -> list[ToolTraceEntry]:
    invoked = (event for event in events if event.invoked and event.name in TOOL_NAMES)
    return [ToolTraceEntry(
        order=order, name=event.name,
        status="timeout" if event_failed(event) and error_code(event) in {"tool_timeout", "api_timeout"}
        else ("error" if event_failed(event) else "success"),
        code=error_code(event) if event_failed(event) else None,
        duration_ms=event.duration_ms, result=safe_summary(event),
    ) for order, event in enumerate(invoked, start=1)]
