"""HTTP contracts; persisted Decimal amounts are serialized as JSON strings."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class PurchaseMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "message": "Preciso de 3 teclados com orçamento total de US$ 300.",
        "include_tool_trace": False,
    }]})

    message: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=20000)]
    include_tool_trace: bool = Field(default=False, strict=True)
    conversation_id: UUID | None = Field(
        default=None, description="Send null to start a conversation, then its returned UUID to continue. Omit for a single independent request.",
    )


class PurchaseItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    product_id: str | None
    name: str | None = Field(validation_alias="product_name")
    description: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0, allow_inf_nan=False)
    subtotal: Decimal | None = Field(ge=0, allow_inf_nan=False)


class PurchaseRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    request_number: UUID | None
    status: Literal["registered"] = "registered"
    title: str
    requester_name: str
    created_at: datetime
    currency: str | None
    total: Decimal | None = Field(validation_alias="total_amount", ge=0, allow_inf_nan=False)
    items: list[PurchaseItemRead]


class ConfirmedPurchaseRequest(PurchaseRequestRead):
    replayed: bool


class ToolTraceEntry(BaseModel):
    order: int = Field(gt=0, description="One-based invocation order within this request; repeated names remain separate.")
    name: Literal["buscar_produtos", "detalhar_produto", "criar_requisicao"]
    status: Literal["success", "error", "timeout"]
    code: str | None = None
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    result: dict[str, int | bool] | None = Field(
        default=None, description="Safe counts and replay flag; no raw tool payload or personal data.",
    )


class PurchaseMessageResponse(BaseModel):
    status: Literal["created", "no_results", "response"]
    message: str = Field(description="Agent text; use status and requisitions to determine confirmed registration.")
    requisitions: list[ConfirmedPurchaseRequest] = Field(default_factory=list)
    tool_trace: list[ToolTraceEntry] | None = None
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def require_confirmation_for_created(self):
        if (self.status == "created") != bool(self.requisitions):
            raise ValueError("Created status requires confirmed persisted requisitions.")
        return self


class PurchaseRequestListResponse(BaseModel):
    requisitions: list[PurchaseRequestRead]
    limit: int
    offset: int
    has_more: bool


class APIErrorDetail(BaseModel):
    code: str
    message: str


class APIErrorResponse(BaseModel):
    detail: APIErrorDetail
    tool_trace: list[ToolTraceEntry] | None = None
    conversation_id: UUID | None = None


class InputValidationErrorResponse(BaseModel):
    detail: list[dict]
