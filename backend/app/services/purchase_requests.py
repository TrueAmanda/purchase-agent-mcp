"""Purchase API orchestration and reads only; all writes belong to the MCP tool."""

import logging
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError as SQLTimeoutError
from sqlalchemy.orm import Session, selectinload, sessionmaker
from starlette.concurrency import run_in_threadpool

from purchase_storage import PurchaseRequest
from app.schemas.agent import AgentResult, ToolExecutionEvent
from app.schemas.purchase_requests import (
    ConfirmedPurchaseRequest, PurchaseMessageResponse, PurchaseRequestListResponse,
    PurchaseRequestRead, ToolTraceEntry,
)

logger = logging.getLogger(__name__)

TOOL_FAILURES = {
    "api_timeout": (504, "DummyJSON timed out. Product validation could not be completed."),
    "api_unavailable": (503, "DummyJSON is unavailable. Product validation could not be completed."),
    "api_http_error": (502, "DummyJSON returned an HTTP error; this is not an empty catalog result."),
    "invalid_response": (502, "DummyJSON returned invalid product data."),
    "product_not_found": (422, "A requested product does not exist in DummyJSON."),
    "database_unavailable": (503, "Purchase storage is unavailable; check saved requisitions before retrying."),
    "database_error": (502, "Purchase registration is unconfirmed; check saved requisitions before retrying."),
    "idempotency_conflict": (409, "The idempotency key was already used for different purchase data."),
    "invalid_amount": (422, "The catalog amount exceeds supported storage precision."),
    "mcp_unavailable": (503, "MCP communication failed. A write may have completed; check saved requisitions before retrying."),
    "tool_timeout": (504, "A tool timed out. A write may have completed; check saved requisitions before retrying."),
}


def _error_code(event: ToolExecutionEvent) -> str:
    if event.outcome != "ok":
        return event.outcome
    error = (event.result or {}).get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in TOOL_FAILURES else "tool_execution_failed"


def _safe_summary(event: ToolExecutionEvent) -> dict | None:
    if _event_failed(event):
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


class PurchaseAPIError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(message)


class AgentRunner(Protocol):
    async def run(self, message: str) -> AgentResult: ...


class _CreationReceipt(BaseModel):
    request_id: int = Field(gt=0, strict=True)
    request_number: UUID
    replayed: bool = Field(strict=True)


def _unconfirmed() -> PurchaseAPIError:
    return PurchaseAPIError(
        "creation_unconfirmed",
        "Purchase registration was not confirmed. A write may have completed; check saved requisitions before retrying.",
        502,
    )


def _event_failed(event: ToolExecutionEvent) -> bool:
    data = event.result or {}
    return event.outcome != "ok" or "error" in data or data.get("isError") is True


class PurchaseRequestService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @staticmethod
    def _raise_read_error(error: Exception):
        if isinstance(error, (OperationalError, SQLTimeoutError)):
            code, status = "database_unavailable", 503
        elif isinstance(error, ValidationError):
            code, status = "invalid_persisted_data", 500
        else:
            code, status = "database_error", 500
        logger.error("purchase_read outcome=%s", code)
        raise PurchaseAPIError(code, "Unable to read persisted purchase requisitions.", status) from None

    def list_requests(self, limit: int, offset: int) -> PurchaseRequestListResponse:
        try:
            with self.sessions() as session:
                records = list(session.scalars(
                    select(PurchaseRequest).options(selectinload(PurchaseRequest.items))
                    .order_by(PurchaseRequest.created_at.desc(), PurchaseRequest.id.desc())
                    .offset(offset).limit(limit + 1)
                ))
                return PurchaseRequestListResponse(
                    requisitions=[PurchaseRequestRead.model_validate(record) for record in records[:limit]],
                    limit=limit, offset=offset, has_more=len(records) > limit,
                )
        except (SQLAlchemyError, ValidationError) as error:
            self._raise_read_error(error)

    def _confirm(self, receipts: list[_CreationReceipt]) -> list[ConfirmedPurchaseRequest]:
        confirmed = {}
        try:
            with self.sessions() as session:
                for receipt in receipts:
                    record = session.scalar(select(PurchaseRequest).where(
                        PurchaseRequest.id == receipt.request_id,
                        PurchaseRequest.request_number == str(receipt.request_number),
                    ).options(selectinload(PurchaseRequest.items)))
                    if record is None or record.total_amount is None or not record.items:
                        raise _unconfirmed()
                    snapshot = PurchaseRequestRead.model_validate(record)
                    # Never copy prices, totals or items from the LLM or tool receipt.
                    result = ConfirmedPurchaseRequest.model_validate({
                        **snapshot.model_dump(), "replayed": receipt.replayed,
                    })
                    previous = confirmed.get(record.id)
                    if previous is not None:
                        result.replayed = previous.replayed and result.replayed
                    confirmed[record.id] = result
            return list(confirmed.values())
        except (SQLAlchemyError, ValidationError) as error:
            self._raise_read_error(error)

    @staticmethod
    def _trace(events: list[ToolExecutionEvent]) -> list[ToolTraceEntry]:
        return [ToolTraceEntry(
            name=event.name,
            status="timeout" if _error_code(event) in {"tool_timeout", "api_timeout"} else (
                "success" if not _event_failed(event) else "error"
            ),
            code=None if not _event_failed(event) else _error_code(event),
            duration_ms=event.duration_ms, result=_safe_summary(event),
        ) for event in events]

    @staticmethod
    def _only_empty_searches(events: list[ToolExecutionEvent]) -> bool:
        searches = [event.result for event in events if event.name == "buscar_produtos" and event.outcome == "ok"]
        return bool(searches) and all(
            data and data.get("status") == "no_results" and data.get("products") == []
            and type(data.get("total")) is int and data["total"] == 0 for data in searches
        ) and not any(event.name == "detalhar_produto" and event.outcome == "ok" for event in events)

    async def submit(self, message: str, agent: AgentRunner, include_trace: bool) -> tuple[PurchaseMessageResponse, int]:
        result = await agent.run(message)
        events = result.tool_events
        receipts = []
        failed_events = []
        for event in events:
            failed = _event_failed(event)
            if failed:
                failed_events.append(event)
            elif event.name == "criar_requisicao":
                try:
                    receipts.append(_CreationReceipt.model_validate(event.result))
                except ValidationError:
                    raise _unconfirmed() from None
        if receipts:
            purchases = await run_in_threadpool(self._confirm, receipts)
            return PurchaseMessageResponse(
                status="created", message=result.content, requisitions=purchases,
                tool_trace=self._trace(events) if include_trace else None,
            ), 200 if all(purchase.replayed for purchase in purchases) else 201
        if failed_events:
            for event in failed_events:
                code = _error_code(event)
                if code in TOOL_FAILURES:
                    status, message = TOOL_FAILURES[code]
                    raise PurchaseAPIError(code, message, status)
            timed_out = any(event.outcome == "tool_timeout" for event in failed_events)
            raise PurchaseAPIError(
                "tool_timeout" if timed_out else "tool_execution_failed",
                "A tool operation could not be confirmed. A write may have completed; check saved requisitions before retrying.",
                504 if timed_out else 502,
            )
        return PurchaseMessageResponse(
            status="no_results" if self._only_empty_searches(events) else "response",
            message=result.content, tool_trace=self._trace(events) if include_trace else None,
        ), 200
