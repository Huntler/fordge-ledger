"""The MCP server, driven over the real JSON-RPC transport.

These go through `/mcp` rather than calling the tool functions directly, so the
schemas, the mount, and the session handling are all exercised — that is where
an MCP integration actually breaks.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

from .conftest import _png_bytes, write_sliced_3mf

PROTOCOL = "2025-06-18"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_OLLAMA_URL", "")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def rpc(client: TestClient, method: str, params: dict | None = None) -> dict[str, Any]:
    """One JSON-RPC call against the mounted MCP endpoint."""
    response = client.post(
        "/mcp/",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text

    body = response.text
    if body.startswith("event:") or body.startswith("data:"):
        # SSE framing: the payload is the last `data:` line.
        lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
        body = lines[-1][len("data:") :].strip()
    payload = json.loads(body)
    assert "error" not in payload, payload["error"]
    return payload["result"]


def initialise(client: TestClient) -> dict[str, Any]:
    return rpc(
        client,
        "initialize",
        {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "test-agent", "version": "1.0"},
        },
    )


def call_tool(client: TestClient, name: str, arguments: dict | None = None) -> Any:
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert not result.get("isError"), result
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def call_tool_expecting_error(client: TestClient, name: str, arguments: dict) -> str:
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert result.get("isError"), f"expected a tool error, got {result}"
    return result["content"][0]["text"]


# --------------------------------------------------------------- handshake


def test_the_endpoint_speaks_mcp(client: TestClient):
    info = initialise(client)

    assert info["protocolVersion"]
    assert info["serverInfo"]["name"] == "forge-ledger"
    # The instructions are how the agent learns the folder conventions.
    assert "upload_file" in info["instructions"]


def test_the_instructions_only_name_tools_that_exist(client: TestClient):
    """A made-up tool name in the preamble sends the agent down a dead end."""
    import re

    info = initialise(client)
    available = {t["name"] for t in rpc(client, "tools/list")["tools"]}

    # Only call-style mentions, e.g. `create_project()` — bare backticked words
    # in the text are parameter names, not tools.
    referenced = set(re.findall(r"`([a-z_]+)\([^`]*\)`", info["instructions"]))
    assert referenced, "the instructions should name the tools to use"
    assert referenced <= available, f"instructions name missing tools: {referenced - available}"


def test_tools_are_advertised_with_schemas(client: TestClient):
    initialise(client)
    tools = {t["name"]: t for t in rpc(client, "tools/list")["tools"]}

    assert {
        "list_projects",
        "get_project",
        "read_project_file",
        "print_history",
        "create_project",
        "update_project",
        "upload_file",
        "attach_files_to_model",
        "rescan_library",
        "library_info",
    } <= set(tools)

    upload = tools["upload_file"]
    assert set(upload["inputSchema"]["required"]) == {
        "project",
        "filename",
        "content_base64",
    }
    assert "base64" in upload["description"]


def test_no_tool_can_delete_a_project(client: TestClient):
    """Deletion is deliberately unreachable from MCP, by design, not by omission.

    The capability exists — the REST API and the UI both have it — so this is a
    guard against someone adding a convenience tool later without thinking about
    an agent being able to erase a project folder unprompted.
    """
    initialise(client)
    tools = rpc(client, "tools/list")["tools"]
    names = [t["name"] for t in tools]

    assert not [n for n in names if "delete" in n or "remove" in n or "purge" in n]

    # Nor by a back door: no tool's schema takes something like `purge`.
    for tool in tools:
        properties = tool["inputSchema"].get("properties", {})
        assert not {"purge", "delete", "destroy"} & set(properties), tool["name"]

    # And the capability really does exist elsewhere, so this proves intent.
    created = call_tool(client, "create_project", {"title": "Doomed"})
    assert client.delete(f"/api/projects/{created['id']}").status_code == 200


def test_mcp_can_be_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_MCP_ENABLED", "false")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        # Falls through to the SPA/404 rather than serving MCP.
        assert client.post("/mcp/", headers=HEADERS, json={}).status_code != 200
        assert client.get("/api/health").status_code == 200
    get_settings.cache_clear()


# -------------------------------------------------------------------- read


def test_listing_and_reading_projects(client: TestClient):
    initialise(client)
    created = call_tool(
        client,
        "create_project",
        {"title": "Desk Organizer", "tags": ["office"], "license": "CC-BY-4.0"},
    )

    listing = call_tool(client, "list_projects")
    assert listing["count"] == 1
    assert listing["projects"][0]["title"] == "Desk Organizer"

    # Both an id and a slug identify a project, since an agent may hold either.
    by_id = call_tool(client, "get_project", {"project": created["id"]})
    by_slug = call_tool(client, "get_project", {"project": "desk-organizer"})
    assert by_id["id"] == by_slug["id"] == created["id"]
    assert by_id["license"] == "CC-BY-4.0"


def test_unknown_project_says_what_to_do(client: TestClient):
    initialise(client)
    message = call_tool_expecting_error(client, "get_project", {"project": "nope"})

    assert "list_projects" in message


def test_reading_a_text_file(client: TestClient):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray", "notes": "# Tray\n\nHand notes."})

    content = call_tool(client, "read_project_file", {"project": "tray", "rel_path": "notes.md"})
    assert content["content"] == "# Tray\n\nHand notes."


def test_reading_outside_the_project_is_refused(client: TestClient):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})

    message = call_tool_expecting_error(
        client, "read_project_file", {"project": "tray", "rel_path": "../../etc/passwd"}
    )
    assert "escapes" in message


def test_library_info_explains_how_to_get_files_in(client: TestClient):
    initialise(client)
    info = call_tool(client, "library_info")

    assert info["counts"]["projects"] == 0
    # Both routes, and the concrete test for which one applies.
    assert "rescan_library" in info["note"]
    assert "forge-upload.py" in info["note"]
    # A real library says nothing about being a demo.
    assert "demo_instance" not in info


# ------------------------------------------- not mistaking the demo for real


@pytest.fixture
def demo_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_DEMO_SEED", "true")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_the_demo_instance_says_so_at_the_handshake(demo_client: TestClient):
    """An agent should learn this before it uploads anything, not after."""
    info = initialise(demo_client)

    assert "THROWAWAY DEMO INSTANCE" in info["instructions"]
    assert "8000" in info["instructions"]
    # Visible even in the server name, which clients display.
    assert info["serverInfo"]["name"] == "forge-ledger-demo"


def test_the_demo_instance_flags_itself_in_library_info(demo_client: TestClient):
    initialise(demo_client)
    info = call_tool(demo_client, "library_info")

    assert info["demo_instance"] is True
    assert "does not" in info["warning"] or "nothing imported here" in info["warning"]


def test_health_reports_a_demo_instance(demo_client: TestClient):
    assert demo_client.get("/api/health").json()["demo_instance"] is True


def test_health_does_not_cry_demo_on_a_real_library(client: TestClient):
    # Separate test on purpose: both fixtures set env and clear the settings
    # cache, so taking them together would just measure fixture ordering.
    assert client.get("/api/health").json()["demo_instance"] is False


def test_upload_file_steers_away_from_inlining_base64(client: TestClient):
    """The token cost of inline base64 is the whole reason the script exists."""
    initialise(client)
    tools = {t["name"]: t for t in rpc(client, "tools/list")["tools"]}

    description = tools["upload_file"]["description"]
    assert "forge-upload.py" in description
    assert "rescan_library" in description

    instructions = initialise(client)["instructions"]
    assert "forge-upload.py" in instructions
    assert "rescan_library" in instructions


# ------------------------------------------------------------------- write


def test_create_project_writes_a_real_folder(client: TestClient):
    initialise(client)
    created = call_tool(
        client,
        "create_project",
        {"title": "Cable Clip", "tags": ["desk"], "status": "designing", "notes": "# Clip"},
    )

    library = Path(client.get("/api/health").json()["library_path"])
    directory = library / "cable-clip"
    assert directory.is_dir()

    doc = yaml.safe_load((directory / "project.yaml").read_text())
    assert doc["title"] == "Cable Clip"
    assert doc["status"] == "designing"
    assert doc["tags"] == ["desk"]
    assert (directory / "notes.md").read_text() == "# Clip"
    assert created["slug"] == "cable-clip"


def test_invalid_status_is_rejected(client: TestClient):
    initialise(client)
    message = call_tool_expecting_error(
        client, "create_project", {"title": "X", "status": "nonsense"}
    )
    assert "status must be one of" in message


def test_update_can_retitle_and_move_the_folder(client: TestClient):
    initialise(client)
    created = call_tool(client, "create_project", {"title": "Old Name"})

    call_tool(
        client, "update_project", {"project": created["id"], "title": "New Name", "tags": ["a"]}
    )

    library = Path(client.get("/api/health").json()["library_path"])
    assert (library / "new-name").is_dir()
    assert not (library / "old-name").exists()
    # The id survives the move, which is the whole point of the ULID.
    assert call_tool(client, "get_project", {"project": created["id"]})["title"] == "New Name"


# ------------------------------------------------------------------ upload


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_uploading_a_folder_worth_of_files(client: TestClient, tmp_path: Path):
    """The headline workflow: an agent importing a folder from the user's machine."""
    initialise(client)
    call_tool(client, "create_project", {"title": "Desk Organizer"})

    step = call_tool(
        client,
        "upload_file",
        {
            "project": "desk-organizer",
            "filename": "tray.step",
            "content_base64": b64(b"ISO-10303-21;\n"),
        },
    )
    stl = call_tool(
        client,
        "upload_file",
        {
            "project": "desk-organizer",
            "filename": "tray.stl",
            "content_base64": b64(b"solid tray\nendsolid\n"),
            "model_name": "tray",
        },
    )
    photo = call_tool(
        client,
        "upload_file",
        {
            "project": "desk-organizer",
            "filename": "hero.png",
            "content_base64": b64(_png_bytes()),
            "variant": "web",
        },
    )
    psd = call_tool(
        client,
        "upload_file",
        {
            "project": "desk-organizer",
            "filename": "hero.psd",
            "content_base64": b64(b"8BPS"),
        },
    )
    sliced = call_tool(
        client,
        "upload_file",
        {
            "project": "desk-organizer",
            "filename": "tray.gcode.3mf",
            "content_base64": b64(write_sliced_3mf(tmp_path / "t.gcode.3mf").read_bytes()),
        },
    )

    assert step["rel_path"] == "models/sources/tray.step"
    assert stl["rel_path"] == "models/tray/tray.stl"
    assert photo["rel_path"] == "images/photos/hero.png"
    assert psd["rel_path"] == "images/sources/hero.psd"
    assert sliced["filed_as"] == "print"

    detail = call_tool(client, "get_project", {"project": "desk-organizer"})
    # The sliced file became a print job with settings parsed out of it.
    assert len(detail["prints"]) == 1
    assert detail["prints"][0]["estimated_s"] == 4521
    assert detail["prints"][0]["settings"]["infill_pattern"] == "gyroid"
    # The image kept the variant it was uploaded with, and found its source.
    hero = next(i for i in detail["images"] if i["path"].endswith("hero.png"))
    assert hero["variant"] == "web"
    assert hero["source"] == "images/sources/hero.psd"


