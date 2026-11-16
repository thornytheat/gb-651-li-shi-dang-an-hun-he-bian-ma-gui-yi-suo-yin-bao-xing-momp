from fastapi import APIRouter, Body, Request
from .service import execute
from . import review
router = APIRouter(tags=["M2-编码判定"])


def _store(request: Request):
    return request.app.state.review_store


@router.get("/encoding")
def page(request: Request):
    data = execute()
    files = _store(request).list_files()
    reconcile = review.reconcile(_store(request)) if files else {"files": [], "reason": "OK"}
    return request.app.state.templates.TemplateResponse(
        request, "m2_encoding.html",
        {"title": "编码判定", "data": data, "review_files": files, "reconcile": reconcile})


@router.post("/api/encoding/detect")
def api(payload: dict = Body(default={})):
    return execute(payload or None)


# ---- 编码复核（按文件留痕、版本对账、可续跑发布）----

@router.post("/api/encoding/review/seed")
def review_seed(request: Request):
    return review.seed(_store(request))


@router.post("/api/encoding/review/files")
def review_register(request: Request, payload: dict = Body(...)):
    return review.register(_store(request), payload)


@router.get("/api/encoding/review/files")
def review_files(request: Request):
    return {"files": _store(request).list_files()}


@router.get("/api/encoding/review/files/{file_id}")
def review_probe(request: Request, file_id: str):
    return review.probe(_store(request), file_id)


@router.post("/api/encoding/review/decisions")
def review_decision(request: Request, payload: dict = Body(...)):
    return review.review(_store(request), payload)


@router.post("/api/encoding/review/publish")
def review_publish(request: Request, payload: dict = Body(default={})):
    return review.publish(_store(request), payload or {})


@router.get("/api/encoding/review/publish/{run_id}")
def review_publish_status(request: Request, run_id: str):
    return _store(request).get_publish_run(run_id)


@router.get("/api/encoding/review/reconcile")
def review_reconcile(request: Request):
    return review.reconcile(_store(request))
