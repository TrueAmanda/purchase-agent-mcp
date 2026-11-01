"""Public commercial facts come from executor/SQL evidence, never LLM prose."""

import re
from decimal import Decimal, ROUND_HALF_UP

from app.schemas.agent import AgentResult
from app.services.purchase_policy import PurchasePolicy
from app.services.tool_trace import event_failed


def english(message: str) -> bool:
    if re.search(r"\b(?:quero|preciso|compre|comprar|crie|busque|procure|orçamento|orcamento|requisição|requisicao|quantidade)\b", message, re.I):
        return False
    return bool(re.search(r"\b(?:i|want|need|please|would|could|buy|purchase|research|find|show|create|what|how|hello|detail|only)\b", message, re.I))


def confirmed_message(purchases: list, message: str) -> str:
    en = english(message)
    return "\n".join(
        (f"Requisition {p.request_number} registered. Total: {p.total} {p.currency or '(currency unknown)'}."
         if en else f"Requisição {p.request_number} registrada. Total: {p.total} {p.currency or '(moeda não informada)' }.")
        for p in purchases
    )


def grounded_message(result: AgentResult, *, empty_search: bool) -> str:
    en = english(result.user_message)
    if empty_search:
        return "No matches in the searches performed. Nothing was registered." if en else "Nenhum resultado nas buscas realizadas. Nenhuma requisição foi registrada."
    products = {}
    checks = []
    detailed = set()
    for event in result.tool_events:
        if event_failed(event) or not event.invoked:
            continue
        data = event.result or {}
        checks.extend((data.get("budget_checks") or {}).get("candidates", []))
        if event.name == "detalhar_produto" and isinstance(data.get("product"), dict):
            detailed.add(data["product"]["id"])
        values = data.get("products", []) if event.name == "buscar_produtos" else [data.get("product")]
        for product in values:
            if isinstance(product, dict) and product.get("name") and product.get("id") is not None and product.get("price") is not None:
                products[product["id"]] = product
    historic = not products and bool(result.catalog_products)
    if historic:
        products = {p["id"]: p for p in result.catalog_products if p.get("id") is not None and p.get("name") and p.get("price") is not None}
    if products:
        def within_budget(p):
            price = Decimal(str(p["price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            limits = result.purchase_limits
            return (("max_unit_price" not in limits or price <= Decimal(limits["max_unit_price"]))
                    and ("max_total" not in limits or not limits.get("quantity")
                         or price * limits["quantity"] <= Decimal(limits["max_total"])))

        eligible = [p for p in products.values() if within_budget(p)]
        excluded = len(eligible) != len(products)
        if not detailed and excluded:
            if not eligible:
                return ("None of the consulted products fits the budget for that quantity. Nothing was registered."
                        if en else "Nenhum dos produtos consultados cabe no orçamento para a quantidade informada. Nenhuma requisição foi registrada.")
            products = {p["id"]: p for p in eligible}
        lines = [("Previously consulted products:" if en else "Produtos consultados anteriormente:") if historic
                 else ("Consulted products:" if en else "Produtos consultados:")]
        if checks and all(row.get("within_budget") is False for row in checks):
            lines.insert(0, "None of the consulted products fits the budget for that quantity. Nothing was registered."
                         if en else "Nenhum dos produtos consultados cabe no orçamento para essa quantidade. Nenhuma requisição foi registrada.")
        labels = {"stock": "Stock" if en else "Estoque", "minimum_order_quantity": "Minimum order" if en else "Pedido mínimo",
                  "warranty_information": "Warranty" if en else "Garantia", "shipping_information": "Shipping" if en else "Envio"}
        for p in list(products.values())[:6]:
            lines.append(f"\n{p['name']} (ID {p['id']}): {p['price']} {p.get('currency') or '(moeda não informada)'}")
            for key, label in labels.items():
                if p.get(key) is not None:
                    lines.append(f"{label}: {p[key]}")
            if p["id"] in detailed:
                for key, pt in (("description", "Descrição"), ("category", "Categoria"), ("brand", "Marca"),
                                ("rating", "Avaliação"), ("return_policy", "Devolução")):
                    if p.get(key) is not None:
                        lines.append(f"{key if en else pt}: {p[key]}")
            image = p.get("thumbnail") or next(iter(p.get("images") or []), None)
            if image and not re.search(r"sem (?:foto|imag(?:em|ens))|no (?:photo|image)|without (?:photo|image)|(?:não|nao) (?:mostre|inclua|exiba|quero).{0,20}(?:foto|imag(?:em|ens))", result.user_message, re.I):
                lines.append(f"![{p['name']}]({image})")
        # Only clarifying questions may accompany facts; factual prose is discarded.
        question = planning_questions(result.content, en)
        if question:
            lines.append("\n" + question)
        return "\n".join(lines)
    policy = PurchasePolicy.from_messages([], result.user_message)
    if policy.limits.get("quantity") == 0:
        return "Quantity must be positive. How many units do you need?" if en else "A quantidade deve ser maior que zero. Quantas unidades você precisa?"
    if policy.clarification:
        return "Please clarify quantities and the USD total/per-unit budget. Nothing was registered." if en else "Esclareça as quantidades e o orçamento em US$ (total ou por unidade). Nenhuma requisição foi registrada."
    question = planning_questions(result.content, en)
    if question:
        return question
    return ("I can help plan purchases. No catalog fact or purchase registration was confirmed. What product, quantity and USD budget do you need?"
            if en else "Posso ajudar no planejamento de compras. Nenhum dado de catálogo ou registro foi confirmado. Informe o produto, a quantidade e o orçamento em US$.")


def planning_questions(text: str, en: bool) -> str:
    """The model may choose missing fields, but cannot embed factual claims in them."""
    if not text.strip().endswith("?"):
        return ""
    fields = [
        (r"quant|how many", "How many units do you need?", "Quantas unidades você precisa?"),
        (r"orcamento|orçamento|budget", "What is your USD budget, total or per unit?", "Qual é o orçamento em US$, total ou por unidade?"),
        (r"nome|name|requester|solicitante", "What is the requester's name?", "Qual é o nome do solicitante?"),
        (r"prefer|marca|brand", "Are there any essential product requirements?", "Há algum requisito essencial para o produto?"),
        (r"produto|product|equipment|equipamento", "What product do you want to buy?", "Qual produto você deseja comprar?"),
    ]
    return " ".join([
        english_text if en else portuguese_text for pattern, english_text, portuguese_text in fields
        if re.search(pattern, text, re.I)
    ][:2])
