"""Servidor A2A que expone el Multi Agent System de Parachute S.A. como un solo agente.

    python -m a2a_server.server            # http://127.0.0.1:9999/
    curl http://127.0.0.1:9999/.well-known/agent-card.json
"""

from __future__ import annotations

import logging
import os
import time

import uvicorn
from a2a.helpers import new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill
from starlette.applications import Starlette

from .mas import MCP_URL, MultiAgentSystem, build_mas

logging.basicConfig(level=logging.INFO, format="%(asctime)s [A2A] %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("parachute.a2a")
for noisy in ("httpx", "httpx2", "mcp.client", "openai", "openai.agents"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

HOST = os.environ.get("A2A_HOST", "127.0.0.1")
PORT = int(os.environ.get("A2A_PORT", "9999"))
PUBLIC_URL = os.environ.get("A2A_PUBLIC_URL", f"http://{HOST}:{PORT}/")

SKILLS = [
    AgentSkill(
        id="faq",
        name="FAQ del evento",
        description="Responde preguntas sobre el evento de paracaidismo con el FAQ oficial, citando faq_id.",
        tags=["faq", "rag", "parachute"],
        examples=["¿Cuál es el peso máximo para saltar?", "¿Qué edad mínima necesito?"],
    ),
    AgentSkill(
        id="clima-salto",
        name="Clima de salto",
        description="Evalúa con Open-Meteo si una fecha (hasta 16 días) es favorable, marginal o no segura.",
        tags=["clima", "open-meteo", "seguridad"],
        examples=["¿Cómo está el clima para saltar mañana?", "¿Es apto el 2026-10-25?"],
    ),
    AgentSkill(
        id="agendar-cita",
        name="Agendar cita de salto",
        description="Agenda o lista citas de salto, verificando el clima antes de reservar.",
        tags=["citas", "agenda"],
        examples=["Agendar una cita el 2026-10-25 a nombre de Ana López", "Ver mis citas"],
    ),
]


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="Parachute S.A. — Asistente",
        description="Agente único que encapsula el MAS de Parachute S.A. (coordinador + FAQ + clima/citas) "
                    "y usa herramientas de un servidor MCP.",
        supported_interfaces=[AgentInterface(url=PUBLIC_URL, protocol_binding="JSONRPC", protocol_version="1.0")],
        provider=AgentProvider(organization="Parachute S.A.", url="https://parachutesa.gt"),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=SKILLS,
    )


class ParachuteAgentExecutor(AgentExecutor):
    """Puente A2A → MAS: crea la tarea, informa progreso y publica la respuesta como artefacto."""

    def __init__(self, mas: MultiAgentSystem):
        self.mas = mas

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        log.info("tarea %s (contexto %s) ← %r", task.id[:8], task.context_id[:8], text)
        await updater.start_work(new_text_message("Coordinador analizando la solicitud…",
                                                  context_id=task.context_id, task_id=task.id))
        started = time.perf_counter()
        try:
            reply = await self.mas.run(text, task.context_id)
        except Exception as exc:  # el cliente recibe un estado failed legible
            log.exception("tarea %s falló", task.id[:8])
            await updater.failed(new_text_message(f"Error del agente: {exc}", context_id=task.context_id,
                                                  task_id=task.id))
            return
        log.info("tarea %s → ruta=%s (%.0f ms)", task.id[:8], " → ".join(reply.route),
                 (time.perf_counter() - started) * 1000)
        await updater.add_artifact([new_text_part(reply.text)], name="respuesta",
                                   metadata={"route": reply.route, "engine": self.mas.name})
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def build_app() -> Starlette:
    card = build_agent_card()
    mas = build_mas()
    log.info("MAS '%s' usando MCP en %s", mas.name, MCP_URL)
    handler = DefaultRequestHandler(agent_executor=ParachuteAgentExecutor(mas), task_store=InMemoryTaskStore(),
                                    agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")])


def main() -> None:
    log.info("Servidor A2A en %s (agent card: %s.well-known/agent-card.json)", PUBLIC_URL, PUBLIC_URL)
    uvicorn.run(build_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
