"""Application route registration."""

from fastapi import APIRouter
from app.api.routes.health import router as health_router
from app.api.routes.purchase_requests import router as purchase_requests_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(purchase_requests_router)
