from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from .gpu import GpuStatus
from .ui_utils import center_on_screen, lift_temporarily


TEXT_TITLE = "GPU \u72b6\u6001"
TEXT_REFRESH = "\u5237\u65b0"
TEXT_CLOSE = "\u5173\u95ed"
TEXT_LOADING = "\u6b63\u5728\u67e5\u8be2 GPU \u72b6\u6001..."
TEXT_NOT_CONFIGURED = "\u8fd8\u6ca1\u6709\u914d\u7f6e\u670d\u52a1\u5668\u8fde\u63a5\u3002"
TEXT_NO_GPU = "\u672a\u68c0\u6d4b\u5230 NVIDIA GPU\u3002"
TEXT_NO_PROCESSES = "\u672a\u53d1\u73b0\u8ba1\u7b97\u8fdb\u7a0b\u3002"
TEXT_PROCESS_DETAIL = "\u9009\u4e2d\u8fdb\u7a0b\u540e\u5728\u8fd9\u91cc\u67e5\u770b\u5b8c\u6574\u547d\u4ee4\u3002"


class StatusDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, gpu_client, is_configured) -> None:
        super().__init__(parent)
        self.title(TEXT_TITLE)
        self.geometry("920x620")
        self.minsize(760, 460)
        self.transient(parent)

        self.gpu_client = gpu_client
        self.is_configured = is_configured
        self.status_var = tk.StringVar(value="")
        self.refreshing = False
        self.process_detail_by_item: dict[str, str] = {}

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<F5>", lambda _event: self.refresh())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(50, self._present)
        self.refresh()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        self.summary = tk.Text(outer, height=8, wrap="word", relief="solid", borderwidth=1)
        self.summary.pack(fill="x")
        self.summary.configure(state="disabled")

        ttk.Label(outer, text="\u8fdb\u7a0b").pack(anchor="w", pady=(12, 4))
        columns = ("gpu", "pid", "user", "name", "memory", "command")
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        headings = {
            "gpu": "GPU",
            "pid": "PID",
            "user": "\u7528\u6237",
            "name": "\u8fdb\u7a0b",
            "memory": "\u663e\u5b58 MB",
            "command": "\u547d\u4ee4",
        }
        widths = {
            "gpu": 60,
            "pid": 80,
            "user": 110,
            "name": 120,
            "memory": 90,
            "command": 280,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected_process)

        ttk.Label(outer, text="\u547d\u4ee4\u8be6\u60c5").pack(anchor="w", pady=(10, 4))
        self.detail = tk.Text(outer, height=4, wrap="word", relief="solid", borderwidth=1)
        self.detail.pack(fill="x")
        self.detail.configure(state="disabled")
        self._set_detail(TEXT_PROCESS_DETAIL)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(bottom, text=TEXT_REFRESH, command=self.refresh)
        self.refresh_button.pack(side="right", padx=(8, 0))
        ttk.Button(bottom, text=TEXT_CLOSE, command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        if self.refreshing:
            return
        if not self.is_configured():
            self._render_message(TEXT_NOT_CONFIGURED)
            return
        self.refreshing = True
        self.refresh_button.configure(state="disabled")
        self.status_var.set(TEXT_LOADING)
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            status = self.gpu_client().poll()
        except Exception as exc:
            self.after(0, lambda: self._render_message(f"GPU \u67e5\u8be2\u5931\u8d25\uff1a{exc}"))
            return
        self.after(0, lambda: self._render_status(status))

    def _render_status(self, status: GpuStatus) -> None:
        lines: list[str] = []
        if not status.gpus:
            lines.append(TEXT_NO_GPU)
        for gpu in status.gpus:
            state = "\u7a7a\u95f2" if gpu.index in status.idle_indices else "\u5fd9\u788c"
            name = f" {gpu.name}" if gpu.name else ""
            process_count = status.process_counts_by_index.get(gpu.index, 0)
            lines.append(
                f"GPU {gpu.index}{name}: {state}, "
                f"util {gpu.utilization_gpu}%, "
                f"memory {gpu.memory_used_mb}/{gpu.memory_total_mb} MB, "
                f"processes {process_count}"
            )

        self._set_summary("\n".join(lines))
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.process_detail_by_item.clear()
        self._set_detail(TEXT_PROCESS_DETAIL)

        uuid_to_index = {gpu.uuid: gpu.index for gpu in status.gpus if gpu.uuid}
        if status.processes:
            for proc in status.processes:
                gpu_index = uuid_to_index.get(proc.gpu_uuid, "?")
                item_id = self.tree.insert(
                    "",
                    "end",
                    values=(
                        gpu_index,
                        proc.pid if proc.pid is not None else "?",
                        proc.user or "?",
                        proc.process_name or "?",
                        proc.used_memory_mb if proc.used_memory_mb is not None else "?",
                        proc.command or "",
                    ),
                )
                self.process_detail_by_item[item_id] = proc.command or proc.process_name or ""
            self.status_var.set("\u5df2\u5237\u65b0\u3002")
        else:
            self.status_var.set(TEXT_NO_PROCESSES)
        self._set_refreshing(False)

    def _render_message(self, message: str) -> None:
        self._set_summary(message)
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.process_detail_by_item.clear()
        self._set_detail(TEXT_PROCESS_DETAIL)
        self.status_var.set(message)
        self._set_refreshing(False)

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _show_selected_process(self, _event: tk.Event) -> None:
        selected = self.tree.selection()
        if not selected:
            self._set_detail(TEXT_PROCESS_DETAIL)
            return
        self._set_detail(self.process_detail_by_item.get(selected[0], ""))

    def _set_refreshing(self, refreshing: bool) -> None:
        self.refreshing = refreshing
        if self.winfo_exists():
            self.refresh_button.configure(state="disabled" if refreshing else "normal")

    def _present(self) -> None:
        center_on_screen(self, width=920, height=620)
        lift_temporarily(self)
