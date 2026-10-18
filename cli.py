"""Terminal de Parachute S.A. como cliente A2A.

Descubre el agente por su Agent Card y conversa con él por JSON-RPC (streaming).

    python cli.py                         # usa A2A_URL o http://127.0.0.1:9999
    python cli.py "¿Cuál es el peso máximo?"   # una sola consulta
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx
from google.protobuf.json_format import MessageToDict
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

A2A_URL = os.environ.get("A2A_URL", "http://127.0.0.1:9999")
DIM, BOLD, CYAN, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[0m"


async def ask(client, text: str, context_id: str) -> None:
    message = new_text_message(text, context_id=context_id, role=Role.ROLE_USER)
    async for response in client.send_message(SendMessageRequest(message=message)):
        if response.HasField("status_update"):
            status = response.status_update.status
            note = get_message_text(status.message) if status.HasField("message") else ""
            state = TaskState.Name(status.state).removeprefix("TASK_STATE_").lower()
            print(f"{DIM}  · [{state}] {note}{RESET}")
        elif response.HasField("artifact_update"):
            artifact = response.artifact_update.artifact
            route = MessageToDict(artifact.metadata).get("route")
            if route:
                print(f"{DIM}  · ruta: {' → '.join(route)}{RESET}")
            print(f"{CYAN}{BOLD}Parachute>{RESET} {get_artifact_text(artifact)}")
        elif response.HasField("message"):
            print(f"{CYAN}{BOLD}Parachute>{RESET} {get_message_text(response.message)}")


async def main() -> None:
    async with httpx.AsyncClient(timeout=120) as http:
        card = await A2ACardResolver(http, A2A_URL).get_agent_card()
        client = await create_client(card, client_config=ClientConfig(streaming=True, httpx_client=http))
        print(f"{BOLD}Conectado a: {card.name}{RESET} ({A2A_URL})")
        for skill in card.skills:
            print(f"  - {skill.name}: {skill.description}")
        context_id = str(uuid.uuid4())
        if len(sys.argv) > 1:
            await ask(client, " ".join(sys.argv[1:]), context_id)
            return
        print("Escriba su consulta ('salir' para terminar).")
        while True:
            try:
                text = (await asyncio.to_thread(input, f"{BOLD}Usted>{RESET} ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in {"salir", "exit", "quit"}:
                break
            if text:
                await ask(client, text, context_id)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