def test_meshes_default_to_a_folder_named_after_the_file(client: TestClient):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})

    result = call_tool(
        client,
        "upload_file",
        {
            "project": "tray",
            "filename": "Lid Part.stl",
            "content_base64": b64(b"solid lid\n"),
        },
    )
    assert result["rel_path"] == "models/lid-part/lid-part.stl"


def test_uploaded_cad_is_unfiled_until_attached(client: TestClient):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})
    call_tool(
        client,
        "upload_file",
        {"project": "tray", "filename": "tray.step", "content_base64": b64(b"ISO-10303-21;\n")},
    )

    assert call_tool(client, "get_project", {"project": "tray"})["unfiled"] == [
        "models/sources/tray.step"
    ]

    result = call_tool(
        client,
        "attach_files_to_model",
        {"project": "tray", "model_name": "tray", "rel_paths": ["models/sources/tray.step"]},
    )
    assert result["unfiled_remaining"] == []


def test_a_traversing_filename_cannot_escape(client: TestClient):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})

    call_tool(
        client,
        "upload_file",
        {
            "project": "tray",
            "filename": "../../../evil.step",
            "content_base64": b64(b"ISO-10303-21;\n"),
        },
    )

    library = Path(client.get("/api/health").json()["library_path"])
    assert not (library.parent / "evil.step").exists()
    assert (library / "tray" / "models" / "sources" / "evil.step").is_file()


