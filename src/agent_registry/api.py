"""REST API — yedi uç nokta."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from psycopg.errors import IntegrityError
from pydantic import BaseModel, Field

from . import service, store
from .auth import AuthError, Principal, authenticate, require_role, require_tenant
from .manifest import AgentManifest
from .service import RegistryError

app = FastAPI(title="Agent Registry", version="0.2.0")


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


# Eval gövdesi bilinçli olarak ham dict: kanıt kuralları eval_result.py'de tek yerde
# yaşıyor. Pydantic modeli kullanınca `dataset_hash` gibi alanlar sessizce düşüyordu.


class PromoteIn(BaseModel):
    env: str = "prod"


def _principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Bearer API key required")
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
    body: dict[str, Any],
    principal: PrincipalDep,
):
    require_role(principal, "publisher")
    require_tenant(principal, tenant)
    return service.validate_version(
        tenant=tenant,
        name=name,
        semver=semver,
        eval_result=body,
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


# ------------------------------------------------------------------ kataloglar (M2)


class ToolIn(BaseModel):
    owner: str = Field(min_length=1)
    description: str = ""


class ModelIn(BaseModel):
    provider: str = Field(min_length=1)
    note: str = ""


class TeamIn(BaseModel):
    on_call: str = Field(min_length=1)
    allowed_scopes: list[str] = Field(default_factory=list)


@app.put("/v1/catalogs/tools/{name}")
def upsert_tool(name: str, body: ToolIn, principal: PrincipalDep):
    require_role(principal, "publisher", "security")
    store.upsert_tool(name, body.owner, body.description)
    store.insert_audit(
        actor=principal.actor,
        action="catalog.tool.upsert",
        object_type="tool",
        object_id=name,
        detail={"owner": body.owner},
    )
    return {"name": name, "owner": body.owner, "description": body.description}


@app.put("/v1/catalogs/models/{name}")
def upsert_model(name: str, body: ModelIn, principal: PrincipalDep):
    require_role(principal, "publisher", "security")
    store.upsert_model(name, body.provider, body.note)
    store.insert_audit(
        actor=principal.actor,
        action="catalog.model.upsert",
        object_type="model",
        object_id=name,
        detail={"provider": body.provider},
    )
    return {"name": name, "provider": body.provider, "note": body.note}


@app.put("/v1/catalogs/teams/{name}")
def upsert_team(name: str, body: TeamIn, principal: PrincipalDep):
    require_role(principal, "publisher", "security")
    store.upsert_team(name, body.on_call, body.allowed_scopes)
    store.insert_audit(
        actor=principal.actor,
        action="catalog.team.upsert",
        object_type="team",
        object_id=name,
        detail={"on_call": body.on_call, "scopes": body.allowed_scopes},
    )
    return {"name": name, "on_call": body.on_call, "allowed_scopes": body.allowed_scopes}


@app.get("/v1/catalogs/tools")
def get_tools(principal: PrincipalDep):
    return {"tools": store.list_tools()}


@app.get("/v1/catalogs/models")
def get_models(principal: PrincipalDep):
    return {"models": store.list_models()}


@app.get("/v1/catalogs/teams")
def get_teams(principal: PrincipalDep):
    return {"teams": store.list_teams()}


@app.get("/v1/catalogs/{kind}/{name}/impact")
def catalog_impact(kind: str, name: str, principal: PrincipalDep):
    """Etki analizi: bu araç/model aktif sürümlerde nerede kullanılıyor?"""
    require_role(principal, "publisher", "security")
    if kind not in ("tools", "models"):
        raise RegistryError("unknown catalogue kind", status=404, code="unknown_catalog")
    return {"kind": kind, "name": name, "agents": store.agent_versions_using(kind, name)}


@app.get("/metrics")
def metrics(principal: PrincipalDep):
    """Prometheus metin biçimi. Toplamlar kiracı kapsamlıdır (security rolü)."""
    require_role(principal, "security")
    tenants = store.list_tenants() if "*" in principal.tenants else principal.tenants
    lines = [
        "# HELP agent_registry_agents_total Registered agents",
        "# TYPE agent_registry_agents_total gauge",
        "# HELP agent_registry_versions_total Versions by state",
        "# TYPE agent_registry_versions_total gauge",
        "# HELP agent_registry_deployments_total Active deployments by env",
        "# TYPE agent_registry_deployments_total gauge",
        "# HELP agent_registry_stale_active_versions Versions active for more than 90 days",
        "# TYPE agent_registry_stale_active_versions gauge",
        "# HELP agent_registry_blocked_versions_total Blocked versions",
        "# TYPE agent_registry_blocked_versions_total gauge",
    ]
    for tenant in tenants or [None]:
        snap = store.metrics_snapshot(tenant)
        label = f'tenant="{tenant}"' if tenant else ""
        for state, count in sorted(snap["versions"].items()):
            lines.append(
                f'agent_registry_versions_total{{state="{state}",{label}}} {count}'
                if label
                else f'agent_registry_versions_total{{state="{state}"}} {count}'
            )
        for env, count in sorted(snap["deployments"].items()):
            lines.append(
                f'agent_registry_deployments_total{{env="{env}",{label}}} {count}'
                if label
                else f'agent_registry_deployments_total{{env="{env}"}} {count}'
            )
        if tenant:
            lines.append(f"agent_registry_agents_total{{{label}}} {snap['agents']}")
            lines.append(f"agent_registry_stale_active_versions{{{label}}} {snap['stale_active']}")
            lines.append(f"agent_registry_blocked_versions_total{{{label}}} {snap['blocked']}")
        else:
            lines.append(f"agent_registry_agents_total {snap['agents']}")
            lines.append(f"agent_registry_stale_active_versions {snap['stale_active']}")
            lines.append(f"agent_registry_blocked_versions_total {snap['blocked']}")
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/v1/audit")
def audit(
    principal: PrincipalDep,
    object_type: str | None = Query(default=None),
    object_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    require_role(principal, "security")
    return {"events": store.audit_events(object_type, object_id, limit)}
