from __future__ import annotations

from dataclasses import dataclass, replace
import csv
import io
from typing import Iterable


@dataclass(frozen=True)
class GpuSample:
    index: int
    uuid: str | None
    utilization_gpu: int
    memory_used_mb: int
    memory_total_mb: int
    name: str | None = None


@dataclass(frozen=True)
class ComputeProcess:
    gpu_uuid: str
    pid: int | None
    process_name: str
    used_memory_mb: int | None
    user: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    user: str | None
    command_name: str | None
    command: str | None


@dataclass(frozen=True)
class GpuStatus:
    gpus: list[GpuSample]
    processes: list[ComputeProcess]
    idle_indices: list[int]
    busy_indices: list[int]
    process_counts_by_index: dict[int, int]

    @property
    def any_idle(self) -> bool:
        return bool(self.idle_indices)

    def bubble_message(self) -> str:
        if self.idle_indices:
            idle = _format_gpu_list(self.idle_indices)
            busy = _format_gpu_list(self.busy_indices)
            if self.busy_indices:
                return f"{idle} \u76ee\u524d\u7a7a\u95f2\uff1b{busy} \u6b63\u5fd9\u3002"
            return f"{idle} \u76ee\u524d\u7a7a\u95f2\u3002"
        if self.busy_indices:
            return f"{_format_gpu_list(self.busy_indices)} \u6b63\u5fd9\u3002"
        return "\u672a\u68c0\u6d4b\u5230 NVIDIA GPU\u3002"

    def processes_for_gpu(self, gpu: GpuSample) -> list[ComputeProcess]:
        return [proc for proc in self.processes if proc.gpu_uuid == gpu.uuid]


def parse_gpu_csv(text: str) -> list[GpuSample]:
    samples: list[GpuSample] = []
    for row in _csv_rows(text):
        if not row:
            continue
        name = None
        if len(row) >= 6:
            index, uuid, name, util, used, total = row[:6]
        elif len(row) >= 5:
            index, uuid, util, used, total = row[:5]
        elif len(row) >= 4:
            index, util, used, total = row[:4]
            uuid = ""
        else:
            continue
        samples.append(
            GpuSample(
                index=_int_or_zero(index),
                uuid=uuid.strip() or None,
                utilization_gpu=_int_or_zero(util),
                memory_used_mb=_int_or_zero(used),
                memory_total_mb=_int_or_zero(total),
                name=name.strip() if name else None,
            )
        )
    return samples


def parse_compute_process_csv(text: str) -> list[ComputeProcess]:
    processes: list[ComputeProcess] = []
    for row in _csv_rows(text):
        if len(row) < 4:
            continue
        gpu_uuid, pid, process_name, used_memory = row[:4]
        gpu_uuid = gpu_uuid.strip()
        if not gpu_uuid:
            continue
        processes.append(
            ComputeProcess(
                gpu_uuid=gpu_uuid,
                pid=_int_or_none(pid),
                process_name=process_name.strip(),
                used_memory_mb=_int_or_none(used_memory),
            )
        )
    return processes


def parse_process_info(text: str) -> dict[int, ProcessInfo]:
    infos: dict[int, ProcessInfo] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        pid = _int_or_none(parts[0])
        if pid is None:
            continue
        user = parts[1].strip() or None
        command_name = parts[2].strip() or None
        command = parts[3].strip() or None
        infos[pid] = ProcessInfo(pid=pid, user=user, command_name=command_name, command=command)
    return infos


def enrich_processes(
    processes: Iterable[ComputeProcess],
    process_info: dict[int, ProcessInfo],
) -> list[ComputeProcess]:
    enriched: list[ComputeProcess] = []
    for process in processes:
        info = process_info.get(process.pid or -1)
        if info:
            enriched.append(
                replace(
                    process,
                    user=info.user,
                    command=info.command or info.command_name,
                    process_name=process.process_name or info.command_name or "",
                )
            )
        else:
            enriched.append(process)
    return enriched


def evaluate_status(
    gpus: Iterable[GpuSample],
    processes: Iterable[ComputeProcess],
    idle_util_threshold: int,
    idle_memory_threshold_mb: int,
) -> GpuStatus:
    gpu_list = sorted(list(gpus), key=lambda gpu: gpu.index)
    process_list = list(processes)
    uuid_to_index = {gpu.uuid: gpu.index for gpu in gpu_list if gpu.uuid}
    process_counts: dict[int, int] = {gpu.index: 0 for gpu in gpu_list}
    for proc in process_list:
        index = uuid_to_index.get(proc.gpu_uuid)
        if index is not None:
            process_counts[index] = process_counts.get(index, 0) + 1

    idle_indices: list[int] = []
    busy_indices: list[int] = []
    for gpu in gpu_list:
        has_processes = process_counts.get(gpu.index, 0) > 0
        is_idle = (
            gpu.utilization_gpu <= idle_util_threshold
            and gpu.memory_used_mb <= idle_memory_threshold_mb
            and not has_processes
        )
        if is_idle:
            idle_indices.append(gpu.index)
        else:
            busy_indices.append(gpu.index)

    return GpuStatus(
        gpus=gpu_list,
        processes=process_list,
        idle_indices=idle_indices,
        busy_indices=busy_indices,
        process_counts_by_index=process_counts,
    )


def status_from_remote_output(
    text: str,
    idle_util_threshold: int,
    idle_memory_threshold_mb: int,
) -> GpuStatus:
    sections = split_remote_output(text)
    processes = enrich_processes(
        parse_compute_process_csv(sections.get("PROCS", "")),
        parse_process_info(sections.get("PS", "")),
    )
    return evaluate_status(
        parse_gpu_csv(sections.get("GPU", "")),
        processes,
        idle_util_threshold,
        idle_memory_threshold_mb,
    )


def split_remote_output(text: str) -> dict[str, str]:
    markers = {
        "__GPU__": "GPU",
        "__PROCS__": "PROCS",
        "__PS__": "PS",
    }
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in markers:
            current = markers[stripped]
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _csv_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        cleaned = [cell.strip() for cell in row]
        if cleaned and not cleaned[0].lower().startswith("no running"):
            rows.append(cleaned)
    return rows


def _int_or_zero(value: str) -> int:
    parsed = _int_or_none(value)
    return parsed if parsed is not None else 0


def _int_or_none(value: str) -> int | None:
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    return int(digits) if digits else None


def _format_gpu_list(indices: list[int]) -> str:
    if not indices:
        return "GPU"
    return "\u3001".join(f"GPU {index}" for index in indices)
