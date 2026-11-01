"""Conservative purchase limits grounded only in server-owned user messages.

This is a bounded parser, not a general natural-language interpreter. Ambiguous
money/scopes block writes and require clarification; reads remain available.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
import json
import re
import unicodedata


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold()) if not unicodedata.combining(c))


MONEY = re.compile(r"(?:us\$|usd\b|(?<![a-z])\$)\s*(?P<before>[+-]?\d+(?:[.,]\d+)*)"
                   r"|(?P<after>[+-]?\d+(?:[.,]\d+)*)\s*(?:usd\b|dolares?\b|dollars?\b)")
FINANCIAL = re.compile(r"orcamento|budget|limite|teto|gastar|spend|exceed|dolar|dollar|us\$|usd\b|\$|maxim[oa]|maximum")
OTHER_CURRENCY = re.compile(r"r\$|brl\b|reais\b|eur\b|euros?\b|gbp\b|libras?\b|[€£]")
UNIT = re.compile(r"por\s+(?:unidade|produto|item)|cada\b|unitari[oa]|per\s+(?:unit|product|item)|each\b")
TOTAL = re.compile(r"\btotal\b|ao\s+todo|overall")
QUANTITY = re.compile(r"(?:\bexatamente|\bexactly|\bcomprar|\bcompre|\bbuy|\bpurchase|\bpreciso de|\bneed)"
                      r"\s+(?:exatamente\s+|exactly\s+)?(\d+)\b(?![.,]\d)")
CATEGORY = re.compile(r"\b(?:categoria|category)\s+([a-z]+(?:[-_][a-z]+)*)\b")
EXACT_PRODUCT = re.compile(r"\b(?:modelo exato|produto exato|exact model|exact product)\s+([^,.;!?]+)")


def parse_amount(raw: str) -> Decimal:
    raw = raw.rstrip(".,")
    if raw.startswith("-"):
        raise ValueError("Negative budget")
    raw = raw.removeprefix("+")
    # Decimal cents and properly grouped thousands in either locale.
    if re.fullmatch(r"\d+", raw):
        canonical = raw
    elif re.fullmatch(r"\d+[.,]\d{1,2}", raw):
        canonical = raw.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:[.]\d{3})+(?:,\d{1,2})?", raw):
        canonical = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:[,]\d{3})+(?:\.\d{1,2})?", raw):
        canonical = raw.replace(",", "")
    else:
        raise ValueError("Ambiguous amount")
    value = Decimal(canonical)
    if value > Decimal("999999999999999999.99"):
        raise ValueError("Budget exceeds supported precision")
    return value.quantize(Decimal("0.01"))


@dataclass
class PurchasePolicy:
    limits: dict = field(default_factory=dict)
    clarification: bool = False

    @classmethod
    def from_messages(cls, history: list[dict], message: str):
        policy = cls()
        # Catalog text and assistant/tool output never become user authorization.
        texts = [entry["content"] for entry in history if entry.get("role") == "user"
                 and isinstance(entry.get("content"), str)] + [message]
        for source in texts:
            source = normalize(source)
            matches = list(MONEY.finditer(source))
            if FINANCIAL.search(source) or OTHER_CURRENCY.search(source):
                current = {}
                ambiguous = not matches or bool(OTHER_CURRENCY.search(source))
                for index, match in enumerate(matches):
                    before = source[max(matches[index - 1].end() if index else 0, match.start() - 90):match.start()]
                    before = re.split(r"\b(?:e|and)\b|[,;!?]", before)[-1]
                    after = source[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(source)]
                    if re.match(r"(?:[.,/]+\d|e[+-]?\d|\s*(?:mil|thousand|million)\b)", after):
                        ambiguous = True
                    # Scope belongs to the adjacent price clause, not a later sentence.
                    after = re.split(r"[.,;!?]|\b(?:e|and)\b", after, maxsplit=1)[0][:45]
                    unit = bool(UNIT.search(before) or UNIT.search(after))
                    total = bool(TOTAL.search(before) or TOTAL.search(after))
                    if unit and total:
                        ambiguous = True
                    kind = "max_unit_price" if unit else "max_total"
                    if not unit and not total and not re.search(r"orcamento|budget|teto|limite", before):
                        ambiguous = True
                    try:
                        value = str(parse_amount(match.group("before") or match.group("after")))
                        if kind in current and current[kind] != value:
                            ambiguous = True
                        current[kind] = value
                    except (InvalidOperation, ValueError):
                        ambiguous = True
                policy.clarification = ambiguous
                if not ambiguous:
                    policy.limits.update(current)
            quantities = set(QUANTITY.findall(source))
            if len(quantities) == 1:
                raw_quantity = quantities.pop()
                if len(raw_quantity) > 10 or int(raw_quantity) > 2147483647:
                    policy.clarification = True
                else:
                    policy.limits["quantity"] = int(raw_quantity)
            elif len(quantities) > 1:
                policy.clarification = True
            categories = set(CATEGORY.findall(source))
            if len(categories) == 1:
                policy.limits["category"] = categories.pop().replace("_", "-")
            elif len(categories) > 1:
                policy.clarification = True
            exact = EXACT_PRODUCT.findall(source)
            if len(exact) == 1:
                policy.limits["product_name"] = exact[0].strip(' "\'')
            elif len(exact) > 1:
                policy.clarification = True
        return policy

    def prompt(self):
        if self.limits.get("quantity") == 0:
            return ("\nRuntime purchase guard: the user requested ZERO units, which is invalid. "
                    "Explain that quantity must be positive and ask for the intended quantity. "
                    "Do not call tools or substitute a positive quantity.")
        if self.clarification:
            return ("\nRuntime purchase guard: the user's financial/quantity constraints are ambiguous or unsupported. "
                    "Catalog reads remain available. Before any write, ask for explicit USD total and/or per-unit "
                    "limits and unambiguous quantities. Never invent or relax a limit.")
        if not self.limits:
            return ""
        info = dict(self.limits)
        if self.limits.get("quantity", 0) > 0 and "max_total" in self.limits:
            ceiling = Decimal(self.limits["max_total"]) / self.limits["quantity"]
            if "max_unit_price" in self.limits:
                ceiling = min(ceiling, Decimal(self.limits["max_unit_price"]))
            info["max_unit_for_one_product"] = str(ceiling.quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        return ("\nRuntime purchase guard (USD; derived from user text, enforced before persistence): "
                + json.dumps(info) + ". max_total limits the entire requisition; max_unit_price limits EACH unit. "
                "Use backend budget_checks on catalog results to compare the actual subtotals with these limits. "
                "Those checks concern money only; stock, category and minimum quantities still apply. "
                "Choose only eligible products. Tool results with constraint errors mean nothing was saved.")

    def with_budget_checks(self, data: dict) -> dict:
        """Arithmetic evidence for selection; never chooses a product or calls a tool."""
        quantity = self.limits.get("quantity")
        if self.clarification or not quantity or not any(key in self.limits for key in ("max_total", "max_unit_price")):
            return data
        products = data.get("products", [data["product"]] if "product" in data else [])
        if not products:
            return data
        checks = []
        for product in products:
            price = Decimal(str(product["price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            subtotal = (price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            within = (("max_total" not in self.limits or subtotal <= Decimal(self.limits["max_total"]))
                      and ("max_unit_price" not in self.limits or price <= Decimal(self.limits["max_unit_price"])))
            checks.append({"product_id": product["id"], "quantity": quantity,
                           "subtotal": str(subtotal), "within_budget": within})
        return {**data, "budget_checks": {
            "source": "backend_decimal_calculation", "currency": "USD",
            "assumption": "Each row considers buying all requested units of that one product; other valid combinations may exist. Creation revalidates current prices.",
            "candidates": checks,
        }}
