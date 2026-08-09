#!/usr/bin/env python3
"""Host-side MCP server that rebuilds a Hermes vault knowledge graph.

Supports per-profile configuration via ``--profile`` or individual overrides.
Single sync tool ``rebuild_graph`` runs graphify extract + cluster-only on the
HOST (keys stay out of the sandbox). The query server hot-reloads graph.json
on mtime change, so no restart is needed.

Run:  <uv graphifyy python> graphify-rebuild-mcp.py --profile researcher
"""
from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path

_HOME = Path.home()

# ── Profile defaults ──────────────────────────────────────────────────────────
_PROFILES: dict[str, dict] = {
    "default": {
        "corpus": _HOME / "Obsidian" / "personal",
        "out_root": _HOME / ".hermes" / "graphify" / "personal",
        "env_file": _HOME / ".hermes" / "profiles" / "default" / ".env",
        "backend": "local-ollama",
        "env_key": "DEFAULT_GRAPHIFY_KEY",
        "port": 8185,
    },
    "researcher": {
        "corpus": _HOME / "Obsidian" / "hermes_vault",
        "out_root": _HOME / ".hermes" / "graphify" / "hermes-vault",
        "env_file": _HOME / ".hermes" / "profiles" / "researcher" / ".env",
        "backend": "ds-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "port": 8183,
    },
    "jseeker": {
        "corpus": _HOME / "Obsidian" / "jseeker",
        "out_root": _HOME / ".hermes" / "graphify" / "jseeker",
        "env_file": _HOME / ".hermes" / "profiles" / "jseeker" / ".env",
        "backend": "ds-chat",
        "env_key": "JSEEKER_GRAPHIFY_KEY",
        "port": 8187,
    },
}


def _resolve(profile: str) -> dict:
    """Resolve a profile name to its config, raising on unknown."""
    if profile not in _PROFILES:
        names = ", ".join(sorted(_PROFILES))
        raise SystemExit(f"Unknown profile {profile!r}. Choices: {names}")
    return dict(_PROFILES[profile])


