"""Router agregado de la version 1 de la API."""

from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    brands,
    doctors,
    drug_types,
    health,
    locations,
    medications,
    patients,
    reports,
    users,
    visits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(drug_types.router)
api_router.include_router(brands.router)
api_router.include_router(locations.router)
api_router.include_router(medications.router)
api_router.include_router(patients.router)
api_router.include_router(doctors.router)
api_router.include_router(visits.router)
api_router.include_router(reports.router)
