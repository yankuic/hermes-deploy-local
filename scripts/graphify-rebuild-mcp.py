#!/usr/bin/env python3
"""Host-side MCP server that rebuilds the Hermes vault knowledge graph.

Exposes a single synchronous tool ``rebuild_graph`` that runs the graphify
build on the HOST (DeepSeek key + ds-chat provider + output dir stay out of
the sandbox). The researcher profile calls this over MCP; the query server
(:8182) hot-reloads graph.json on mtime change, so no restart is needed.

Run:  <uv graphifyy python> graphify-rebuild-mcp.py --host 127.0.0.1 --port 8183
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import subprocess
import sys
from pathlib import Path

_HOME = Path.home()


def _env_path(name: str, default: Path) -> Path:
    """Return an env-var path override (``~``-expanded), else the default."""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


GRAPHIFY_BIN = os.environ.get(
    "GRAPHIFY_BIN", str(_HOME / ".local" / "bin" / "graphify")
)
CORPUS = str(_env_path("GRAPHIFY_CORPUS", _HOME / "Obsidian" / "hermes_vault"))
OUT_ROOT = str(
    _env_path("GRAPHIFY_OUT_ROOT", _HOME / ".hermes" / "graphify" / "hermes-vault")
)
PROFILE_ENV = _env_path(
    "GRAPHIFY_PROFILE_ENV",
    _HOME / ".hermes" / "profiles" / "researcher" / ".env",
)


def _load_deepseek_key() -> str:
    """Read DEEPSEEK_API_KEY from the researcher profile .env (single source)."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    if PROFILE_ENV.exists():
        for raw in PROFILE_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _run(*args: str, tail: int = 120) -> tuple[int, str]:
    env = dict(os.environ)
    env["PATH"] = f"{_HOME / '.local' / 'bin'}:" + env.get("PATH", "")
    env.setdefault("GRAPHIFY_MAX_OUTPUT_TOKENS", "65536")
    if not env.get("DEEPSEEK_API_KEY"):
        key = _load_deepseek_key()
        if key:
            env["DEEPSEEK_API_KEY"] = key
    proc = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        cwd=OUT_ROOT,
        env=env,
        timeout=7200,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    lines = combined.splitlines()
    body = "\n".join(lines[-tail:])
    return proc.returncode, body


def _rebuild(force: bool, allow_partial: bool, token_budget: int) -> str:
    if not Path(GRAPHIFY_BIN).exists():
        return "error: graphify binary not found at %s" % GRAPHIFY_BIN
    extract_args = [
        GRAPHIFY_BIN,
        "extract",
        CORPUS,
        "--out",
        OUT_ROOT,
        "--backend",
        "ds-chat",
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

    code, log = _run(*extract_args)
    part = "EXTRACT (rc=%d):\n%s" % (code, log)
    if code != 0:
        return part + "\n\nAborted before cluster-only (extract failed)."

    code2, log2 = _run(
        GRAPHIFY_BIN,
        "cluster-only",
        OUT_ROOT,
        "--backend",
        "ds-chat",
    )
    part2 = "\n\nCLUSTER-ONLY (rc=%d):\n%s" % (code2, log2)

    stats = _graph_stats()
    return part + part2 + "\n\n" + stats


def _graph_stats() -> str:
    graph_json = Path(OUT_ROOT) / "graphify-out" / "graph.json"
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
            f"  source .obsidian nodes still present: "
            f"{sum(1 for n in data.get('nodes', []) if str(n.get('source_file', '')).startswith('.obsidian/'))}"
        )
    except Exception as exc:
        return f"RESULT: could not stat rebuilt graph.json: {exc}"


def _build_server():
    from mcp import types
    from mcp.server import Server

    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="rebuild_graph",
                description=(
                    "Rebuild the Hermes vault knowledge graph (semantic + AST, "
                    "incremental, honors .graphifyignore) on the host and write it "
                    "to the served location. The query graph (:8182) hot-reloads. "
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
                    type="text", text=_rebuild(force, allow_partial, token_budget)
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

        server = Server(
            "graphify-rebuild",
            version=_version,
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
        )
    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="graphify-rebuild-mcp",
        description="Host-side MCP server exposing a sync rebuild_graph tool.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8183)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--api-key", default=os.environ.get("GRAPHIFY_REBUILD_API_KEY"))
    args = parser.parse_args()

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
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
            await self.app(scope, receive, send)

    server = _build_server()
    api_key = (args.api_key or "").strip() or None

    if args.host in ("0.0.0.0", "::", ""):
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        allowed = {args.host, "localhost", "127.0.0.1"}
        allowed |= {f"{h}:{args.port}" for h in list(allowed)}
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
        f"graphify-rebuild MCP server (streamable-http) on http://{args.host}:{args.port}{args.path} - "
        f"{'api-key required' if api_key else 'no auth'}",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
