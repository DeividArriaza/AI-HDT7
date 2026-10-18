"""Multi Agent System de Parachute S.A. consumiendo herramientas vía MCP.

Arquitectura jerárquica (HDT-5): un coordinador delega en dos especialistas
expuestos como herramientas (`as_tool`). Ninguno implementa lógica de negocio:
todas las capacidades llegan del servidor MCP por Streamable HTTP.

    Coordinador ─┬─ Agente FAQ ─────────────── MCP: buscar_faq
                 └─ Agente Clima y Citas ───── MCP: fecha_actual, consultar_clima_salto,
                                                    agendar_cita, listar_citas

Hay dos implementaciones con el mismo contrato `run(text, context_id)`:
- `OpenAIAgentsMAS`: OpenAI Agents SDK (requiere OPENAI_API_KEY).
- `RuleBasedMAS`: enrutamiento determinista sin LLM, útil sin credenciales.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("parachute.mas")


def load_env_file(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    """Carga KEY=VALUE de `.env` sin sobrescribir variables ya exportadas."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
FAQ_TOOLS = ["buscar_faq"]
SCHEDULER_TOOLS = ["fecha_actual", "consultar_clima_salto", "agendar_cita", "listar_citas"]


@dataclass
class AgentReply:
    text: str
    route: list[str] = field(default_factory=list)  # agentes que intervinieron


class MultiAgentSystem(Protocol):
    name: str

    async def run(self, text: str, context_id: str) -> AgentReply: ...


# --------------------------------------------------------------------------- OpenAI Agents SDK

COORDINATOR_PROMPT = """Eres el coordinador de atención de Parachute S.A. (paracaidismo tándem, Guatemala).
Responde en español, breve y claro. No inventes datos: delega SIEMPRE en un especialista,
incluso si crees saber la respuesta por la conversación (por ejemplo, para listar citas o
verificar fechas): la información vigente solo la tienen las herramientas.
- Preguntas sobre el evento, precios, requisitos, logística o políticas → `agente_faq`.
- Clima de una fecha, agendar citas o consultar citas → `agente_clima_citas`.
Pasa al especialista toda la información relevante acumulada en la conversación (nombre, fecha,
personas) y pídele explícitamente la acción: "agendar", "consultar clima" o "listar citas".
Solo confirma una cita si el especialista devuelve un identificador CITA-….
Conserva las citas (faq_id o URL de Open-Meteo) y los valores exactos que devuelvan los especialistas."""

FAQ_PROMPT = """Eres el especialista del FAQ de Parachute S.A. Usa SIEMPRE `buscar_faq`.
Responde solo con lo que diga el FAQ y cita el `faq_id` como [FAQ-xxx].
Si `found` es false, di que no hay respuesta sustentada en el FAQ."""

SCHEDULER_PROMPT = """Eres el especialista de clima y citas de Parachute S.A.
- Resuelve fechas relativas (hoy, mañana, el sábado) con la fecha de hoy indicada abajo.
- Cualquier fecha entre hoy y el último día con pronóstico es válida: consúltala, no la rechaces.
- Para clima usa `consultar_clima_salto` SOLO con la fecha que pidió el usuario; no explores otras
  fechas por tu cuenta (sugiere que el usuario indique otra).
- Para reservar DEBES llamar `agendar_cita` (ya revisa el clima). Nunca digas que una cita quedó
  agendada si `agendar_cita` no devolvió `booked: true`; incluye el `appointment_id` (CITA-…).
- Para mostrar citas DEBES llamar `listar_citas`.
- Para reservar necesitas nombre y fecha; si falta algo, pídelo.
- Si la fecha supera los 16 días de pronóstico, explica que no se puede y sugiere una fecha válida.
- Estados: favorable = ideal; marginal = solo tándem experimentado; rejected = NO SEGURO;
  unavailable = Open-Meteo no tiene datos (no se puede evaluar ni agendar).
Incluye los valores con las unidades de `units` (viento en km/h), la ubicación de `location`
(no inventes otra) y cita la URL de Open-Meteo."""


