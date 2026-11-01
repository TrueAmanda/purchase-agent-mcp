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
FINANCIAL = re.compile(r"orcamento|budget|limite|teto|gastar|spend|exceed|dolar|dollar|us\$|usd\b|\$|maxim[oa]|maximum|preco|price|valor|cost|moeda|currency|\b(?:por|for)\s+\d")
OTHER_CURRENCY = re.compile(r"r\$|brl\b|reais\b|eur\b|euros?\b|gbp\b|libras?\b|jpy\b|ienes?\b|yen\b|cad\b|aud\b|cny\b|yuan\b|pesos?\b|[€£¥]")
UNIT = re.compile(r"por\s+(?:unidade|produto|item)|cada\b|unitari[oa]|per\s+(?:unit|product|item)|each\b")
TOTAL = re.compile(r"\btotal\b|ao\s+todo|overall")
QUANTITY = re.compile(r"(?:\bexatamente|\bexactly|\bcomprar|\bcompre|\bbuy|\bpurchase|\bpreciso de|\bneed|\bquero|\bwant|\bquantidade|\bquantity)"
                      r"\s*(?:de\s+|e\s+|is\s+|:\s*)?(?:exatamente\s+|exactly\s+)?([+-]?\d+)\b(?![.,]\d)")
CATEGORY = re.compile(r"\b(?:categoria|category)\s+([a-z]+(?:[-_][a-z]+)*)\b")
EXACT_PRODUCT = re.compile(r"\b(?:modelo exato|produto exato|exact model|exact product)\s+([^,.;!?]+)")
AUTHORIZE = re.compile(
    r"(?:^|[.!?;,:]\s*|\b(?:e|entao|then|please|favor)\s+)(?:crie|registre|salve|gere|create|register|save|submit)\b"
    r"|\b(?:quero|desejo|solicito|pode|poderia|want|can you)\s+(?:(?:que|to|you to)\s+)?(?:crie|criar|registre|registrar|salve|salvar|create|register|save|submit)\b"
    r"|^\s*(?:compre|buy|purchase)\b")
