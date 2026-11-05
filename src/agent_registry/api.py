"""REST API — yedi uç nokta."""

from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query
from fastapi.responses import JSONResponse
from psycopg.errors import IntegrityError
from pydantic import BaseModel, Field

from . import service, store
from .auth import AuthError, Principal, authenticate, require_role, require_tenant
from .manifest import AgentManifest
from .service import RegistryError

app = FastAPI(title="Agent Registry", version="0.1.0")


# ------------------------------------------------------------------------ modeller


class AgentCreate(BaseModel):
    name: str
    tenant: str
    owner_team: str
    purpose: str = Field(min_length=10)
    criticality: str = "standard"
    data_classification: str = "internal"


class VersionCreate(BaseModel):
    manifest: AgentManifest
    git_commit: str | None = None


class EvalResultIn(BaseModel):
    suite: str
    success_rate: float
    suites: dict[str, dict] = Field(default_factory=dict)
    total_cases: int | None = None


class PromoteIn(BaseModel):
    env: str = "prod"


def _principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Bearer API anahtarı gerekli")
    return authenticate(authorization.removeprefix("Bearer ").strip())


PrincipalDep = Annotated[Principal, Depends(_principal)]


@app.exception_handler(IntegrityError)
async def _integrity_error(_request, exc: IntegrityError):
    """Yarış koşulu: DB kısıtı son savunma hattı."""
    return JSONResponse(
        status_code=409,
        content={"error": "constraint_violation", "message": str(exc).splitlines()[0]},
    )


@app.exception_handler(RegistryError)
async def _registry_error(_request, exc: RegistryError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "message": exc.message},
    )


@app.exception_handler(AuthError)
async def _auth_error(_request, exc: AuthError):
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "message": exc.message},
        headers=headers,
    )


# ------------------------------------------------------------------------ uç noktalar


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/agents", status_code=201)
def create_agent(body: AgentCreate, principal: PrincipalDep):
    require_role(principal, "publisher")
    require_tenant(principal, body.tenant)
    return service.register_agent(
        tenant=body.tenant,
        name=body.name,
        owner_team=body.owner_team,
        purpose=body.purpose,
        actor=principal.actor,
        criticality=body.criticality,
        data_classification=body.data_classification,
    )


@app.get("/v1/agents")
def list_agents(
    principal: PrincipalDep,
    tenant: str | None = Query(default=None),
    owner: str | None = Query(default=None),
):
    require_role(principal, "publisher", "security")
    if tenant:
        require_tenant(principal, tenant)
    elif "*" not in principal.tenants:
        if len(principal.tenants) != 1:
            raise AuthError("tenant parametresi gerekli", status=400, code="tenant_required")
        tenant = next(iter(principal.tenants))
    return {"agents": store.list_agents(tenant=tenant, owner=owner)}


@app.post("/v1/agents/{tenant}/{name}/versions", status_code=201)
def create_version(
    tenant: str,
    name: str,
    body: VersionCreate,
    principal: PrincipalDep,
):
    require_role(principal, "publisher")
    require_tenant(principal, tenant)
    return service.create_version(
        tenant=tenant,
        name=name,
        manifest=body.manifest,
        actor=principal.actor,
        git_commit=body.git_commit,
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:validate")
def validate_version(
    tenant: str,
    name: str,
    semver: str,
    body: EvalResultIn,
    principal: PrincipalDep,
):
    require_role(principal, "publisher")
    require_tenant(principal, tenant)
    return service.validate_version(
        tenant=tenant,
        name=name,
        semver=semver,
        eval_result=body.model_dump(),
        actor=principal.actor,
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:promote")
def promote_version(
    tenant: str,
    name: str,
    semver: str,
    body: PromoteIn,
    principal: PrincipalDep,
):
    require_role(principal, "publisher")
    require_tenant(principal, tenant)
    return service.promote_version(
        tenant=tenant,
        name=name,
        semver=semver,
        actor=principal.actor,
        env=body.env,
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:deprecate")
def deprecate_version(
    tenant: str,
    name: str,
    semver: str,
    principal: PrincipalDep,
):
    require_role(principal, "publisher")
    require_tenant(principal, tenant)
    return service.transition(
        tenant=tenant, name=name, semver=semver, target="deprecated", actor=principal.actor
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:retire")
def retire_version(
    tenant: str,
    name: str,
    semver: str,
    principal: PrincipalDep,
):
    require_role(principal, "publisher")
    require_tenant(principal, tenant)
    return service.transition(
        tenant=tenant, name=name, semver=semver, target="retired", actor=principal.actor
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:block")
def block_version(
    tenant: str,
    name: str,
    semver: str,
    principal: PrincipalDep,
):
    require_role(principal, "security")
    require_tenant(principal, tenant)
    return service.transition(
        tenant=tenant, name=name, semver=semver, target="blocked", actor=principal.actor
    )


@app.get("/v1/agents/{tenant}/{name}/resolve")
def resolve(
    tenant: str,
    name: str,
    principal: PrincipalDep,
    env: str = Query(default="prod"),
):
    require_role(principal, "runtime", "publisher")
    require_tenant(principal, tenant)
    return service.resolve(tenant=tenant, name=name, env=env)


@app.get("/v1/audit")
def audit(
    principal: PrincipalDep,
    object_type: str | None = Query(default=None),
    object_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    require_role(principal, "security")
    return {"events": store.audit_events(object_type, object_id, limit)}
