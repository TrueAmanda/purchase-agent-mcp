"""Opt-in, paid five-request evaluation against the running application.

No agent decisions are mocked. SQL is read-only; writes go through POST -> MCP.
A durable journal prevents the script from resubmitting an uncertain purchase.
"""

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select, text

from purchase_storage import PurchaseRequest, PurchaseRequestItem
from purchase_storage.config import Settings

TOOLS = {"buscar_produtos", "detalhar_produto", "criar_requisicao"}
CENT = Decimal("0.01")
CATALOG_URL = "https://dummyjson.com"


@dataclass
class Scenario:
    key: str
    label: str
    message: str
    title: str
    category: str | None = None
    quantity: int = 0
    budget: str = "0"
    positive: bool = False


def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid monetary amount")
    return result


def get_json(client, path, **kwargs):
    response = client.get(path, **kwargs)
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


def load_catalog(client):
    catalog = {}
    for category in ("laptops", "smartphones"):
        data = get_json(client, f"/products/category/{category}", params={"limit": 0})
        products = data["products"]
        if not products or len(products) != data["total"] or data["skip"] != 0:
            raise ValueError("Incomplete category inventory")
        for product in products:
            if product["category"] != category or amount(product["price"]) <= 0:
                raise ValueError("Invalid catalog category or price")
            catalog[str(product["id"])] = {key: product[key] for key in (
                "id", "title", "category", "price", "stock", "minimumOrderQuantity",
            )}
    return catalog


def make_scenarios(catalog, run_id):
    scenarios = []
    for category, label in (("laptops", "laptops"), ("smartphones", "smartphones")):
        eligible = [p for p in catalog.values() if p["category"] == category
                    and type(p.get("minimumOrderQuantity")) is int
                    and type(p.get("stock")) is int
                    and 0 < p["minimumOrderQuantity"] <= p["stock"]
                    and max(2, p["minimumOrderQuantity"]) <= p["stock"]]
        if not eligible:
            raise ValueError("No catalog product with confirmed stock and order minimum")
        candidate = min(eligible, key=lambda p: amount(p["price"]))
        quantity = max(2, candidate["minimumOrderQuantity"])
        budget = (amount(candidate["price"]) * quantity * Decimal("1.10")).quantize(CENT)
        title = f"Evaluation {run_id} {category}"
        scenarios.append(Scenario(
            category, f"Compra válida de {label}",
            f"Preciso comprar exatamente {quantity} {label}, da categoria {category}, "
            f"com orçamento TOTAL de US$ {budget}. Pesquise no catálogo e escolha "
            "uma opção que respeite o orçamento, o estoque e o pedido mínimo. "
            "Se atender, crie agora uma única requisição em nome de Evaluation Runner, "
            f'com título "{title}". Esta mensagem já autoriza a criação; não substitua a categoria.',
            title, category, quantity, str(budget), True,
        ))
    nonexistent = f"INEXISTENTE-QX-{run_id}"
    scenarios.extend([
        Scenario("nonexistent", "Produto inexistente", (
            f"Compre exatamente 2 unidades do produto de modelo exato {nonexistent}, "
            "até US$ 100 no total. Pesquise esse modelo e não aceite substitutos. "
            "Crie somente se esse produto exato existir, em nome de Evaluation Runner, "
            f'com título "Evaluation {run_id} nonexistent".'
        ), f"Evaluation {run_id} nonexistent"),
        Scenario("invalid_quantity", "Quantidade inválida", (
            "Quero comprar exatamente 0 smartphones, com orçamento total de US$ 1000. "
            "Não altere a quantidade. Crie a requisição em nome de Evaluation Runner, "
            f'com título "Evaluation {run_id} invalid_quantity".'
        ), f"Evaluation {run_id} invalid_quantity"),
        Scenario("impossible_budget", "Orçamento impossível", (
            "Preciso comprar exatamente 1 laptop da categoria laptops, com orçamento "
            "TOTAL de US$ 0.01. Pesquise o catálogo e crie somente se couber nesse "
            "orçamento, sem descontos não confirmados e sem substituir a categoria, "
            f'em nome de Evaluation Runner, com título "Evaluation {run_id} impossible_budget".'
        ), f"Evaluation {run_id} impossible_budget", "laptops", 1, "0.01"),
    ])
    return scenarios


