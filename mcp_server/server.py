"""Servidor MCP de Parachute S.A. sobre Streamable HTTP.

Concentra en un solo servidor las herramientas que antes vivían dentro de cada
agente de HDT-5: FAQ, clima de salto y agenda de citas. Cada ejecución de
herramienta emite un log con argumentos, resultado y duración.

    python -m mcp_server.server            # http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import os
import time
from datetime import date

from mcp.server.mcpserver import MCPServer

from parachute import build_services
from parachute.weather import DateOutOfRange

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MCP] %(levelname)s %(message)s")
log = logging.getLogger("parachute.mcp")

faq, weather, book = build_services()
mcp = MCPServer(
    name="parachute-tools",
    title="Parachute S.A. — herramientas",
    instructions="Herramientas de Parachute S.A.: FAQ del evento, clima de salto (Open-Meteo) y agenda de citas.",
    version="1.0.0",
)
_calls = itertools.count(1)


def logged(fn):
    """Registra cada ejecución de herramienta: entrada, salida y duración."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        call_id = next(_calls)
        log.info("tool #%d → %s args=%s", call_id, fn.__name__, json.dumps(kwargs, ensure_ascii=False))
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            log.warning("tool #%d ✗ %s error=%s (%.1f ms)", call_id, fn.__name__, exc,
                        (time.perf_counter() - started) * 1000)
            raise
        summary = json.dumps(result, ensure_ascii=False)
        log.info("tool #%d ✓ %s result=%s (%.1f ms)", call_id, fn.__name__,
                 summary if len(summary) < 300 else summary[:300] + "…", (time.perf_counter() - started) * 1000)
        return result

    return wrapper


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Fecha inválida '{value}'; use el formato YYYY-MM-DD.") from exc


@mcp.tool()
@logged
def buscar_faq(pregunta: str) -> dict:
    """Busca la respuesta oficial en el FAQ de Parachute S.A. (precios, requisitos, logística, seguridad...).

    Devuelve `found`, la respuesta y su `faq_id`, que debe citarse.
    """
    return faq.search(pregunta).to_dict()


@mcp.tool()
@logged
def consultar_clima_salto(fecha: str) -> dict:
    """Evalúa con Open-Meteo si una fecha (YYYY-MM-DD) es apta para saltar en la zona de aterrizaje.

    Solo acepta fechas entre hoy y los próximos 16 días. Devuelve `status`
    (favorable | marginal | rejected), razones, valores medidos y la URL citada.
    """
    try:
        return weather.assess(_parse_date(fecha)).to_dict()
    except DateOutOfRange as exc:
        return {"status": "out_of_range", "error": str(exc), "today": weather.today.isoformat()}


@mcp.tool()
@logged
def agendar_cita(nombre: str, fecha: str, personas: int = 1) -> dict:
    """Agenda una cita de salto para `nombre` en `fecha` (YYYY-MM-DD) revisando primero el clima.

    Rechaza la reserva si el clima es NO SEGURO o si la fecha supera los 16 días de pronóstico.
    """
    try:
        return book.book(nombre, _parse_date(fecha), personas).to_dict()
    except DateOutOfRange as exc:
        return {"booked": False, "message": str(exc), "today": weather.today.isoformat()}


@mcp.tool()
@logged
def listar_citas(nombre: str | None = None) -> dict:
    """Lista las citas agendadas; opcionalmente filtra por nombre."""
    return {"appointments": book.list(nombre)}


@mcp.tool()
@logged
def fecha_actual() -> dict:
    """Devuelve la fecha de referencia del sistema y el último día con pronóstico disponible."""
    today = weather.today
    return {"today": today.isoformat(), "last_forecast_day": date.fromordinal(today.toordinal() + 16).isoformat()}


def main() -> None:
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8000"))
    log.info("Servidor MCP 'parachute-tools' en http://%s:%d/mcp (Streamable HTTP)", host, port)
    mcp.run("streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
