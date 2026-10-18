"""Pruebas de integración en proceso: MCP con su cliente oficial y la Agent Card A2A."""

import json

from mcp import Client
from starlette.testclient import TestClient


async def test_mcp_exposes_all_tools_and_runs_them(monkeypatch):
    monkeypatch.setenv("PARACHUTE_TODAY", "2026-10-17")
    from mcp_server import server

    async with Client(server.mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"buscar_faq", "consultar_clima_salto", "agendar_cita", "listar_citas", "fecha_actual"}
        result = await client.call_tool("consultar_clima_salto", {"fecha": "2027-01-01"})
        text = "".join(getattr(c, "text", "") for c in result.content)
        payload = result.structured_content or json.loads(text)
        payload = payload.get("result", payload)
        assert payload["status"] == "out_of_range"


def test_a2a_agent_card_lists_skills():
    from a2a_server.server import build_app

    with TestClient(build_app()) as http:
        card = http.get("/.well-known/agent-card.json").json()
    assert card["name"].startswith("Parachute")
    assert {s["id"] for s in card["skills"]} == {"faq", "clima-salto", "agendar-cita"}
