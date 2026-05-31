from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from .config import AppConfig
from .credentials import get_password
from .gpu import GpuStatus, status_from_remote_output


REMOTE_GPU_SCRIPT = r"""
set -u
echo __GPU__
nvidia-smi --query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits
echo __PROCS__
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || true
echo __PS__
PIDS=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | awk 'NF {print $1}' | sort -u)
for pid in $PIDS; do
  user=$(ps -o user= -p "$pid" 2>/dev/null | awk '{$1=$1; print}')
  comm=$(ps -o comm= -p "$pid" 2>/dev/null | awk '{$1=$1; print}')
  args=""
  if [ -r "/proc/$pid/cmdline" ]; then
    args=$(tr '\000' ' ' < "/proc/$pid/cmdline" | sed 's/[[:space:]]*$//')
  fi
  if [ -n "$pid" ]; then
    printf '%s|%s|%s|%s\n' "$pid" "$user" "$comm" "$args"
  fi
done
"""


@dataclass(frozen=True)
class SshResult:
    returncode: int
    stdout: str
    stderr: str


class SshGpuClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def poll(self) -> GpuStatus:
        if not self.config.is_connection_configured:
            raise RuntimeError(
                "\u8fd8\u6ca1\u6709\u914d\u7f6e\u670d\u52a1\u5668\u8fde\u63a5\u3002"
                "\u53f3\u952e Rowlet \u6253\u5f00\u201c\u8bbe\u7f6e\u201d\u540e\u586b\u5199 IP\u3001\u7aef\u53e3\u548c\u8d26\u53f7\u3002"
            )
        result = run_ssh_command(self.config, REMOTE_GPU_SCRIPT)
        if result.returncode != 0:
            raise RuntimeError(_format_ssh_error(result))
        return status_from_remote_output(
            result.stdout,
            idle_util_threshold=self.config.idle_util_threshold,
            idle_memory_threshold_mb=self.config.idle_memory_threshold_mb,
        )

    def test_connection(self) -> SshResult:
        command = "command -v nvidia-smi >/dev/null && nvidia-smi -L"
        return run_ssh_command(self.config, command)


def run_ssh_command(config: AppConfig, remote_command: str) -> SshResult:
    if not config.is_connection_configured:
        return SshResult(1, "", "connection is not configured")
    return run_paramiko_command(config, remote_command)


def run_paramiko_command(config: AppConfig, remote_command: str) -> SshResult:
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is not installed. Run: python -m pip install -r requirements.txt") from exc

    password = None
    key_filename = None
    if config.auth_method == "key":
        if not config.identity_file or not config.identity_file.exists():
            raise FileNotFoundError(f"SSH identity file not found: {config.identity_file}")
        key_filename = str(config.identity_file)
    else:
        password = get_password(config.credential_service, config.credential_key)
        if not password:
            raise RuntimeError(
                "\u6ca1\u6709\u627e\u5230\u4fdd\u5b58\u7684\u5bc6\u7801\u3002"
                "\u53f3\u952e Rowlet \u6253\u5f00\u201c\u8bbe\u7f6e\u201d\uff0c\u8f93\u5165\u5bc6\u7801\u5e76\u4fdd\u5b58\u3002"
            )

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            hostname=config.host,
            port=config.port,
            username=config.username,
            password=password,
            key_filename=key_filename,
            look_for_keys=False,
            allow_agent=False,
            timeout=config.ssh_connect_timeout_seconds,
            banner_timeout=config.ssh_connect_timeout_seconds,
            auth_timeout=config.ssh_connect_timeout_seconds,
        )
        stdin, stdout, stderr = client.exec_command(
            build_remote_shell_command(remote_command),
            timeout=config.ssh_connect_timeout_seconds + 30,
        )
        stdin.close()
        exit_status = stdout.channel.recv_exit_status()
        return SshResult(
            exit_status,
            stdout.read().decode("utf-8", "replace"),
            stderr.read().decode("utf-8", "replace"),
        )
    except Exception as exc:
        return SshResult(1, "", str(exc))
    finally:
        client.close()


def run_openssh_command(config: AppConfig, remote_command: str) -> SshResult:
    if not config.identity_file or not config.identity_file.exists():
        raise FileNotFoundError(f"SSH identity file not found: {config.identity_file}")

    ssh = shutil.which("ssh")
    if not ssh:
        raise FileNotFoundError("ssh.exe was not found in PATH.")

    command = build_ssh_command(config, remote_command, ssh_path=ssh)
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=config.ssh_connect_timeout_seconds + 30,
        check=False,
    )
    return SshResult(completed.returncode, completed.stdout, completed.stderr)


def build_ssh_command(config: AppConfig, remote_command: str, ssh_path: str = "ssh") -> list[str]:
    if not config.identity_file:
        raise ValueError("identity_file is required for OpenSSH command mode.")
    destination = f"{config.username}@{config.host}"
    return [
        ssh_path,
        "-i",
        str(Path(config.identity_file)),
        "-p",
        str(config.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        f"ConnectTimeout={config.ssh_connect_timeout_seconds}",
        destination,
        "bash",
        "-lc",
        json.dumps(remote_command),
    ]


def build_remote_shell_command(remote_command: str) -> str:
    return f"bash -lc {shlex.quote(remote_command)}"


def _format_ssh_error(result: SshResult) -> str:
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    detail = stderr or stdout or "no output"
    return f"SSH GPU query failed with exit code {result.returncode}: {detail}"