def _load_key(profile_cfg: dict) -> str:
    """Read the API key from the profile .env, then process env, then empty."""
    env_key = profile_cfg["env_key"]
    if os.environ.get(env_key):
        return os.environ[env_key]
    env_file = profile_cfg["env_file"]
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith(f"{env_key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _run(args: list[str], out_root: str, profile_cfg: dict, tail: int = 120) -> tuple[int, str]:
    env = dict(os.environ)
    env["PATH"] = f"{_HOME / '.local' / 'bin'}:" + env.get("PATH", "")
    env.setdefault("GRAPHIFY_MAX_OUTPUT_TOKENS", "65536")
    env_key = profile_cfg["env_key"]
    if not env.get(env_key):
        key = _load_key(profile_cfg)
        if key:
            env[env_key] = key
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=out_root,
        env=env,
        timeout=7200,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    lines = combined.splitlines()
    body = "\n".join(lines[-tail:])
    return proc.returncode, body


def _graph_stats(out_root: str) -> str:
    graph_json = Path(out_root) / "graphify-out" / "graph.json"
    try:
        import json
        data = json.loads(graph_json.read_text(encoding="utf-8"))
        nodes = len(data.get("nodes", []))
        links = data.get("links", data.get("edges", []))
        links_n = len(links) if isinstance(links, (list, dict)) else 0
        communities = len(data.get("communities", {})) if isinstance(
            data.get("communities"), (dict, list)
        ) else 0
        return (
            f"RESULT: graph.json at {graph_json}\n"
            f"  nodes={nodes} links={links_n} communities={communities}\n"
        )
    except Exception as exc:
        return f"RESULT: could not stat rebuilt graph.json: {exc}"


def _rebuild(profile_cfg: dict, force: bool, allow_partial: bool, token_budget: int) -> str:
    graphify_bin = os.environ.get("GRAPHIFY_BIN", str(_HOME / ".local" / "bin" / "graphify"))
    corpus = str(profile_cfg["corpus"])
    out_root = str(profile_cfg["out_root"])
    backend = profile_cfg["backend"]

    if not Path(graphify_bin).exists():
        return f"error: graphify binary not found at {graphify_bin}"

    extract_args = [
        graphify_bin,
        "extract",
        corpus,
        "--out",
        out_root,
        "--backend",
        backend,
        "--token-budget",
        str(token_budget),
        "--max-concurrency",
        "6",
        "--timing",
    ]
    if force:
        extract_args.append("--force")
    if allow_partial:
        extract_args.append("--allow-partial")

    code, log = _run(extract_args, out_root, profile_cfg)
    part = f"EXTRACT (rc={code}):\n{log}"
    if code != 0:
        return part + "\n\nAborted before cluster-only (extract failed)."

    code2, log2 = _run(
        [graphify_bin, "cluster-only", out_root, "--backend", backend],
        out_root,
        profile_cfg,
    )
    part2 = f"\n\nCLUSTER-ONLY (rc={code2}):\n{log2}"

    stats = _graph_stats(out_root)
    return part + part2 + "\n\n" + stats


def _build_server(profile_cfg: dict):
    from mcp import types
    from mcp.server import Server

    profile = profile_cfg.get("_profile_name", "unknown")

    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="rebuild_graph",
                description=(
                    f"Rebuild the {profile} vault knowledge graph (semantic + AST, "
                    "incremental, honors .graphifyignore) on the host and write it "
                    "to the served location. The query server hot-reloads. "
                    "Use after vault edits or after changing .graphifyignore."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "force": {
                            "type": "boolean",
                            "description": "Full re-scan/re-dispatch, skip incremental gate + semantic cache (default false)",
                            "default": False,
                        },
                        "allow_partial": {
                            "type": "boolean",
                            "description": "Allow overwriting a larger graph if extraction is incomplete (default false)",
                            "default": False,
                        },
                        "token_budget": {
                            "type": "integer",
                            "description": "Per-chunk token cap for semantic extraction (default 20000)",
                            "default": 20000,
                        },
                    },
                },
            )
        ]

    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name != "rebuild_graph":
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        arguments = dict(arguments or {})
        force = bool(arguments.get("force", False))
        allow_partial = bool(arguments.get("allow_partial", False))
        try:
            token_budget = int(arguments.get("token_budget", 20000))
        except (TypeError, ValueError):
            token_budget = 20000
        try:
            return [
                types.TextContent(
                    type="text", text=_rebuild(profile_cfg, force, allow_partial, token_budget)
                )
            ]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"Error running rebuild: {exc}")]

    try:
        from importlib.metadata import version as _pkg_version
        _version = _pkg_version("mcp")
    except Exception:
        _version = "0"

    if hasattr(Server, "list_tools"):
        server = Server("graphify-rebuild")
        server.list_tools()(list_tools)
        server.call_tool()(call_tool)
    else:
        async def _on_list_tools(ctx, params) -> types.ListToolsResult:
            return types.ListToolsResult(tools=await list_tools())
        async def _on_call_tool(ctx, params) -> types.CallToolResult:
            content = await call_tool(params.name, dict(params.arguments or {}))
            return types.CallToolResult(content=content)
        server = Server("graphify-rebuild", version=_version, on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="graphify-rebuild-mcp",
        description="Host-side MCP server exposing a sync rebuild_graph tool.",
    )
    parser.add_argument("--profile", default="",
        help="Profile name (default/researcher/jseeker) — sets corpus, out-root, backend, env-key, and port defaults")
    parser.add_argument("--corpus", default="",
        help="Vault/corpus path (overrides profile default)")
    parser.add_argument("--out-root", default="",
        help="Graph output directory (overrides profile default)")
    parser.add_argument("--backend", default="",
        help="Graphify extraction backend (overrides profile default)")
    parser.add_argument("--env-file", default="",
        help="Path to profile .env file (overrides profile default)")
    parser.add_argument("--env-key", default="",
        help="Environment variable name for the API key (overrides profile default)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
        help="Server port (overrides profile default port)")
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--api-key", default=os.environ.get("GRAPHIFY_REBUILD_API_KEY"))
    args = parser.parse_args()

    profile_cfg = _resolve(args.profile) if args.profile else {}
    if not profile_cfg and not args.corpus:
        parser.error("Either --profile or --corpus is required")

    if args.profile:
        profile_cfg["_profile_name"] = args.profile
    if args.corpus:
        profile_cfg["corpus"] = Path(args.corpus)
    if args.out_root:
        profile_cfg["out_root"] = Path(args.out_root)
    if args.backend:
        profile_cfg["backend"] = args.backend
    if args.env_file:
        profile_cfg["env_file"] = Path(args.env_file)
    if args.env_key:
        profile_cfg["env_key"] = args.env_key
    port = args.port or profile_cfg.get("port", 8183)

    try:
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Route
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
        import uvicorn
    except ImportError as e:
        raise ImportError("HTTP transport needs the mcp extra. Run: pip install 'graphifyy[mcp]'") from e

    class _ASGIApp:
        def __init__(self, manager):
            self._manager = manager
        async def __call__(self, scope, receive, send):
            await self._manager.handle_request(scope, receive, send)

    class _ApiKeyMiddleware:
        def __init__(self, app, api_key):
            self.app = app
            self._expected = (api_key or "").encode("utf-8")
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or not self._expected:
                await self.app(scope, receive, send)
                return
            import hmac
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"x-api-key")
            if provided is None:
                scheme, _, token = headers.get(b"authorization", b"").partition(b" ")
                if scheme.lower() == b"bearer" and token:
                    provided = token.strip()
            if provided is None or not hmac.compare_digest(provided, self._expected):
                body = b'{"error": "unauthorized"}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))],
                })
                await send({"type": "http.response.body", "body": body})
                return
            await self.app(scope, receive, send)

    server = _build_server(profile_cfg)
    api_key = (args.api_key or "").strip() or None

    if args.host in ("0.0.0.0", "::", ""):
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        allowed = {args.host, "localhost", "127.0.0.1"}
        allowed |= {f"{h}:{port}" for h in list(allowed)}
        security = TransportSecuritySettings(allowed_hosts=sorted(allowed))

    manager = StreamableHTTPSessionManager(
        app=server, json_response=False, stateless=False,
        security_settings=security, session_idle_timeout=3600.0,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    middleware = []
    if api_key:
        middleware.append(Middleware(_ApiKeyMiddleware, api_key=api_key))

    app = Starlette(
        routes=[Route(args.path, endpoint=_ASGIApp(manager))],
        middleware=middleware,
        lifespan=lifespan,
    )
    print(
        f"graphify-rebuild MCP server ({profile_cfg.get('_profile_name', 'custom')}) "
        f"on http://{args.host}:{port}{args.path} - "
        f"{'api-key required' if api_key else 'no auth'}",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host, port=port)


if __name__ == "__main__":
    main()
