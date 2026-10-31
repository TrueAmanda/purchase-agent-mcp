"""Synthetic tools for live prompt evaluations; no MCP, catalog API or database."""

from copy import deepcopy
from decimal import Decimal
from uuid import UUID

from app.core.agent_errors import ToolExecutionError
from app.schemas.agent import ToolDefinition


class PromptSandbox:
    """Deterministic tool facts, with model decisions left entirely to the real LLM."""

    request_number = "11111111-1111-4111-8111-111111111111"

    def __init__(self, scenario="normal"):
        self.scenario = scenario
        self.calls = []
        self.product = {
            "id": 1, "name": "Fixture keyboard", "description": "Synthetic evaluation product.",
            "price": "100.00", "currency": "BRL", "stock": 10,
        }
        if scenario == "missing":
            self.product = {"id": 1, "name": "Fixture keyboard", "price": "100.00"}
        self.definitions = [
            ToolDefinition(name="buscar_produtos", description="Search the evaluation catalog by term.", parameters={
                "type": "object", "properties": {"termo": {"type": "string", "minLength": 1}},
                "required": ["termo"], "additionalProperties": False,
            }),
            ToolDefinition(name="detalhar_produto", description="Get an evaluation product by positive ID.", parameters={
                "type": "object", "properties": {"id": {"type": "integer", "minimum": 1}},
                "required": ["id"], "additionalProperties": False,
            }),
            ToolDefinition(name="criar_requisicao", description=(
                "Persist a purchase request when creation is intended. Reuse the same idempotency_key on retries. "
                "Supply product IDs and quantities, never prices. Product prices are verified by this tool."
            ), parameters={
                "type": "object", "properties": {"request": {
                    "type": "object", "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "requester_name": {"type": "string", "minLength": 1},
                        "idempotency_key": {"type": "string", "format": "uuid"},
                        "items": {"type": "array", "minItems": 1, "items": {
                            "type": "object", "properties": {
                                "product_id": {"type": "integer", "minimum": 1},
                                "quantity": {"type": "integer", "minimum": 1},
                            }, "required": ["product_id", "quantity"], "additionalProperties": False,
                        }},
                    }, "required": ["title", "requester_name", "idempotency_key", "items"],
                    "additionalProperties": False,
                }}, "required": ["request"], "additionalProperties": False,
            }),
        ]

    async def list_tools(self):
        return [] if self.scenario == "no_tools" else deepcopy(self.definitions)

    async def call_tool(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if name == "buscar_produtos":
            if self.scenario == "search_error":
                raise ToolExecutionError("Synthetic catalog outage.")
            products = [] if self.scenario == "empty" else [deepcopy(self.product)]
            if "farinha" in arguments["termo"].lower():
                products = [{"id": 2, "name": "Fixture flour", "price": "10.00", "currency": "BRL"}]
            return {"status": "success" if products else "no_results", "products": products, "total": len(products)}
        if name == "detalhar_produto":
            if arguments["id"] != self.product["id"]:
                raise ToolExecutionError("Synthetic product not found.")
            return {"status": "success", "product": deepcopy(self.product)}
        if name == "criar_requisicao":
            data = arguments["request"]
            UUID(data["idempotency_key"])
            if self.scenario == "create_timeout":
                raise TimeoutError("Synthetic uncertain write outcome.")
            if self.scenario == "create_error":
                raise ToolExecutionError("Synthetic persistence outage.")
            if any(item["product_id"] != 1 for item in data["items"]):
                raise ToolExecutionError("Synthetic product not found.")
            items = [{
                **item, "unit_price": "100.00",
                "subtotal": str(Decimal("100.00") * item["quantity"]),
            } for item in data["items"]]
            return {
                "request_number": self.request_number, "items": items, "currency": "BRL",
                "total": str(sum(Decimal(item["subtotal"]) for item in items)),
            }
        raise AssertionError("Unadvertised evaluation tool.")