class ReadOnlyStore:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def connection(self):
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                yield connection

    def ids(self):
        with self.connection() as connection:
            return list(connection.scalars(select(PurchaseRequest.id)))

    def since(self, baseline):
        with self.connection() as connection:
            query = select(PurchaseRequest.__table__).where(PurchaseRequest.id.not_in(baseline))
            records = []
            for row in connection.execute(query).mappings():
                items = list(connection.execute(select(PurchaseRequestItem.__table__).where(
                    PurchaseRequestItem.purchase_request_id == row["id"],
                ).order_by(PurchaseRequestItem.id)).mappings())
                records.append({
                    "id": row["id"], "request_number": row["request_number"],
                    "title": row["title"], "requester_name": row["requester_name"],
                    "currency": row["currency"], "total": str(row["total_amount"]),
                    "items": [{
                        "id": item["id"], "product_id": item["product_id"],
                        "name": item["product_name"], "quantity": item["quantity"],
                        "unit_price": str(item["unit_price"]), "subtotal": str(item["subtotal"]),
                    } for item in items],
                })
            orphan = connection.scalar(select(PurchaseRequestItem.id).outerjoin(
                PurchaseRequest, PurchaseRequest.id == PurchaseRequestItem.purchase_request_id,
            ).where(PurchaseRequest.id.is_(None)).limit(1))
            return records, orphan is not None


def list_api_records(client):
    records = []
    offset = 0
    # Bound the scan: a busy/shared database must not make evaluation unbounded.
    for _ in range(100):
        data = get_json(client, "/requisicoes", params={"limit": 100, "offset": offset})
        records.extend(data["requisitions"])
        if not data["has_more"]:
            return records
        offset += 100
    raise ValueError("GET pagination exceeded evaluation limit")


