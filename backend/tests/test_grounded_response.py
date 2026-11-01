"""Visible commercial facts use evidence, including budgets and prior tool data."""

from decimal import Decimal
from types import SimpleNamespace

from app.schemas.agent import AgentResult, ToolExecutionEvent
from app.services.grounded_response import confirmed_message, grounded_message


def result(products, limits=None):
    return AgentResult(content="Invented offer for USD 0.01.", user_message="Procure perfumes.", iterations=2, tool_calls=1,
                       purchase_limits=limits or {}, tool_events=[ToolExecutionEvent(name="buscar_produtos", outcome="ok",
                       result={"products": products})])


def test_unit_budget_filters_visible_offers_using_actual_prices_without_a_quantity():
    answer = grounded_message(result([{"id": 1, "name": "Within", "price": "99.99", "currency": "USD"},
                                      {"id": 2, "name": "Too expensive", "price": "100.01", "currency": "USD"}],
                                     {"max_unit_price": "100.00"}), empty_search=False)
    assert "Within" in answer and "99.99" in answer
    assert "Too expensive" not in answer and "Invented" not in answer


def test_impossible_total_budget_is_explained_without_claiming_an_empty_catalog():
    answer = grounded_message(result([{"id": 1, "name": "Real", "price": "19.99"}],
                                     {"max_total": "30.00", "quantity": 2}), empty_search=False)
    assert "orçamento" in answer and "Nenhuma requisição" in answer
    assert "Nenhum resultado" not in answer


def test_prior_catalog_data_can_be_shown_without_fabricating_new_invocations():
    data = AgentResult(content="Fake product USD 0.01.", user_message="Quero detalhes.", iterations=1, tool_calls=0,
                       catalog_products=[{"id": 1, "name": "Previously verified", "price": "19.99", "currency": "USD"}])
    answer = grounded_message(data, empty_search=False)
    assert "anteriormente" in answer and "Previously verified" in answer and "19.99" in answer
    assert not data.tool_events and "Fake product" not in answer


def test_confirmation_text_uses_sql_number_and_total_instead_of_provider_prose():
    data = SimpleNamespace(request_number="actual-number", total=Decimal("19.99"), currency="USD")
    assert confirmed_message([data], "Compre") == "Requisição actual-number registrada. Total: 19.99 USD."


def test_question_cannot_smuggle_a_fake_creation_or_product_claim():
    data = AgentResult(content="Qual é o nome do produto FAKE que eu já registrei?", iterations=1, tool_calls=0)
    answer = grounded_message(data, empty_search=False)
    assert "FAKE" not in answer and "registrei" not in answer
    assert "nome do solicitante" in answer


def test_verified_images_can_be_omitted_when_requested_by_the_user():
    data = result([{"id": 1, "name": "Real", "price": "19.99", "thumbnail": "https://cdn.dummyjson.com/product-images/real/thumbnail.webp"}])
    data.user_message = "Quero pesquisar, não inclua imagens."
    assert "![" not in grounded_message(data, empty_search=False)
