from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .app import RowletApp
from .config import DEFAULT_CONFIG_PATH, load_config
from .mock_client import MockGpuClient
from .ssh_client import SshGpuClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rowlet desktop monitor for remote NVIDIA GPU idleness.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json.")
    parser.add_argument("--mock", action="store_true", help="Run with local mock GPU data.")
    parser.add_argument("--once", action="store_true", help="Poll once and print JSON instead of opening UI.")
    parser.add_argument("--test-ssh", action="store_true", help="Test SSH and nvidia-smi availability.")
    args = parser.parse_args(argv)

    try:
        config_path = Path(args.config).resolve()
        config = load_config(config_path)
        client = MockGpuClient() if args.mock else SshGpuClient(config)

        if args.test_ssh:
            if args.mock:
                print("mock mode: SSH test skipped")
                return 0
            result = client.test_connection()
            print(result.stdout.strip())
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            return result.returncode

        if args.once:
            status = client.poll()
            print(
                json.dumps(
                    {
                        "any_idle": status.any_idle,
                        "idle_indices": status.idle_indices,
                        "busy_indices": status.busy_indices,
                        "message": status.bubble_message(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        RowletApp(config, client, config_path=config_path, use_mock=args.mock).run()
        return 0
    except Exception as exc:
        print(f"gpu-rowlet-monitor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
