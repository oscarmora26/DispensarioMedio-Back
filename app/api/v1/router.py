"""Router agregado de la version 1 de la API."""

from fastapi import APIRouter

from app.api.v1.routes import auth, drug_types, health, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(drug_types.router)
