"""Router agregado de la version 1 de la API."""

from fastapi import APIRouter

from app.api.v1.routes import auth, brands, drug_types, health, locations, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(drug_types.router)
api_router.include_router(brands.router)
api_router.include_router(locations.router)
