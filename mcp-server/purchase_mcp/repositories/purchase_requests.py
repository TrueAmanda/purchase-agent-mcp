"""Atomic persistence and durable idempotency over the shared purchase models."""

import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from purchase_storage.models import PurchaseRequest, PurchaseRequestItem
from purchase_mcp.schemas.purchase_requests import PurchaseItemResult, PurchaseRequestInput, PurchaseRequestResult
from purchase_mcp.services.purchase_errors import PurchaseRequestError

logger = logging.getLogger(__name__)


class PurchaseRequestRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def check_connection(self) -> None:
        with self.sessions() as session:
            session.execute(text("SELECT 1"))

    def _lookup(self, session: Session, key: str, fingerprint: str) -> PurchaseRequestResult | None:
        record = session.scalar(select(PurchaseRequest).where(
            PurchaseRequest.idempotency_key == key
        ).options(selectinload(PurchaseRequest.items)))
        if record is None:
            return None
        if record.request_fingerprint != fingerprint:
            raise PurchaseRequestError("idempotency_conflict", "This key was already used for different request data.")
        return self._result(record, replayed=True)

    def find_existing(self, key: str, fingerprint: str) -> PurchaseRequestResult | None:
        try:
            with self.sessions() as session:
                return self._lookup(session, key, fingerprint)
        except SQLAlchemyError as error:
            self._raise_database_error(error)

    def create(
        self, request: PurchaseRequestInput, fingerprint: str, items: list[PurchaseItemResult]
    ) -> PurchaseRequestResult:
        key = str(request.idempotency_key)
        try:
            with self.sessions.begin() as session:
                existing = self._lookup(session, key, fingerprint)
                if existing is not None:
                    return existing
                record = PurchaseRequest(
                    request_number=str(uuid4()), idempotency_key=key, request_fingerprint=fingerprint,
                    title=request.title, requester_name=request.requester_name, currency=None,
                    total_amount=sum((item.subtotal for item in items), start=Decimal("0.00")),
                    items=[PurchaseRequestItem(
                        product_id=str(item.product_id), product_name=item.name, description=item.description,
                        quantity=item.quantity, unit_price=item.unit_price, subtotal=item.subtotal,
                    ) for item in items],
                )
                session.add(record)
                session.flush()
                result = self._result(record, replayed=False)
            # Return only after commit, including failures during COMMIT itself.
            return result
        except IntegrityError as error:
            # A concurrent request may have committed the same key while we inserted.
            existing = self.find_existing(key, fingerprint)
            if existing is not None:
                return existing
            self._raise_database_error(error)
        except SQLAlchemyError as error:
            self._raise_database_error(error)

    @staticmethod
    def _result(record: PurchaseRequest, *, replayed: bool) -> PurchaseRequestResult:
        return PurchaseRequestResult(
            request_id=record.id, request_number=record.request_number, created_at=record.created_at,
            replayed=replayed, currency=record.currency, total=record.total_amount,
            items=[PurchaseItemResult(
                product_id=int(item.product_id), name=item.product_name, description=item.description,
                quantity=item.quantity, unit_price=item.unit_price, subtotal=item.subtotal,
            ) for item in record.items],
        )

    @staticmethod
    def _raise_database_error(error: SQLAlchemyError) -> None:
        code = "database_unavailable" if isinstance(error, (OperationalError, TimeoutError)) else "database_error"
        logger.error("Purchase persistence failed: code=%s", code)
        raise PurchaseRequestError(code, "Unable to persist or retrieve the request. Retry with the same idempotency key.") from None