def judge(scenario, status_code, body, records, orphan, listed, catalog):
    """Score structured executor/API/SQL/catalog evidence, with explicit failures."""
    failures = []

    def check(condition, reason):
        if not condition:
            failures.append(reason)

    check(status_code in (200, 201), f"HTTP inesperado: {status_code}; serviço/execução não concluiu")
    if status_code not in (200, 201):
        check(not records and not body.get("requisitions"), "Houve gravação/confirmação apesar do erro HTTP")
        check(not orphan, "Existem itens órfãos no banco")
        return failures
    trace = body.get("tool_trace")
    check(isinstance(trace, list), "Histórico real de ferramentas ausente")
    trace = trace if isinstance(trace, list) else []
    for index, event in enumerate(trace, 1):
        check(event.get("order") == index and event.get("name") in TOOLS, "Nome/ordem de ferramenta inválido")
        check(event.get("status") == "success", f"Ferramenta {event.get('name')} falhou: {event.get('code')}")
        duration = event.get("duration_ms")
        check(type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
              "Duração de ferramenta ausente/inválida")
    names = [event.get("name") for event in trace]
    created = body.get("requisitions", [])
    check(isinstance(created, list), "Contrato requisitions inválido")
    created = created if isinstance(created, list) else []
    check(not orphan, "Existem itens órfãos no banco")
    if not scenario.positive:
        check(body.get("status") in ("response", "no_results"), "Contrato não confirmou uma resposta sem criação")
        check(not records and not created, "Pedido inválido/impossível gravou ou apresentou uma requisição")
        check("criar_requisicao" not in names, "Ferramenta de gravação foi chamada para pedido inválido/impossível")
        message = body.get("message", "")
        check(isinstance(message, str) and bool(message.strip()), "Explicação ao usuário ausente")
        if scenario.key in ("nonexistent", "impossible_budget"):
            check("buscar_produtos" in names, "Não houve consulta real ao catálogo")
        if scenario.key == "nonexistent":
            searches = [entry for entry in trace if entry.get("name") == "buscar_produtos"]
            check(bool(searches) and all((entry.get("result") or {}).get("product_count") == 0
                                        for entry in searches), "Busca não confirmou ausência de resultados")
        if scenario.key == "invalid_quantity":
            check(bool(re.search(r"quant|zero|positiv|maior|inválid|invalid", str(message), re.I)),
                  "A resposta não esclareceu o problema da quantidade")
        if scenario.key == "impossible_budget":
            check(bool(re.search(r"orçamento|orcamento|budget|0[.,]01|insuficiente", str(message), re.I)),
                  "A resposta não explicou a restrição de orçamento")
        return failures

    check(body.get("status") == "created" and len(created) == 1, "POST não confirmou exatamente uma requisição criada")
    check("buscar_produtos" in names and "criar_requisicao" in names, "Busca/criação MCP real ausente")
    check(names.count("criar_requisicao") == 1, "Esperada uma única execução de gravação")
    check(len(records) == 1, "SQL não confirmou exatamente uma nova requisição (ou houve outra gravação concorrente)")
    if len(records) != 1 or len(created) != 1:
        return failures
    record, receipt = records[0], created[0]
    check(record["title"] == scenario.title and record["requester_name"] == "Evaluation Runner",
          "Identificação da requisição diferente do pedido")
    check(bool(record["request_number"]) and receipt.get("request_number") == record["request_number"]
          and receipt.get("id") == record["id"], "Número/ID retornado não corresponde ao registro SQL")
    check(record["currency"] == receipt.get("currency") == "USD", "Moeda deve ser USD")
    check(receipt.get("status") == "registered" and receipt.get("replayed") is False,
          "Status/replay da confirmação inesperado")
    listed_record = next((row for row in listed if row.get("id") == record["id"]), None)
    check(listed_record is not None, "GET não lista o registro persistido")
    check(bool(record["items"]), "Requisição sem itens persistidos")
    total_quantity, calculated = 0, Decimal("0")
    per_product = {}
    for item in record["items"]:
        product = catalog.get(str(item["product_id"]))
        check(product is not None, f"Produto {item['product_id']} não confirmado na API externa")
        check(type(item["quantity"]) is int and item["quantity"] > 0, "Quantidade persistida inválida")
        if product is None:
            continue
        check(product["category"] == scenario.category, "Produto selecionado fora da categoria solicitada")
        check(item["name"] == product["title"], "Nome persistido diferente do catálogo")
        check(item["quantity"] <= product["stock"] and item["quantity"] >= product["minimumOrderQuantity"],
              "Quantidade não respeita estoque/pedido mínimo")
        price = amount(item["unit_price"])
        check(price == amount(product["price"]), "Preço persistido diferente da fonte externa (ou preço mudou)")
        subtotal = (price * item["quantity"]).quantize(CENT)
        check(amount(item["subtotal"]) == subtotal, "Subtotal diferente de preço × quantidade")
        calculated += subtotal
        total_quantity += item["quantity"]
        key = str(item["product_id"])
        per_product[key] = per_product.get(key, 0) + item["quantity"]
    for key, quantity in per_product.items():
        check(quantity <= catalog[key]["stock"], "Linhas repetidas ultrapassam o estoque agregado do produto")
    check(total_quantity == scenario.quantity, "Quantidade total diferente da solicitada")
    check(amount(record["total"]) == calculated == amount(receipt.get("total")), "Total SQL/POST diferente da soma dos itens")
    check(calculated <= amount(scenario.budget), f"Total US$ {calculated} excede orçamento de US$ {scenario.budget}")
    for api_record, origin in ((receipt, "POST"), (listed_record, "GET")):
        if api_record is None:
            continue
        check(all(api_record.get(key) == record[key] for key in ("request_number", "currency", "title", "requester_name")),
              f"{origin} diverge dos campos persistidos")
        check(amount(api_record.get("total")) == calculated, f"Total {origin} diferente do SQL")
        check(api_record.get("items") is not None and all(
            {key: row.get(key) for key in item} == item
            for row, item in zip(api_record.get("items", []), record["items"], strict=False)
        ) and len(api_record.get("items", [])) == len(record["items"]), f"Itens {origin} diferentes do SQL")
    return failures


