"""Offline checks of scoring and replay guards; never run the paid evaluation."""

from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from evals import purchase_evaluation as evaluation


@pytest.fixture
def evidence():
    scenario = evaluation.Scenario("laptops", "Laptops", "Fixture request", "Evaluation fixture", "laptops", 3, "40", True)
    product = {"id": 7, "title": "Fixture laptop", "category": "laptops", "price": "10.10",
               "stock": 10, "minimumOrderQuantity": 1}
    record = {
        "id": 101, "request_number": "ca269ff0-71b8-464d-b7ad-c7916728369b", "title": scenario.title,
        "requester_name": "Evaluation Runner", "currency": "USD", "total": "30.30", "items": [{
            "id": 201, "product_id": "7", "name": product["title"], "quantity": 3,
            "unit_price": "10.10", "subtotal": "30.30",
        }],
    }
    receipt = deepcopy(record) | {"status": "registered", "replayed": False}
    body = {"status": "created", "message": "Registered", "requisitions": [receipt], "tool_trace": [
        {"order": 1, "name": "buscar_produtos", "status": "success", "duration_ms": 12, "result": {"product_count": 1}},
        {"order": 2, "name": "criar_requisicao", "status": "success", "duration_ms": 15, "result": {"item_count": 1}},
    ]}
    return dict(scenario=scenario, status_code=201, body=body, records=[record], orphan=False,
                listed=[deepcopy(receipt)], catalog={"7": product})


def test_complete_independent_evidence_passes_without_trusting_narrative(evidence):
    evidence["body"]["message"] = "Arbitrary LLM wording does not establish persistence."
    assert evaluation.judge(**evidence) == []


@pytest.mark.parametrize("fault", [
    "missing_sql", "duplicate_sql", "missing_get", "fictional_number", "wrong_category", "wrong_quantity",
    "wrong_price", "wrong_subtotal", "wrong_total", "over_budget", "missing_tools", "fake_tool",
    "wrong_order", "tool_error", "bad_duration", "wrong_currency", "insufficient_stock", "below_minimum",
    "partial_items", "orphan_items", "http_failure", "get_wrong_items", "post_wrong_total", "aggregate_stock",
])
def test_scoring_rejects_false_success(evidence, fault):
    record = evidence["records"][0]
    item = record["items"][0]
    product = evidence["catalog"]["7"]
    body = evidence["body"]
    if fault == "missing_sql":
        evidence["records"] = []
    elif fault == "duplicate_sql":
        evidence["records"].append(deepcopy(record))
    elif fault == "missing_get":
        evidence["listed"] = []
    elif fault == "fictional_number":
        body["requisitions"][0]["request_number"] = "made-up"
    elif fault == "wrong_category":
        product["category"] = "beauty"
    elif fault == "wrong_quantity":
        evidence["scenario"].quantity = 4
    elif fault == "wrong_price":
        product["price"] = "11.11"
    elif fault == "wrong_subtotal":
        item["subtotal"] = "30.31"
    elif fault == "wrong_total":
        record["total"] = "0"
    elif fault == "over_budget":
        evidence["scenario"].budget = "20"
    elif fault == "missing_tools":
        body["tool_trace"] = None
    elif fault == "fake_tool":
        body["tool_trace"][0]["name"] = "invented"
    elif fault == "wrong_order":
        body["tool_trace"][0]["order"] = 3
    elif fault == "tool_error":
        body["tool_trace"][0]["status"] = "error"
    elif fault == "bad_duration":
        body["tool_trace"][0]["duration_ms"] = float("nan")
    elif fault == "wrong_currency":
        record["currency"] = "BRL"
    elif fault == "insufficient_stock":
        product["stock"] = 2
    elif fault == "below_minimum":
        product["minimumOrderQuantity"] = 4
    elif fault == "partial_items":
        record["items"] = []
    elif fault == "orphan_items":
        evidence["orphan"] = True
    elif fault == "http_failure":
        evidence["status_code"] = 503
    elif fault == "get_wrong_items":
        evidence["listed"][0]["items"][0]["quantity"] = 999
    elif fault == "post_wrong_total":
        body["requisitions"][0]["total"] = "100"
    elif fault == "aggregate_stock":
        record["items"].append(deepcopy(item) | {"id": 202})
        record["total"] = "60.60"
        product["stock"] = 5
        evidence["scenario"].quantity = 6
        evidence["scenario"].budget = "100"
        for receipt in (body["requisitions"][0], evidence["listed"][0]):
            receipt.update(total="60.60", items=deepcopy(record["items"]))
    assert evaluation.judge(**evidence), fault


@pytest.mark.parametrize("key,message", [
    ("nonexistent", "Não encontrei resultados nas buscas realizadas."),
    ("invalid_quantity", "A quantidade deve ser maior que zero."),
    ("impossible_budget", "O orçamento é insuficiente."),
])
def test_negative_scenarios_require_no_write_and_relevant_evidence(key, message):
    body = {"status": "response", "message": message, "requisitions": [], "tool_trace": []}
    if key != "invalid_quantity":
        body["tool_trace"].append({"order": 1, "name": "buscar_produtos", "status": "success",
                                   "duration_ms": 1, "result": {"product_count": 0}})
    evidence = dict(scenario=evaluation.Scenario(key, key, "fixture", "fixture"), status_code=200,
                    body=body, records=[], orphan=False, listed=[], catalog={})
    assert evaluation.judge(**evidence) == []
    evidence["records"] = [{"id": 999}]
    assert evaluation.judge(**evidence)
    evidence["records"] = []
    body["tool_trace"].append({"order": len(body["tool_trace"]) + 1, "name": "criar_requisicao",
                               "status": "success", "duration_ms": 2})
    assert evaluation.judge(**evidence)