class OpenAIAgentsMAS:
    name = "openai-agents"

    def __init__(self, mcp_url: str = MCP_URL):
        self.mcp_url = mcp_url
        self.model = self._build_model()
        self._history: dict[str, list[Any]] = {}

    def _build_model(self):
        """OpenAI directo, o cualquier proveedor compatible (Groq) vía Chat Completions."""
        groq_key = os.environ.get("GROQ_API_KEY")
        if not groq_key:
            return os.environ.get("OPENAI_MODEL")
        from agents import OpenAIChatCompletionsModel, set_tracing_disabled
        from openai import AsyncOpenAI

        set_tracing_disabled(True)  # el tracing sube trazas a OpenAI; no aplica con Groq
        model = os.environ.get("MODEL", "openai/gpt-oss-120b")
        self.name = f"openai-agents (groq: {model})"
        client = AsyncOpenAI(base_url=os.environ.get("BASE_URL", "https://api.groq.com/openai/v1"), api_key=groq_key,
                             max_retries=6)  # el plan gratuito de Groq limita tokens por minuto (429)
        return OpenAIChatCompletionsModel(model=model, openai_client=client)

    def _mcp(self, name: str, tools: list[str]):
        from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter

        return MCPServerStreamableHttp(
            params={"url": self.mcp_url, "timeout": 30},
            name=name,
            cache_tools_list=True,
            client_session_timeout_seconds=30,
            tool_filter=create_static_tool_filter(allowed_tool_names=tools),
        )

    async def run(self, text: str, context_id: str) -> AgentReply:
        from agents import Agent, ModelSettings, Runner

        # La fecha de referencia viene del propio MCP para que el LLM no la adivine.
        dates = await McpTools(self.mcp_url).call("fecha_actual")
        today = f"\nHoy es {dates['today']}; el último día con pronóstico es {dates['last_forecast_day']}."
        extra = {"model": self.model} if self.model else {}
        settings = ModelSettings(temperature=0.2)
        async with self._mcp("mcp-faq", FAQ_TOOLS) as faq_mcp, self._mcp("mcp-citas", SCHEDULER_TOOLS) as sched_mcp:
            faq_agent = Agent(name="Agente FAQ", instructions=FAQ_PROMPT, mcp_servers=[faq_mcp],
                              model_settings=settings, **extra)
            scheduler = Agent(name="Agente Clima y Citas", instructions=SCHEDULER_PROMPT + today,
                              mcp_servers=[sched_mcp], model_settings=settings, **extra)
            coordinator = Agent(
                name="Coordinador Parachute",
                instructions=COORDINATOR_PROMPT + today,
                tools=[
                    faq_agent.as_tool("agente_faq", "Responde preguntas con el FAQ oficial de Parachute S.A."),
                    scheduler.as_tool("agente_clima_citas",
                                      "Evalúa el clima de salto con Open-Meteo y agenda o lista citas."),
                ],
                model_settings=settings,
                **extra,
            )
            history = self._history.get(context_id, [])
            for attempt in (1, 2):  # Groq devuelve errores 400 transitorios ocasionales
                try:
                    result = await Runner.run(coordinator, history + [{"role": "user", "content": text}])
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise
                    log.warning("reintentando tras error del modelo: %s", exc)
        self._history[context_id] = _last_turns(result.to_input_list(), turns=4)
        route = [getattr(item.raw_item, "name", "?") for item in result.new_items if item.type == "tool_call_item"]
        return AgentReply(str(result.final_output), ["coordinador", *route])


def _last_turns(items: list[Any], turns: int) -> list[Any]:
    """Recorta el historial en un límite de mensaje del usuario para no separar llamada y resultado."""
    starts = [i for i, item in enumerate(items) if isinstance(item, dict) and item.get("role") == "user"]
    return items[starts[-turns]:] if len(starts) > turns else items


# --------------------------------------------------------------------------- Determinista (sin LLM)

DATE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DATE_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
NAME = re.compile(r"(?:a nombre de|mi nombre es|me llamo|soy)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)*)")
PEOPLE = re.compile(r"\b(\d+)\s+(?:personas|pasajeros|saltos|participantes)\b")
SCHEDULE_WORDS = ("agenda", "agendar", "reserv", "cita", "calendariz", "apart")
WEATHER_WORDS = ("clima", "tiempo", "viento", "lluvia", "llover", "nubes", "pronóstico", "pronostico", "apto")
WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


class McpTools:
    """Cliente MCP mínimo (Streamable HTTP) que decodifica la salida JSON de cada herramienta."""

    def __init__(self, url: str):
        self.url = url

    async def call(self, name: str, **arguments: Any) -> dict:
        from mcp import Client

        async with Client(self.url) as client:
            result = await client.call_tool(name, arguments)
        if result.structured_content:
            data = result.structured_content
            return data.get("result", data) if set(data) == {"result"} else data
        text = "".join(getattr(c, "text", "") for c in result.content)
        if getattr(result, "is_error", False):
            raise RuntimeError(text)
        return json.loads(text)


def parse_date(text: str, today: date) -> date | None:
    lower = text.lower()
    if m := DATE_ISO.search(text):
        return date.fromisoformat(m.group(1))
    if m := DATE_DMY.search(text):
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if "pasado mañana" in lower:
        return today + timedelta(days=2)
    if "mañana" in lower:
        return today + timedelta(days=1)
    if "hoy" in lower:
        return today
    for index, day in enumerate(WEEKDAYS):
        if day in lower or day.replace("á", "a").replace("é", "e") in lower:
            return today + timedelta(days=(index - today.weekday()) % 7 or 7)
    return None


