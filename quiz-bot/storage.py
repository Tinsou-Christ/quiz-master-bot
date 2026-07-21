"""Persistance simple JSON pour les scores et l'etat du quiz par chat."""
import json
import os
import threading
from typing import Any, Dict

_LOCK = threading.Lock()

DATA_DIR = os.environ.get("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "quiz_data.json")


def _load() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def get_chat(chat_id: int) -> Dict[str, Any]:
    """Retourne (et cree si besoin) l'etat d'un chat."""
    with _LOCK:
        data = _load()
        key = str(chat_id)
        if key not in data:
            data[key] = {
                "active": False,          # quiz active ?
                "host_id": None,          # id du poseur de questions
                "host_name": None,
                "players": {},            # user_id(str) -> {"name": str, "score": int}
                "round": None,            # round en cours: {"question": str, "answers": [{"user_id","name","text","ts"}]}
            }
            _save(data)
        return data[key]


def update_chat(chat_id: int, mutator) -> Dict[str, Any]:
    """Applique une fonction mutator(chat_state) et sauvegarde."""
    with _LOCK:
        data = _load()
        key = str(chat_id)
        if key not in data:
            data[key] = {
                "active": False,
                "host_id": None,
                "host_name": None,
                "players": {},
                "round": None,
            }
        mutator(data[key])
        _save(data)
        return data[key]
