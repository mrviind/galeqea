"""stdio MCP transport, for desktop hosts.

Implements the JSON-RPC framing directly so the server runs with no extra
dependency; the official SDK is used when it is installed.
"""

from __future__ import annotations

import asyncio
import json
import sys

from . import server


async def serve(project_id: str) -> None:
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = await _dispatch(request, project_id)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


async def _dispatch(request: dict, project_id: str) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        info = server.server_info()
        return _ok(request_id, {
            "protocolVersion": info["protocolVersion"],
            "capabilities": info["capabilities"],
            "serverInfo": {"name": info["name"], "version": info["version"]},
            "instructions": info["instructions"],
        })
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "tools/list":
        return _ok(request_id, {"tools": server.list_tools()})
    if method == "tools/call":
        result = await server.call_tool(
            params.get("name", ""), params.get("arguments") or {}, project_id=project_id
        )
        return _ok(request_id, result)
    if method == "resources/list":
        return _ok(request_id, {"resources": server.list_resources(project_id)})
    if method == "resources/read":
        return _ok(request_id, await server.read_resource(params.get("uri", "")))
    if method == "prompts/list":
        return _ok(request_id, {"prompts": server.list_prompts()})
    if method == "prompts/get":
        return _ok(request_id, server.get_prompt(
            params.get("name", ""), params.get("arguments") or {}, project_id=project_id
        ))
    if method == "ping":
        return _ok(request_id, {})

    return {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _ok(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main(project_id: str = "") -> None:
    from ..db import init_db, session_scope
    from ..models import Project

    init_db()
    if not project_id:
        with session_scope() as db:
            from sqlalchemy import select

            project = db.execute(select(Project).limit(1)).scalar_one_or_none()
            project_id = project.id if project else ""
    asyncio.run(serve(project_id))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