def save_journal(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def journal_lock(path):
    lock = path.with_suffix(path.suffix + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def print_report(journal):
    passed = 0
    for entry in journal["cases"]:
        result = entry.get("result", {})
        success = entry["state"] == "done" and result.get("passed") is True
        passed += success
        print(f"{'PASS' if success else 'FAIL'} | {entry['scenario']['label']}")
        for reason in result.get("failures", ["Não executado ou resultado incerto; não reenviado por segurança"]):
            print(f"  - {reason}")
        if "http_status" in result:
            print(f"  HTTP {result['http_status']} | SQL: {result['new_request_numbers']} | MCP: "
                  + " -> ".join(event["name"] for event in result["tool_trace"]))
    total = len(journal["cases"])
    print(f"Acertos: {passed}/{total} ({passed / total * 100:.0f}%)")
    return 0 if passed == total else 1


def evaluate(args):
    state = args.state.resolve()
    state.parent.mkdir(parents=True, exist_ok=True)
    with journal_lock(state):
        if state.exists():
            journal = json.loads(state.read_text(encoding="utf-8"))
            print("Relatório salvo; nenhuma chamada foi reenviada. Para nova avaliação use outro --state.")
            return print_report(journal)
        if not args.execute:
            raise ValueError("Use --execute to authorize paid calls and test purchases")
        settings = Settings.from_environment()
        engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        try:
            store = ReadOnlyStore(engine)
            with httpx.Client(base_url=args.api_url, timeout=180, trust_env=False, follow_redirects=False) as api, \
                    httpx.Client(base_url=CATALOG_URL, timeout=15, trust_env=False) as external:
                get_json(api, "/health")
                catalog = load_catalog(external)
                run_id = str(uuid4())
                scenarios = make_scenarios(catalog, run_id)
                nonexistent = get_json(external, "/products/search", params={"q": f"INEXISTENTE-QX-{run_id}"})
                if nonexistent["total"] != 0 or any(amount(p["price"]) <= CENT for p in catalog.values()):
                    raise ValueError("Catalog no longer supports the negative evaluation criteria")
                journal = {
                    "version": 1, "run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
                    "model": os.getenv("OPENROUTER_MODEL", "unknown"),
                    "catalog_snapshot": catalog,
                    "cases": [{"scenario": asdict(s), "state": "pending"} for s in scenarios],
                }
                save_journal(state, journal)
                for entry, scenario in zip(journal["cases"], scenarios, strict=True):
                    print(f"Executando: {scenario.label}...", flush=True)
                    baseline = store.ids()
                    entry.update(state="running", baseline_ids=baseline)
                    # Must reach durable storage BEFORE submitting a potentially mutating POST.
                    save_journal(state, journal)
                    started = time.monotonic()
                    result = {"passed": False, "failures": [], "tool_trace": [], "new_request_numbers": []}
                    uncertain = False
                    try:
                        response = api.post("/requisicoes", json={"message": scenario.message, "include_tool_trace": True})
                        body = response.json()
                        if not isinstance(body, dict):
                            raise ValueError("Invalid API response contract")
                        result["http_status"] = response.status_code
                        result["response_status"] = body.get("status")
                        code = (body.get("detail") or {}).get("code") if isinstance(body.get("detail"), dict) else None
                        result["api_error_code"] = code if isinstance(code, str) and re.fullmatch(r"[a-z_]+", code) else None
                        if isinstance(body, dict) and isinstance(body.get("tool_trace"), list):
                            result["tool_trace"] = [{key: event.get(key) for key in (
                                "order", "name", "status", "code", "duration_ms", "result",
                            )} for event in body["tool_trace"]]
                        records, orphan = store.since(baseline)
                        result["new_request_numbers"] = [row["request_number"] for row in records]
                        result["persisted_evidence"] = [{key: row[key] for key in (
                            "id", "request_number", "currency", "total", "items",
                        )} for row in records]
                        verified_catalog = dict(catalog)
                        for record in records:
                            for item in record["items"]:
                                product = get_json(external, f"/products/{int(item['product_id'])}")
                                verified_catalog[str(product["id"])] = product
                        listed = list_api_records(api)
                        result["get_confirmed_ids"] = [row["id"] for row in records
                                                       if any(item["id"] == row["id"] for item in listed)]
                        result["failures"] = judge(
                            scenario, response.status_code, body, records, orphan,
                            listed, verified_catalog,
                        )
                        result["passed"] = not result["failures"]
                    except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation) as error:
                        result["failures"] = [f"Evidência incompleta/resultado incerto: {type(error).__name__}; não reenviar automaticamente"]
                        uncertain = True
                    # A SQL failure also stops the run: further writes would lack independent verification.
                    result["duration_seconds"] = round(time.monotonic() - started, 2)
                    entry.update(state="done", result=result)
                    save_journal(state, journal)
                    if uncertain:
                        # An in-flight write must not be attributed to the next scenario.
                        break
                journal["finished_at"] = datetime.now(timezone.utc).isoformat()
                save_journal(state, journal)
                return print_report(journal)
        finally:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://backend:8000")
    parser.add_argument("--state", type=Path, default=Path(".agent-evaluation/run.json"))
    parser.add_argument("--execute", action="store_true", help="Authorize paid real calls and test purchases")
    args = parser.parse_args()
    try:
        return evaluate(args)
    except Exception as error:
        # Never print exception messages/tracebacks: connection/provider errors can contain secrets.
        print(f"Avaliação interrompida: {type(error).__name__}. Confira serviços, configuração e diário local; nenhuma repetição automática.", file=sys.stderr)
        if args.state.exists():
            try:
                print_report(json.loads(args.state.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError):
                pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