def test_no_paid_execution_without_explicit_flag(tmp_path):
    with pytest.raises(ValueError, match="--execute"):
        evaluation.evaluate(SimpleNamespace(state=tmp_path / "run.json", execute=False))
    assert not list(tmp_path.iterdir())


def test_journal_lock_prevents_concurrent_runs_and_preserves_owner(tmp_path):
    path = tmp_path / "run.json"
    with evaluation.journal_lock(path):
        with pytest.raises(FileExistsError):
            with evaluation.journal_lock(path):
                pytest.fail("Second evaluation must never acquire the lock")
        assert path.with_suffix(".json.lock").exists()
    assert not path.with_suffix(".json.lock").exists()


@pytest.mark.parametrize("state", ["done", "running", "pending"])
def test_saved_or_uncertain_cases_are_never_submitted_again(tmp_path, monkeypatch, state):
    path = tmp_path / "run.json"
    journal = {"cases": [{"scenario": {"label": str(i)}, "state": state,
                          "result": {"passed": state == "done", "failures": []}} for i in range(5)]}
    evaluation.save_journal(path, journal)

    def forbidden(*args, **kwargs):
        pytest.fail("Saved evaluation must not initialize HTTP or SQL")

    monkeypatch.setattr(evaluation.Settings, "from_environment", forbidden)
    monkeypatch.setattr(evaluation.httpx, "Client", forbidden)
    assert evaluation.evaluate(SimpleNamespace(state=path, execute=True)) == (0 if state == "done" else 1)
    assert json.loads(path.read_text(encoding="utf-8")) == journal


def test_preflight_uses_live_category_data_not_invented_prices():
    catalog = {
        "1": {"id": 1, "title": "Fixture laptop", "category": "laptops", "price": "400.15", "stock": 10, "minimumOrderQuantity": 3},
        "2": {"id": 2, "title": "Fixture phone", "category": "smartphones", "price": "100.01", "stock": 20, "minimumOrderQuantity": 1},
    }
    scenarios = evaluation.make_scenarios(catalog, "fixture-run")
    assert len(scenarios) == 5
    assert scenarios[0].quantity == 3
    assert scenarios[0].budget == "1320.50"
    assert scenarios[1].quantity == 2
    assert scenarios[1].budget == "220.02"
    assert "Evaluation Runner" in scenarios[0].message
    catalog["1"]["stock"] = 1
    with pytest.raises(ValueError, match="confirmed stock"):
        evaluation.make_scenarios(catalog, "fixture-run")


def test_category_snapshot_excludes_unnecessary_upstream_personal_data():
    def respond(request):
        category = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json={"total": 1, "skip": 0, "products": [{
            "id": 1 if category == "laptops" else 2, "title": "Fixture", "category": category,
            "price": 10, "stock": 5, "minimumOrderQuantity": 1,
            "reviews": [{"reviewerEmail": "private@example.test"}],
        }]})

    with httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond)) as client:
        catalog = evaluation.load_catalog(client)
    assert len(catalog) == 2
    assert "reviews" not in repr(catalog) and "private@example.test" not in repr(catalog)


@pytest.mark.parametrize("timeout", [False, True])
def test_runner_journals_before_post_and_never_retries(tmp_path, monkeypatch, timeout):
    path = tmp_path / "run.json"
    posts = []
    original_client = httpx.Client

    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path.startswith("/products/category/"):
            category = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"total": 1, "skip": 0, "products": [{
                "id": 1 if category == "laptops" else 2, "title": "Fixture", "category": category,
                "price": 10, "stock": 5, "minimumOrderQuantity": 1,
            }]})
        if request.url.path == "/products/search":
            return httpx.Response(200, json={"total": 0, "products": []})
        if request.method == "POST":
            journal = json.loads(path.read_text(encoding="utf-8"))
            assert journal["cases"][len(posts)]["state"] == "running"
            assert journal["cases"][len(posts)]["baseline_ids"] == [123]
            posts.append(json.loads(request.content))
            if timeout:
                raise httpx.ReadTimeout("Potentially mutating call has uncertain outcome", request=request)
            return httpx.Response(200, json={"status": "response", "message": "Quantidade/orçamento inválido.",
                                            "requisitions": [], "tool_trace": []})
        return httpx.Response(200, json={"requisitions": [], "has_more": False})

    class FakeStore:
        def __init__(self, engine):
            pass

        def ids(self):
            return [123]

        def since(self, baseline):
            return [], False

    monkeypatch.setattr(evaluation.Settings, "from_environment", lambda: SimpleNamespace(database_url="fixture"))
    monkeypatch.setattr(evaluation, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(evaluation, "ReadOnlyStore", FakeStore)
    monkeypatch.setattr(evaluation.httpx, "Client", lambda **kw: original_client(**kw, transport=httpx.MockTransport(respond)))
    args = SimpleNamespace(state=path, execute=True, api_url="https://backend.example.test")
    assert evaluation.evaluate(args) == 1
    assert len(posts) == (1 if timeout else 5)
    assert all("conversation_id" not in post and post["include_tool_trace"] is True for post in posts)
    journal = json.loads(path.read_text(encoding="utf-8"))
    assert len(journal["cases"]) == 5
    if timeout:
        assert journal["cases"][0]["result"]["passed"] is False
        assert all(entry["state"] == "pending" for entry in journal["cases"][1:])
    assert evaluation.evaluate(args) == 1
    assert len(posts) == (1 if timeout else 5)
