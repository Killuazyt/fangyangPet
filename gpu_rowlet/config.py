from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
BUNDLED_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


STATE_ROWS = {
    "idle": 0,
    "running-right": 1,
    "running-left": 2,
    "waving": 3,
    "jumping": 4,
    "failed": 5,
    "waiting": 6,
    "running": 7,
    "review": 8,
}


FORBIDDEN_SECRET_KEYS = {
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
}


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    username: str
    auth_method: str
    identity_file: Path | None
    spritesheet_path: Path
    poll_interval_seconds: int = 30
    ssh_connect_timeout_seconds: int = 8
    idle_util_threshold: int = 5
    idle_memory_threshold_mb: int = 512
    idle_state: str = "idle"
    active_state: str = "idle"
    error_state: str = "failed"
    animation_ms: int = 500
    bubble_repeat_seconds: int = 300
    window_scale: float = 0.5
    credential_service: str = "gpu-rowlet-monitor"

    @property
    def is_connection_configured(self) -> bool:
        if not self.host or not self.username:
            return False
        if self.auth_method == "key":
            return bool(self.identity_file)
        return self.auth_method == "password"

    @property
    def credential_key(self) -> str:
        return f"{self.username}@{self.host}:{self.port}"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    config_path = Path(path).resolve()
    if not config_path.exists():
        save_config(default_config(config_path.parent), config_path)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    _reject_secret_fields(raw)

    base_dir = config_path.parent
    auth_method = str(raw.get("auth_method", "key")).lower()
    if auth_method not in {"key", "password"}:
        raise ValueError("auth_method must be 'key' or 'password'.")
    idle_state = str(raw.get("idle_state", "idle"))
    active_state = str(raw.get("active_state", "idle"))
    error_state = str(raw.get("error_state", "failed"))
    for state_name in [idle_state, active_state, error_state]:
        if state_name not in STATE_ROWS:
            choices = ", ".join(STATE_ROWS)
            raise ValueError(f"Unknown animation state '{state_name}'. Choices: {choices}")

    spritesheet = raw.get("spritesheet_path", default_spritesheet_path(base_dir))
    identity_file = str(raw.get("identity_file", "")).strip()
    return AppConfig(
        host=str(raw.get("host", "")).strip(),
        port=int(raw.get("port", 22)),
        username=str(raw.get("username", "")).strip(),
        auth_method=auth_method,
        identity_file=_expand_path(identity_file, base_dir) if identity_file else None,
        spritesheet_path=_expand_path(spritesheet, base_dir),
        poll_interval_seconds=max(5, int(raw.get("poll_interval_seconds", 30))),
        ssh_connect_timeout_seconds=max(3, int(raw.get("ssh_connect_timeout_seconds", 8))),
        idle_util_threshold=max(0, int(raw.get("idle_util_threshold", 5))),
        idle_memory_threshold_mb=max(0, int(raw.get("idle_memory_threshold_mb", 512))),
        idle_state=idle_state,
        active_state=active_state,
        error_state=error_state,
        animation_ms=max(40, int(raw.get("animation_ms", 500))),
        bubble_repeat_seconds=max(30, int(raw.get("bubble_repeat_seconds", 300))),
        window_scale=max(0.25, float(raw.get("window_scale", 0.5))),
        credential_service=str(raw.get("credential_service", "gpu-rowlet-monitor")),
    )


def default_config(base_dir: Path = PROJECT_ROOT) -> AppConfig:
    return AppConfig(
        host="",
        port=22,
        username="",
        auth_method="key",
        identity_file=None,
        spritesheet_path=default_spritesheet_path(base_dir),
        idle_state="idle",
    )


def default_spritesheet_path(base_dir: Path = PROJECT_ROOT) -> Path:
    candidates = [
        base_dir / "spritesheet.webp",
        BUNDLED_ROOT / "spritesheet.webp",
        base_dir / ".." / "rowlet" / "spritesheet.webp",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (base_dir / "spritesheet.webp").resolve()


def save_config(config: AppConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    config_path = Path(path).resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "auth_method": config.auth_method,
        "identity_file": str(config.identity_file) if config.identity_file else "",
        "spritesheet_path": _path_for_json(config.spritesheet_path, config_path.parent),
        "poll_interval_seconds": config.poll_interval_seconds,
        "ssh_connect_timeout_seconds": config.ssh_connect_timeout_seconds,
        "idle_util_threshold": config.idle_util_threshold,
        "idle_memory_threshold_mb": config.idle_memory_threshold_mb,
        "idle_state": config.idle_state,
        "active_state": config.active_state,
        "error_state": config.error_state,
        "animation_ms": config.animation_ms,
        "bubble_repeat_seconds": config.bubble_repeat_seconds,
        "window_scale": config.window_scale,
        "credential_service": config.credential_service,
    }
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _expand_path(value: str, base_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    path = Path(expanded)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _path_for_json(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def _reject_secret_fields(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() in FORBIDDEN_SECRET_KEYS:
                raise ValueError(
                    f"Refusing to load secret-like config field '{key_path}'. "
                    "Use SSH keys or Windows Credential Manager instead of plaintext secrets."
                )
            _reject_secret_fields(child, key_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")
