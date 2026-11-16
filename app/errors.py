from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .domain import DomainError


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=422, content={"error": exc.code, "message": exc.message})
