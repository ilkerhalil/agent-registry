"""Komut satırı: sunucu, şema kurulumu, CI kapısı."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import store
from .config import get_settings
from .db import close_pool, init_schema
from .gate import gate_passed, load_catalogs, run_gate


def _load_json_or_yaml(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml

        return yaml.safe_load(text)
    return json.loads(text)


def cmd_init_db(_args) -> int:
    init_schema()
    print("schema applied")
    return 0


def cmd_serve(args) -> int:
    if not get_settings().api_keys:
        print(
            "error: REGISTRY_API_KEYS is not set; "
            "the server refuses to start without authentication",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    uvicorn.run("agent_registry.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_gate(args) -> int:
    manifest = _load_json_or_yaml(args.manifest)
    eval_result = _load_json_or_yaml(args.eval_result) if args.eval_result else None
    catalogs = load_catalogs(args.catalog_dir)

    conflicts = []
    if not args.no_db:
        tenant = (manifest or {}).get("tenant")
        if tenant:
            try:
                conflicts = store.active_scopes_by_team(tenant)
            except Exception as exc:  # DB yoksa kapı DB'siz koşar ama uyarır
                print(
                    f"warning: registry unreachable, scope-conflict check skipped ({exc})",
                    file=sys.stderr,
                )

    checks = run_gate(manifest, eval_result, catalogs, conflicts)
    ok = gate_passed(checks)

    if args.json:
        print(
            json.dumps(
                {"passed": ok, "checks": [c.__dict__ for c in checks]}, ensure_ascii=False, indent=2
            )
        )
    else:
        for c in checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"[{mark}] {c.name:16} {c.detail}")
        print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_agents(args) -> int:
    rows = store.list_agents(tenant=args.tenant, owner=args.owner)
    for r in rows:
        ver = r.get("active_version") or "-"
        print(f"{r['tenant']:12} {r['name']:24} {r['owner_team']:20} {ver}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-registry", description="Agent Registry CLI")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="start the API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8100)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("init-db", help="apply the schema")
    s.set_defaults(func=cmd_init_db)

    s = sub.add_parser("gate", help="CI gate: run the seven checks on a manifest")
    s.add_argument("--manifest", required=True)
    s.add_argument("--eval-result")
    s.add_argument("--catalog-dir", default="./catalogs")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-db", action="store_true", help="skip the scope-conflict check")
    s.set_defaults(func=cmd_gate)

    s = sub.add_parser("agents", help="envanter")
    s.add_argument("--tenant")
    s.add_argument("--owner")
    s.set_defaults(func=cmd_agents)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
