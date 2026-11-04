"""REST API — yedi uç nokta."""

from fastapi import Depends, FastAPI, Header, Query
from fastapi.responses import JSONResponse
from psycopg.errors import IntegrityError
from pydantic import BaseModel, Field

from . import service, store
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
    gate_results: dict = Field(default_factory=dict)


def _actor(x_actor: str | None = Header(default=None)) -> str:
    return x_actor or "system"


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


# ------------------------------------------------------------------------ uç noktalar


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/agents", status_code=201)
def create_agent(body: AgentCreate, actor: str = Depends(_actor)):
    return service.register_agent(
        tenant=body.tenant,
        name=body.name,
        owner_team=body.owner_team,
        purpose=body.purpose,
        actor=actor,
        criticality=body.criticality,
        data_classification=body.data_classification,
    )


@app.get("/v1/agents")
def list_agents(
    tenant: str | None = Query(default=None),
    owner: str | None = Query(default=None),
):
    return {"agents": store.list_agents(tenant=tenant, owner=owner)}


@app.post("/v1/agents/{tenant}/{name}/versions", status_code=201)
def create_version(tenant: str, name: str, body: VersionCreate, actor: str = Depends(_actor)):
    return service.create_version(
        tenant=tenant,
        name=name,
        manifest=body.manifest,
        actor=actor,
        git_commit=body.git_commit,
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:validate")
def validate_version(
    tenant: str, name: str, semver: str, body: EvalResultIn, actor: str = Depends(_actor)
):
    return service.validate_version(
        tenant=tenant,
        name=name,
        semver=semver,
        eval_result=body.model_dump(),
        actor=actor,
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:promote")
def promote_version(
    tenant: str, name: str, semver: str, body: PromoteIn, actor: str = Depends(_actor)
):
    return service.promote_version(
        tenant=tenant,
        name=name,
        semver=semver,
        actor=actor,
        env=body.env,
        gate_results=body.gate_results,
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:deprecate")
def deprecate_version(tenant: str, name: str, semver: str, actor: str = Depends(_actor)):
    return service.transition(
        tenant=tenant, name=name, semver=semver, target="deprecated", actor=actor
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:retire")
def retire_version(tenant: str, name: str, semver: str, actor: str = Depends(_actor)):
    return service.transition(
        tenant=tenant, name=name, semver=semver, target="retired", actor=actor
    )


@app.post("/v1/agents/{tenant}/{name}/versions/{semver}:block")
def block_version(
    tenant: str,
    name: str,
    semver: str,
    actor: str = Depends(_actor),
    x_role: str | None = Header(default=None),
):
    if x_role != "security":
        raise RegistryError("blocking requires X-Role: security", status=403, code="forbidden")
    return service.transition(
        tenant=tenant, name=name, semver=semver, target="blocked", actor=actor
    )


@app.get("/v1/agents/{tenant}/{name}/resolve")
def resolve(tenant: str, name: str, env: str = Query(default="prod")):
    return service.resolve(tenant=tenant, name=name, env=env)


@app.get("/v1/audit")
def audit(
    object_type: str | None = Query(default=None),
    object_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    return {"events": store.audit_events(object_type, object_id, limit)}
