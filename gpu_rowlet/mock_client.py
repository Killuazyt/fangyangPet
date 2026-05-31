from __future__ import annotations

from itertools import cycle

from .gpu import GpuSample, GpuStatus, evaluate_status


class MockGpuClient:
    def __init__(self) -> None:
        self._samples = cycle(
            [
                [
                    GpuSample(0, "GPU-0", 72, 18000, 24576),
                    GpuSample(1, "GPU-1", 1, 120, 24576),
                    GpuSample(2, "GPU-2", 60, 14000, 24576),
                    GpuSample(3, "GPU-3", 0, 80, 24576),
                ],
                [
                    GpuSample(0, "GPU-0", 80, 20000, 24576),
                    GpuSample(1, "GPU-1", 76, 19000, 24576),
                    GpuSample(2, "GPU-2", 65, 17000, 24576),
                    GpuSample(3, "GPU-3", 55, 12000, 24576),
                ],
            ]
        )

    def poll(self) -> GpuStatus:
        return evaluate_status(
            next(self._samples),
            [],
            idle_util_threshold=5,
            idle_memory_threshold_mb=512,
        )