DENY_WRITE = re.compile(r"\b(?:nao|nunca|sem|not|never|do not|don't)\s+(?:(?:quero|deve|must|to)\s+)?(?:crie|criar|registre|registrar|salve|salvar|gere|gerar|create|register|save|submit|buy|purchase)\b|\b(?:so|apenas|somente|only)\s+(?:pesquis\w*|consult\w*|research|search)\b")
# Language aliases describe product types, never product IDs, prices or availability.
PRODUCT_CATEGORIES = {
    "celular": "smartphones", "celulares": "smartphones", "smartphone": "smartphones",
    "smartphones": "smartphones", "laptop": "laptops", "laptops": "laptops",
    "notebook": "laptops", "notebooks": "laptops",
    "maquiagem": "beauty", "makeup": "beauty", "mascara de cilios": "beauty",
    "perfume": "fragrances", "perfumes": "fragrances", "fragrances": "fragrances",
    "cuidados com a pele": "skin-care", "skin care": "skin-care", "skin-care": "skin-care",
}
PRODUCT_ID = re.compile(r"\b(?:id|codigo|product(?:\s+id)?|produto(?:\s+(?:de\s+)?codigo)?)\s*[#:]?\s*(\d+)\b")


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
    authorized: bool = False
    ambiguities: set[str] = field(default_factory=set, repr=False)
    requested_ids: set[int] = field(default_factory=set, repr=False)

    @classmethod
    def from_messages(cls, history: list[dict], message: str):
        policy = cls()
        # Catalog text and assistant/tool output never become user authorization.
        texts = [entry["content"] for entry in history if entry.get("role") == "user"
                 and isinstance(entry.get("content"), str)] + [message]
        for source in texts:
            policy.update(source)
        return policy

    def update(self, source: str) -> None:
        policy = self
        if source:
            source = normalize(source)
            ids = PRODUCT_ID.findall(source)
            if ids:
                if any(len(value) > 10 for value in ids):
                    policy.ambiguities.add("product_id")
                else:
                    policy.requested_ids = {int(value) for value in ids}
                    policy.ambiguities.discard("product_id")
            if DENY_WRITE.search(source):
                policy.authorized = False
            elif AUTHORIZE.search(source):
                policy.authorized = True
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
                if ambiguous:
                    policy.ambiguities.add("money")
                else:
                    policy.ambiguities.discard("money")
                if not ambiguous:
                    policy.limits.update(current)
            quantities = set(QUANTITY.findall(source))
            if len(quantities) == 1:
                raw_quantity = quantities.pop()
                if len(raw_quantity) > 10 or not 0 <= int(raw_quantity) <= 2147483647:
                    policy.ambiguities.add("quantity")
                else:
                    policy.limits["quantity"] = int(raw_quantity)
                    policy.ambiguities.discard("quantity")
            elif len(quantities) > 1:
                policy.ambiguities.add("quantity")
            categories = set(CATEGORY.findall(source))
            if not categories:
                categories = {category for word, category in PRODUCT_CATEGORIES.items()
                              if re.search(r"\b" + word + r"\b", source)}
            if len(categories) == 1:
                policy.limits["category"] = categories.pop().replace("_", "-")
                policy.ambiguities.discard("category")
            elif len(categories) > 1:
                policy.ambiguities.add("category")
            exact = EXACT_PRODUCT.findall(source)
            if len(exact) == 1:
                policy.limits["product_name"] = exact[0].strip(' "\'')
                policy.ambiguities.discard("product_name")
            elif len(exact) > 1:
                policy.ambiguities.add("product_name")
            policy.clarification = bool(policy.ambiguities)

    def prompt(self):
        authorization = ("\nRuntime write authorization: creation is authorized for this one intent. "
                         if self.authorized else
                         "\nRuntime write authorization: creation is NOT authorized. Research is allowed; do not call criar_requisicao. ")
        if self.authorized and not self.target_known:
            authorization += ("Before creation, ask for an explicit official category, an exact product name "
                              "(modelo exato / exact model), or catalog product IDs. Do not guess an unrecognized product type. ")
        if self.authorized and "quantity" not in self.limits:
            authorization += "Before creation, ask for an explicit numeric quantity from the user; never supply a default. "
        if self.limits.get("quantity") == 0:
            return (authorization + "\nRuntime purchase guard: the user requested ZERO units, which is invalid. "
                    "Explain that quantity must be positive and ask for the intended quantity. "
                    "Do not call tools or substitute a positive quantity.")
        if self.clarification:
            return (authorization + "\nRuntime purchase guard: the user's financial/quantity constraints are ambiguous or unsupported. "
                    "Catalog reads remain available. Before any write, ask for explicit USD total and/or per-unit "
                    "limits and unambiguous quantities. Never invent or relax a limit.")
        if not self.limits:
            return authorization
        info = dict(self.limits)
        if self.limits.get("quantity", 0) > 0 and "max_total" in self.limits:
            ceiling = Decimal(self.limits["max_total"]) / self.limits["quantity"]
            if "max_unit_price" in self.limits:
                ceiling = min(ceiling, Decimal(self.limits["max_unit_price"]))
            info["max_unit_for_one_product"] = str(ceiling.quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        return (authorization + "\nRuntime purchase guard (USD; derived from user text, enforced before persistence): "
                + json.dumps(info) + ". max_total limits the entire requisition; max_unit_price limits EACH unit. "
                "Use backend budget_checks on catalog results to compare the actual subtotals with these limits. "
                "Those checks concern money only; stock, category and minimum quantities still apply. "
                "Choose only eligible products. Tool results with constraint errors mean nothing was saved.")

    @property
    def target_known(self) -> bool:
        return bool(self.requested_ids or self.limits.get("category") or self.limits.get("product_name"))

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
