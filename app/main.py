"""Fellowship Candidate Intelligence — web application (FastAPI)."""
from __future__ import annotations

import base64
import logging
import re
import secrets
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, jobs, llm
from app.examples import example_dirs, example_id, example_input
from app.export import dossier_workbook, summary_rows, summary_workbook
from app.pipeline import CandidateInput, dossier_path, list_dossiers, load
from app.sources import scopus, sjr

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

APP_DIR = Path(__file__).parent
ALLOWED = {".pdf", ".docx", ".doc", ".rtf", ".odt", ".txt"}
MAX_UPLOAD = config.MAX_FILE_MB * 1024 * 1024

app = FastAPI(title="Fellowship Candidate Intelligence")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Password-protect the site when APP_USERNAME / APP_PASSWORD are set."""
    if not (config.APP_USERNAME and config.APP_PASSWORD) or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            user, pwd = "", ""
        if secrets.compare_digest(user, config.APP_USERNAME) and secrets.compare_digest(pwd, config.APP_PASSWORD):
            return await call_next(request)
    return Response("Требуется авторизация", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="ITMO Fellowship", charset="UTF-8"'})

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

QUARTILE_ORDER = ["Q1", "Q2", "Q3", "Q4", "—"]


def _ctx(request: Request, **kw) -> dict:
    return {
        "request": request,
        "llm": config.llm_enabled(),
        "llm_label": llm.label(),
        "scopus_api": scopus.available(),
        "sjr_year": sjr.max_year(),
        "default_from": config.default_from_year(),
        "this_year": date.today().year,
        "max_file_mb": config.MAX_FILE_MB,
        **kw,
    }


# ------------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    dossiers = list_dossiers()
    built = {d.id for d in dossiers}
    examples = [{"name": f.name, "id": example_id(f), "built": example_id(f) in built} for f in example_dirs()]
    return templates.TemplateResponse(request, "index.html", _ctx(request, dossiers=dossiers, examples=examples))


@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request):
    return templates.TemplateResponse(request, "new.html", _ctx(request))


@app.get("/method", response_class=HTMLResponse)
def method(request: Request):
    return templates.TemplateResponse(request, "method.html", _ctx(request))


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return templates.TemplateResponse(request, "job.html", _ctx(request, job=job))


@app.get("/dossiers/{dossier_id}", response_class=HTMLResponse)
def dossier_page(request: Request, dossier_id: str):
    d = load(dossier_id)
    if not d:
        raise HTTPException(404, "Досье не найдено")
    max_year = max(d.metrics.pubs_by_year.values() or [0])
    by_year_q: dict[int, dict[str, int]] = {y: {q: 0 for q in QUARTILE_ORDER} for y in d.metrics.pubs_by_year}
    for p in d.publications:
        if p.year in by_year_q:
            q = p.quality.quartile if p.quality and p.quality.quartile else "—"
            by_year_q[p.year][q] += 1
    pub_types = sorted({p.pub_type for p in d.publications})
    return templates.TemplateResponse(request, "dossier.html", _ctx(
        request, d=d, summary=summary_rows(d), by_year_q=by_year_q, max_year=max_year,
        quartiles=QUARTILE_ORDER, pub_types=pub_types,
    ))


# ------------------------------------------------------------------------------ actions
def _save_upload(f: UploadFile | None, folder: Path) -> Path | None:
    if f is None or not f.filename:
        return None
    suffix = Path(f.filename).suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Неподдерживаемый формат файла: {f.filename}")
    data = f.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, f"Файл больше {config.MAX_FILE_MB} МБ: {f.filename}")
    safe = re.sub(r"[^\w.\-]+", "_", Path(f.filename).name)[:120] or f"file{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe
    path.write_bytes(data)
    return path


@app.post("/dossiers")
def create_dossier(
    cv: UploadFile | None = File(None),
    proposal: UploadFile | None = File(None),
    application: UploadFile | None = File(None),
    other: list[UploadFile] | None = File(None),
    full_name: str = Form(""),
    orcid: str = Form(""),
    scopus_id: str = Form(""),
    scholar: str = Form(""),
    h_index_scopus: str = Form(""),
    from_year: str = Form(""),
):
    folder = config.UPLOAD_DIR / uuid.uuid4().hex[:12]
    files: dict[str, list[Path]] = {}
    for kind, f in (("cv", cv), ("proposal", proposal), ("application", application)):
        p = _save_upload(f, folder)
        if p:
            files[kind] = [p]
    for f in other or []:
        p = _save_upload(f, folder)
        if p:
            files.setdefault("other", []).append(p)
    if not files and not (orcid or scholar or scopus_id):
        raise HTTPException(400, "Загрузите хотя бы CV или укажите ORCID / Google Scholar / Scopus.")
    h = int(h_index_scopus) if h_index_scopus.strip().isdigit() else None
    y0 = int(from_year) if from_year.strip().isdigit() and 1990 <= int(from_year) <= date.today().year else None
    inp = CandidateInput(files=files, full_name=full_name, orcid=orcid, scopus=scopus_id, scholar=scholar,
                         h_index_scopus_manual=h, from_year=y0)
    job = jobs.submit(inp, label=full_name or (cv.filename if cv and cv.filename else "кандидат"))
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.post("/examples/{eid}/build")
def build_example(eid: str):
    folder = next((f for f in example_dirs() if example_id(f) == eid), None)
    if not folder:
        raise HTTPException(404, "Пример не найден")
    job = jobs.submit(example_input(folder), label=folder.name)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.post("/dossiers/{dossier_id}/delete")
def delete_dossier(dossier_id: str):
    if load(dossier_id):
        dossier_path(dossier_id).unlink(missing_ok=True)
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------------- API
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    return job.to_dict()


@app.get("/api/dossiers")
def api_list():
    return [{"id": d.id, "name": d.basic.full_name, "track": d.basic.track, "created_at": d.created_at.isoformat()}
            for d in list_dossiers()]


@app.get("/api/dossiers/{dossier_id}")
def api_dossier(dossier_id: str):
    d = load(dossier_id)
    if not d:
        raise HTTPException(404)
    return JSONResponse(d.model_dump(mode="json"))


@app.get("/dossiers/{dossier_id}/export.xlsx")
def export_dossier(dossier_id: str):
    d = load(dossier_id)
    if not d:
        raise HTTPException(404)
    return Response(dossier_workbook(d), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="dossier-{d.id}.xlsx"'})


@app.get("/dossiers/{dossier_id}/export.json")
def export_dossier_json(dossier_id: str):
    """Readable, downloadable JSON (a download works the same in every browser, unlike a new tab)."""
    d = load(dossier_id)
    if not d:
        raise HTTPException(404)
    return Response(d.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="dossier-{d.id}.json"'})


@app.get("/export/candidates.xlsx")
def export_summary():
    ds = sorted(list_dossiers(), key=lambda d: d.basic.full_name)
    if not ds:
        raise HTTPException(404, "Нет досье для выгрузки")
    return Response(summary_workbook(ds), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="candidates.xlsx"'})


@app.get("/healthz")
def healthz():
    return {"ok": True, "llm": llm.label() or False, "scopus_api": scopus.available(), "sjr": sjr.max_year()}