class RuleBasedMAS:
    name = "rule-based"

    def __init__(self, mcp_url: str = MCP_URL):
        self.tools = McpTools(mcp_url)
        self._pending: dict[str, dict[str, Any]] = {}  # datos de reserva parcial por contexto

    async def run(self, text: str, context_id: str) -> AgentReply:
        lower = text.lower()
        pending = self._pending.get(context_id)
        mentions_date = parse_date(text, date.today()) is not None
        if pending or mentions_date or any(w in lower for w in SCHEDULE_WORDS + WEATHER_WORDS):
            return await self._scheduler_agent(text, context_id)
        return await self._faq_agent(text)

    async def _faq_agent(self, text: str) -> AgentReply:
        log.info("coordinador → agente FAQ")
        r = await self.tools.call("buscar_faq", pregunta=text)
        answer = f"{r['answer']} [{r['faq_id']}]" if r["found"] else r["answer"]
        return AgentReply(answer, ["coordinador", "agente_faq", "buscar_faq"])

    async def _scheduler_agent(self, text: str, context_id: str) -> AgentReply:
        log.info("coordinador → agente clima y citas")
        route = ["coordinador", "agente_clima_citas", "fecha_actual"]
        today = date.fromisoformat((await self.tools.call("fecha_actual"))["today"])
        lower = text.lower()
        state = self._pending.pop(context_id, {})
        wants_booking = state.get("booking") or any(w in lower for w in SCHEDULE_WORDS)

        if "mis citas" in lower or "listar" in lower or "ver citas" in lower:
            route.append("listar_citas")
            items = (await self.tools.call("listar_citas"))["appointments"]
            if not items:
                return AgentReply("No hay citas agendadas.", route)
            lines = [f"- {i['appointment_id']}: {i['name']}, {i['date']}, {i['people']} persona(s), "
                     f"clima {i['weather_status']}" for i in items]
            return AgentReply("Citas agendadas:\n" + "\n".join(lines), route)

        date_in_text = parse_date(text, today)
        requested = date_in_text or (date.fromisoformat(state["date"]) if state.get("date") else None)
        name_match = NAME.search(text)
        name = name_match.group(1) if name_match else state.get("name")
        if not name and state.get("booking") and not date_in_text and len(text.split()) <= 4:
            name = text.strip().title()  # respuesta corta a "¿a nombre de quién?"
        people_match = PEOPLE.search(lower)
        people = int(people_match.group(1)) if people_match else state.get("people", 1)

        if requested is None:
            self._pending[context_id] = {"booking": wants_booking, "name": name, "people": people}
            return AgentReply(f"¿Para qué fecha? Hoy es {today}; puedo revisar hasta "
                              f"{today + timedelta(days=15)} (formato YYYY-MM-DD).", route)
        if wants_booking and not name:
            self._pending[context_id] = {"booking": True, "date": requested.isoformat(), "people": people}
            return AgentReply("¿A nombre de quién agendo la cita?", route)

        if wants_booking:
            route.append("agendar_cita")
            r = await self.tools.call("agendar_cita", nombre=name, fecha=requested.isoformat(), personas=people)
            source = r.get("weather", {}).get("citation")
            return AgentReply(r["message"] + (f"\nFuente: {source}" if source else ""), route)

        route.append("consultar_clima_salto")
        r = await self.tools.call("consultar_clima_salto", fecha=requested.isoformat())
        if r["status"] == "out_of_range":
            return AgentReply(r["error"], route)
        v = r["values"]
        if r["status"] == "unavailable":
            return AgentReply(f"{r['date']}: sin pronóstico completo — {'; '.join(r['reasons'])}.", route)
        verdict = {"favorable": "FAVORABLE para saltar", "marginal": "MARGINAL (solo tándem experimentado)",
                   "rejected": "NO SEGURO para saltar"}[r["status"]]
        detail = "; ".join(r["reasons"]) or "todas las variables en rango ideal"
        return AgentReply(
            f"{r['date']}: {verdict} — {detail}.\n"
            f"Viento {v['wind_speed_10m']} km/h, ráfagas {v['wind_gust_10m']} km/h, lluvia {v['precipitation']} mm, "
            f"nubosidad {v['cloud_cover']}%, temperatura máx. {v['temperature_2m']} °C.\nFuente: {r['citation']}",
            route,
        )


def build_mas() -> MultiAgentSystem:
    mode = os.environ.get("PARACHUTE_AGENT_MODE", "auto")
    has_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")
    if mode == "openai" or (mode == "auto" and has_key):
        return OpenAIAgentsMAS()
    return RuleBasedMAS()
