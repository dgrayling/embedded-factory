"""Board registry access."""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

_REGISTRY = Path(__file__).resolve().parents[1] / "boards.yaml"


@cache
def load_boards() -> dict[str, dict]:
    return yaml.safe_load(_REGISTRY.read_text())


def board_info(board: str) -> dict:
    boards = load_boards()
    if board not in boards:
        raise KeyError(f"unknown board '{board}'; known: {', '.join(boards)}")
    return {"id": board, **boards[board]}


def has_simulation(board: str) -> bool:
    return board_info(board).get("renode_platform") is not None
