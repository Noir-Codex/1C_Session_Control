from __future__ import annotations

import base64
import logging
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import RasServer
from onec.discovery import DiscoveryService
from onec.rac import RacError
from web.auth import ClusterCredentials
from web.companies import AssignedInfobase, Company, CompanyStoreError

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
AUTH_COOKIE = "session_control_auth"
AUTH_MAX_AGE = 8 * 60 * 60


def _server_with_credentials(server: RasServer, credentials: ClusterCredentials) -> RasServer:
    return replace(
        server,
        cluster_user=credentials.username,
        cluster_password=credentials.password,
    )


def _validate_credentials(request: Request, credentials: ClusterCredentials) -> None:
    clusters_checked = 0
    for configured_server in request.app.state.settings.servers:
        server = _server_with_credentials(configured_server, credentials)
        for item in request.app.state.rac.list_clusters(server.endpoint):
            cluster_id = item.get("cluster", "")
            if not cluster_id:
                continue
            request.app.state.rac.list_infobases(
                server.endpoint,
                cluster_id,
                server.cluster_user,
                server.cluster_password,
            )
            clusters_checked += 1
    if clusters_checked == 0:
        raise RacError("RAS не вернул ни одного доступного кластера")


def _collect(
    request: Request,
    credentials: ClusterCredentials,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    service = DiscoveryService(request.app.state.rac)
    snapshots: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for configured_server in request.app.state.settings.servers:
        server = _server_with_credentials(configured_server, credentials)
        try:
            snapshots.append(asdict(service.collect(server)))
        except RacError as exc:
            log.error("RAS %s недоступен: %s", server.endpoint, exc)
            errors.append({"server": server.name, "endpoint": server.endpoint, "error": str(exc)})
    return snapshots, errors


def _base_key(server_endpoint: str, cluster_id: str, infobase_id: str) -> str:
    raw = "\0".join((server_endpoint, cluster_id, infobase_id)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _available_bases(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for server in snapshots:
        endpoint = str(server["endpoint"])
        server_name = str(server["server_name"])
        for cluster_snapshot in server["clusters"]:
            cluster = cluster_snapshot["cluster"]
            cluster_id = str(cluster["id"])
            cluster_name = str(cluster["name"] or cluster_id)
            sessions_by_base: dict[str, list[dict[str, object]]] = {}
            for session in cluster_snapshot["sessions"]:
                infobase_id = str(session["infobase_id"])
                if not infobase_id:
                    continue
                session_view = dict(session)
                session_view["server_name"] = server_name
                session_view["cluster_name"] = cluster_name
                sessions_by_base.setdefault(infobase_id, []).append(session_view)
            for infobase in cluster_snapshot["infobases"]:
                infobase_id = str(infobase["id"])
                sessions = sessions_by_base.get(infobase_id, [])
                result.append(
                    {
                        "key": _base_key(endpoint, cluster_id, infobase_id),
                        "server_endpoint": endpoint,
                        "server_name": server_name,
                        "cluster_id": cluster_id,
                        "cluster_name": cluster_name,
                        "infobase_id": infobase_id,
                        "infobase_name": str(infobase["name"] or infobase_id),
                        "sessions": sessions,
                        "session_count": len(sessions),
                        "online": True,
                    }
                )
    return sorted(result, key=lambda item: str(item["infobase_name"]).casefold())


def _company_views(
    companies: tuple[Company, ...],
    available_bases: list[dict[str, object]],
) -> list[dict[str, object]]:
    available_by_key = {str(base["key"]): base for base in available_bases}
    views: list[dict[str, object]] = []
    for company in companies:
        assigned_keys: list[str] = []
        assigned_bases: list[dict[str, object]] = []
        sessions: list[dict[str, object]] = []
        for stored_base in company.bases:
            key = _base_key(
                stored_base.server_endpoint,
                stored_base.cluster_id,
                stored_base.infobase_id,
            )
            assigned_keys.append(key)
            current = available_by_key.get(key)
            if current is None:
                assigned_bases.append(
                    {
                        "key": key,
                        "server_endpoint": stored_base.server_endpoint,
                        "cluster_id": stored_base.cluster_id,
                        "infobase_id": stored_base.infobase_id,
                        "infobase_name": stored_base.infobase_name,
                        "sessions": [],
                        "session_count": 0,
                        "online": False,
                    }
                )
            else:
                assigned_bases.append(current)
                sessions.extend(current["sessions"])
        views.append(
            {
                "id": company.id,
                "name": company.name,
                "session_limit": company.session_limit,
                "remaining_sessions": (
                    max(company.session_limit - len(sessions), 0)
                    if company.session_limit > 0
                    else None
                ),
                "over_limit": company.session_limit > 0 and len(sessions) > company.session_limit,
                "assigned_keys": assigned_keys,
                "bases": assigned_bases,
                "sessions": sessions,
            }
        )
    return views


def _redirect_dashboard(*, message: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({key: value for key, value in {"message": message, "error": error}.items() if value})
    return RedirectResponse(url=f"/{'?' + query if query else ''}", status_code=303)


async def _form_fields(request: Request) -> dict[str, list[str]]:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return {}
    return parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)


def _current_credentials(request: Request) -> ClusterCredentials | None:
    token = request.cookies.get(AUTH_COOKIE)
    return request.app.state.credentials.get(token)


def _login_page(request: Request, error: str | None = None, username: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "username": username},
        status_code=401 if error else 200,
    )


@router.get("/health", tags=["service"])
async def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "MONITOR_ONLY",
        "servers_configured": len(request.app.state.settings.servers),
    }


@router.get("/api/snapshots", tags=["monitoring"])
async def snapshots(request: Request) -> dict[str, object]:
    credentials = _current_credentials(request)
    if credentials is None:
        raise HTTPException(status_code=401, detail="Требуется вход администратора кластера")
    data, errors = await run_in_threadpool(_collect, request, credentials)
    return {"mode": "MONITOR_ONLY", "servers": data, "errors": errors}


@router.post("/login", include_in_schema=False)
async def login(request: Request) -> HTMLResponse:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return _login_page(request, "Неверный формат запроса")
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    username = fields.get("username", [""])[0].strip()
    password = fields.get("password", [""])[0]
    if not username:
        return _login_page(request, "Введите имя администратора", username)
    credentials = ClusterCredentials(username=username, password=password)
    try:
        await run_in_threadpool(_validate_credentials, request, credentials)
    except RacError as exc:
        log.warning("Не удалось выполнить вход администратора кластера: %s", exc)
        return _login_page(request, f"Не удалось войти: {exc}", username)
    token = request.app.state.credentials.create(credentials)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=AUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout", include_in_schema=False)
async def logout(request: Request) -> RedirectResponse:
    request.app.state.credentials.delete(request.cookies.get(AUTH_COOKIE))
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(AUTH_COOKIE)
    return response


@router.post("/companies", include_in_schema=False)
async def create_company(request: Request) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect_dashboard(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    name = fields.get("name", [""])[0]
    try:
        await run_in_threadpool(request.app.state.companies.create_company, name)
    except CompanyStoreError as exc:
        return _redirect_dashboard(error=str(exc))
    return _redirect_dashboard(message="Компания создана")


@router.post("/companies/{company_id}/bases", include_in_schema=False)
async def update_company_bases(request: Request, company_id: int) -> RedirectResponse:
    credentials = _current_credentials(request)
    if credentials is None:
        return _redirect_dashboard(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    selected_keys = set(fields.get("base", []))
    snapshots, errors = await run_in_threadpool(_collect, request, credentials)
    if errors and not snapshots:
        return _redirect_dashboard(error="Не удалось получить список баз из RAS")
    available = _available_bases(snapshots)
    available_by_key = {str(base["key"]): base for base in available}
    unknown = selected_keys.difference(available_by_key)
    if unknown:
        return _redirect_dashboard(error="Список баз изменился. Обновите страницу и повторите")

    companies = await run_in_threadpool(request.app.state.companies.list_companies)
    company = next((item for item in companies if item.id == company_id), None)
    if company is None:
        return _redirect_dashboard(error="Компания не найдена")

    assignments = [
        AssignedInfobase(
            server_endpoint=str(available_by_key[key]["server_endpoint"]),
            cluster_id=str(available_by_key[key]["cluster_id"]),
            infobase_id=str(available_by_key[key]["infobase_id"]),
            infobase_name=str(available_by_key[key]["infobase_name"]),
        )
        for key in selected_keys
    ]
    for stored_base in company.bases:
        key = _base_key(
            stored_base.server_endpoint,
            stored_base.cluster_id,
            stored_base.infobase_id,
        )
        if key not in available_by_key:
            assignments.append(stored_base)
    try:
        await run_in_threadpool(
            request.app.state.companies.replace_bases,
            company_id,
            assignments,
        )
    except CompanyStoreError as exc:
        return _redirect_dashboard(error=str(exc))
    return _redirect_dashboard(message=f"Базы компании «{company.name}» сохранены")


@router.post("/companies/{company_id}/session-limit", include_in_schema=False)
async def update_company_session_limit(request: Request, company_id: int) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect_dashboard(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    raw_limit = fields.get("session_limit", [""])[0].strip()
    try:
        session_limit = int(raw_limit)
    except ValueError:
        return _redirect_dashboard(error="Введите целое число сеансов")
    try:
        await run_in_threadpool(
            request.app.state.companies.update_session_limit,
            company_id,
            session_limit,
        )
    except CompanyStoreError as exc:
        return _redirect_dashboard(error=str(exc))
    return _redirect_dashboard(message="Лимит сеансов сохранён")


@router.post("/companies/{company_id}/delete", include_in_schema=False)
async def delete_company(request: Request, company_id: int) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect_dashboard(error="Сначала войдите как администратор кластера")
    try:
        await run_in_threadpool(request.app.state.companies.delete_company, company_id)
    except CompanyStoreError as exc:
        return _redirect_dashboard(error=str(exc))
    return _redirect_dashboard(message="Компания удалена")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request) -> HTMLResponse:
    credentials = _current_credentials(request)
    if credentials is None:
        response = _login_page(request)
        if request.cookies.get(AUTH_COOKIE):
            response.delete_cookie(AUTH_COOKIE)
        return response
    data, errors = await run_in_threadpool(_collect, request, credentials)
    available_bases = _available_bases(data)
    stored_companies = await run_in_threadpool(request.app.state.companies.list_companies)
    companies = _company_views(stored_companies, available_bases)
    owners = {
        key: company["name"]
        for company in companies
        for key in company["assigned_keys"]
    }
    for base in available_bases:
        base["company_name"] = owners.get(base["key"], "")
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "servers": data,
            "errors": errors,
            "cluster_user": credentials.username,
            "available_bases": available_bases,
            "companies": companies,
            "message": request.query_params.get("message"),
            "page_error": request.query_params.get("error"),
        },
    )
