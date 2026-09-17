"""Talks to the sibling `simkl-mcp` server for every Simkl call.

re-com-video holds no Simkl credentials of its own -- no token, no client_id,
no login flow. All of that lives in `simkl-mcp`; this module spawns it as an
MCP server subprocess the way any MCP client would, and exposes the handful of
tools the engine needs. Same arrangement re-com has with `ytmusic-mcp`, and
for the same reason (PLAN.md 8.4): a recommender that could mutate the history
cannot be trusted to have excluded what it just added.

Configured via RECOM_VIDEO_SIMKL_MCP_COMMAND (interpreter) and
RECOM_VIDEO_SIMKL_MCP_ARGS (its server.py path) -- the command/args split
`claude mcp add` already uses. SIMKL_* variables are forwarded into the child,
because MCP's stdio client hands it only a minimal environment and simkl-mcp
reads SIMKL_AUTH_PATH from its own.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import shlex
import threading
from contextlib import AsyncExitStack
from typing import Any

_CONNECT_TIMEOUT = 30
_CALL_TIMEOUT = 90

CONFIG_HELP = (
    "simkl-mcp is not configured. Set RECOM_VIDEO_SIMKL_MCP_COMMAND (its "
    "interpreter) and RECOM_VIDEO_SIMKL_MCP_ARGS (its server.py path) so "
    "re-com-video can reach Simkl through it -- see README."
)


class SimklSourceError(RuntimeError):
    """A simkl-mcp call failed. Its message is already actionable -- that
    server translates auth, rate-limit and network failures itself."""


def _subprocess_env() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    env = get_default_environment()
    env.update({k: v for k, v in os.environ.items() if k.startswith("SIMKL_")})
    return env


class SimklSource:
    """Synchronous facade over a persistent simkl-mcp subprocess.

    One dedicated background thread runs the asyncio loop the MCP session
    needs; every public method blocks the caller on the result, so the rest of
    the engine -- entirely synchronous -- never sees an await.
    """

    def __init__(self, command: str | None = None, args: list[str] | None = None):
        from mcp import ClientSession, StdioServerParameters  # noqa: F401

        command = command or os.environ.get("RECOM_VIDEO_SIMKL_MCP_COMMAND")
        if args is None:
            raw = os.environ.get("RECOM_VIDEO_SIMKL_MCP_ARGS")
            args = shlex.split(raw) if raw else None
        if not command or not args:
            raise SimklSourceError(CONFIG_HELP)
        self._command, self._args = command, args

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        try:
            fut.result(timeout=_CONNECT_TIMEOUT)
        except Exception as e:
            self._shutdown_loop()
            raise SimklSourceError(f"Couldn't start simkl-mcp: {e}") from e
        atexit.register(self.close)

    async def _connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self._command, args=self._args, env=_subprocess_env()
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

    def _call(self, tool: str, *, unwrap: bool = True, **arguments: Any) -> Any:
        fut = asyncio.run_coroutine_threadsafe(
            self._call_async(tool, arguments, unwrap), self._loop
        )
        return fut.result(timeout=_CALL_TIMEOUT)

    async def _call_async(self, tool: str, arguments: dict[str, Any], unwrap: bool) -> Any:
        result = await self._session.call_tool(tool, arguments)
        if result.is_error:
            text = "".join(getattr(b, "text", "") for b in result.content)
            raise SimklSourceError(text or f"simkl-mcp's {tool} failed")
        content = result.structured_content
        # Tools returning a list or str get wrapped as {"result": ...} by the
        # MCP framework; tools returning a dict do not.
        if unwrap and isinstance(content, dict) and "result" in content:
            return content["result"]
        return content

    def close(self) -> None:
        if self._stack is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop)
        try:
            fut.result(timeout=10)
        except Exception:
            pass
        self._stack = None
        self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # --- the surface the engine uses ---------------------------------------

    def activities(self) -> dict[str, Any]:
        return self._call("get_activities", unwrap=False) or {}

    def library(self, type=None, status=None, date_from=None) -> dict[str, Any]:
        return self._call(
            "get_library", unwrap=False, type=type, status=status, date_from=date_from
        ) or {}

    def library_ids(self, type=None) -> list[int]:
        out = self._call("get_library_ids", unwrap=False, type=type) or {}
        return list(out.get("simkl_ids") or [])

    def title(self, simkl_id: int, type: str) -> dict[str, Any]:
        return self._call("get_title", unwrap=False, simkl_id=int(simkl_id), type=type) or {}

    def resolve_imdb(self, imdb: str) -> dict[str, Any]:
        return self._call("resolve_id", unwrap=False, imdb=imdb) or {}

    def search(self, query: str, type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self._call("search", query=query, type=type, limit=limit) or []

    def lookup_watched(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._call("lookup_watched", items=items) or []
