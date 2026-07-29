"""Módulo compartido para consultas a LLM local (Ollama)."""

import json
import urllib.request
import urllib.error
from typing import Optional

_URL = "http://localhost:11434/api/generate"
_MODELO = "qwen2.5:7b"
_TIMEOUT = 30


def consultar(
    prompt: str,
    modelo: str = _MODELO,
    timeout: int = _TIMEOUT,
    sistema: str = "",
) -> Optional[str]:
    """Envía un prompt a Ollama y devuelve la respuesta como texto.

    Devuelve None si Ollama no está disponible o hay error.
    """
    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
    }
    if sistema:
        payload["system"] = sistema

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _URL,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resultado = json.loads(resp.read().decode("utf-8"))
            return resultado.get("response", "").strip() or None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def disponible() -> bool:
    """Comprueba si Ollama está corriendo."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
