"""`promo-agent pair | run | status | unpair`."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from promo_agent.client import ApiClient, BadPairingCode, Unreachable, api_origin
from promo_agent.credentials import Pairing, forget, load, save
from promo_agent.instance import AlreadyRunning, single_instance
from promo_agent.runner import Runner
from promo_agent.supervisor import Supervisor


def default_state_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PromoBotAgent"


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _pair(state_dir: Path, api: str) -> int:
    try:
        url = api_origin(api)
    except ValueError as error:
        print(f"Endereço inválido: {error}")
        return 1
    # Typed, not passed as an argument: it stays out of the shell history.
    code = input("Código de pareamento (mostrado no painel): ").strip()
    try:
        token, workspace_id = ApiClient(url).pair(code)
    except BadPairingCode:
        print("Código inválido, vencido ou já usado. Gere outro no painel.")
        return 1
    except Unreachable:
        print("Não foi possível falar com a API. Confira o endereço e tente de novo.")
        return 1
    save(state_dir, Pairing(url, workspace_id, token))
    print("Pareado. Agora execute: promo-agent run")
    return 0


def _run(state_dir: Path) -> int:
    pairing = load(state_dir)
    if pairing is None:
        print("Este PC ainda não está pareado. Execute: promo-agent pair --api <endereço>")
        return 1
    leaving: list[int] = []
    for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda number, _: leaving.append(number))

    def wait(seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not leaving and time.monotonic() < deadline:
            time.sleep(min(0.2, max(deadline - time.monotonic(), 0)))
        return bool(leaving)

    say(f"Agente em execução para {pairing.api_url}. Ctrl+C encerra e para o processo.")
    runner = Runner(ApiClient(pairing.api_url, pairing.token), Supervisor(), log=say)
    asked_to_leave = runner.run(wait)
    say("Agente encerrado.")
    return 0 if asked_to_leave else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promo-agent", description="Agente local do Promo Bot")
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pair", help="parear com um workspace").add_argument("--api", required=True)
    commands.add_parser("run", help="conectar e atender iniciar/parar")
    commands.add_parser("status", help="mostrar o pareamento deste PC")
    commands.add_parser("unpair", help="apagar o pareamento deste PC")
    args = parser.parse_args(argv)
    state_dir: Path = args.state_dir

    if args.command == "status":
        pairing = load(state_dir)
        if pairing is None:
            print("Não pareado.")
        else:
            print(f"Pareado com {pairing.api_url} (workspace {pairing.workspace_id}).")
        return 0
    try:
        lock: AbstractContextManager[None] = single_instance(state_dir)
        with lock:
            if args.command == "pair":
                return _pair(state_dir, args.api)
            if args.command == "run":
                return _run(state_dir)
            forget(state_dir)
            print("Pareamento apagado deste PC. Remova também o agente no painel.")
            return 0
    except AlreadyRunning:
        print("Já existe um agente em execução neste PC. Encerre-o antes.")
        return 1


def entrypoint() -> None:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    entrypoint()