def test_bad_base64_is_reported_clearly(client: TestClient):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})

    message = call_tool_expecting_error(
        client,
        "upload_file",
        {"project": "tray", "filename": "tray.stl", "content_base64": "not base64!!"},
    )
    assert "base64" in message


def test_an_oversized_file_suggests_the_alternative(client: TestClient, monkeypatch):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})
    monkeypatch.setattr("app.mcp_server.MAX_UPLOAD_BYTES", 1024)

    message = call_tool_expecting_error(
        client,
        "upload_file",
        {
            "project": "tray",
            "filename": "big.stl",
            "content_base64": b64(b"x" * 4096),
        },
    )
    assert "rescan_library" in message


def test_print_history_surfaces_the_failure_log(client: TestClient, tmp_path: Path):
    initialise(client)
    call_tool(client, "create_project", {"title": "Tray"})
    call_tool(
        client,
        "upload_file",
        {
            "project": "tray",
            "filename": "tray.gcode.3mf",
            "content_base64": b64(write_sliced_3mf(tmp_path / "t.gcode.3mf").read_bytes()),
        },
    )

    print_id = call_tool(client, "get_project", {"project": "tray"})["prints"][0]["id"]
    client.patch(
        f"/api/prints/{print_id}",
        json={"status": "failed", "failure_reason": "warped", "failure_fix": "brim"},
    )

    history = call_tool(client, "print_history")
    assert history["stats"]["counts"]["failed"] == 1
    assert history["failures"][0]["reason"] == "warped"
    assert history["failures"][0]["fix"] == "brim"
