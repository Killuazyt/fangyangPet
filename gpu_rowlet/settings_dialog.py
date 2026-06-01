from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import AppConfig, save_config
from .credentials import get_password, set_password
from .ssh_client import SshGpuClient
from .ui_utils import bind_entry_select_all, center_on_screen, lift_temporarily


TEXT_TITLE = "GPU Rowlet \u8bbe\u7f6e"
TEXT_PASSWORD_HINT = "\u5bc6\u7801\u7559\u7a7a\u8868\u793a\u4e0d\u4fee\u6539\u5df2\u4fdd\u5b58\u7684\u7cfb\u7edf\u51ed\u636e\u3002"
TEXT_SERVER = "\u670d\u52a1\u5668"
TEXT_PORT = "\u7aef\u53e3"
TEXT_ACCOUNT = "\u8d26\u53f7"
TEXT_AUTH = "\u8ba4\u8bc1"
TEXT_SSH_KEY = "SSH \u5bc6\u94a5"
TEXT_PASSWORD_AUTH = "\u8d26\u53f7\u5bc6\u7801"
TEXT_KEY_PATH = "\u79c1\u94a5\u8def\u5f84"
TEXT_BROWSE = "\u6d4f\u89c8"
TEXT_PASSWORD = "\u5bc6\u7801"
TEXT_MONITOR = "\u76d1\u63a7"
TEXT_POLL_SECONDS = "\u8f6e\u8be2\u79d2\u6570"
TEXT_IDLE_UTIL = "\u7a7a\u95f2\u5229\u7528\u7387 <="
TEXT_IDLE_MEMORY = "\u7a7a\u95f2\u663e\u5b58 MB <="
TEXT_TEST = "\u6d4b\u8bd5\u8fde\u63a5"
TEXT_SAVE = "\u4fdd\u5b58"
TEXT_CANCEL = "\u53d6\u6d88"
TEXT_SAVE_FAILED = "\u4fdd\u5b58\u5931\u8d25"
TEXT_INVALID_CONFIG = "\u914d\u7f6e\u65e0\u6548"
TEXT_TESTING = "\u6b63\u5728\u6d4b\u8bd5\u8fde\u63a5..."
TEXT_CONNECTED = "\u8fde\u63a5\u6210\u529f\u3002"
TEXT_CONNECT_FAILED = "\u8fde\u63a5\u5931\u8d25"
TEXT_PICK_KEY = "\u9009\u62e9 SSH \u79c1\u94a5"
TEXT_SHOW_PASSWORD = "\u663e\u793a"
TEXT_TEST_OK = "\u6d4b\u8bd5\u901a\u8fc7\u3002"
TEXT_PASSWORD_SAVED = "\u5df2\u6709\u4fdd\u5b58\u7684\u7cfb\u7edf\u51ed\u636e\uff0c\u5bc6\u7801\u7559\u7a7a\u5c06\u7ee7\u7eed\u4f7f\u7528\u5b83\u3002"


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, config: AppConfig, config_path: Path, on_saved) -> None:
        super().__init__(parent)
        self.title(TEXT_TITLE)
        self.resizable(True, True)
        self.minsize(620, 480)
        self.transient(parent)
        self.grab_set()

        self.config_path = config_path
        self.original_config = config
        self.on_saved = on_saved

        self.host_var = tk.StringVar(value=config.host)
        self.port_var = tk.StringVar(value=str(config.port))
        self.username_var = tk.StringVar(value=config.username)
        self.auth_method_var = tk.StringVar(value=config.auth_method)
        self.identity_file_var = tk.StringVar(value=str(config.identity_file or ""))
        self.show_password_var = tk.BooleanVar(value=False)
        self.poll_var = tk.StringVar(value=str(config.poll_interval_seconds))
        self.util_var = tk.StringVar(value=str(config.idle_util_threshold))
        self.memory_var = tk.StringVar(value=str(config.idle_memory_threshold_mb))
        self.status_var = tk.StringVar(value=TEXT_PASSWORD_HINT)
        self.entries: list[ttk.Entry] = []
        self._credential_check_token = 0

        self._build()
        self._sync_auth_controls()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self._save())
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.after(50, self._present)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        scroll_host = ttk.Frame(self)
        scroll_host.grid(row=0, column=0, sticky="nsew")
        scroll_host.columnconfigure(0, weight=1)
        scroll_host.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(scroll_host, borderwidth=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(scroll_host, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        outer = ttk.Frame(self.canvas, padding=(14, 12, 14, 8))
        self.content_window = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text=TEXT_SERVER).grid(row=0, column=0, sticky="w")
        form = ttk.Frame(outer)
        form.grid(row=1, column=0, sticky="ew", pady=(8, 12))

        self._entry(form, "IP / Host", self.host_var, 0)
        self._entry(form, TEXT_PORT, self.port_var, 1, width=10)
        self._entry(form, TEXT_ACCOUNT, self.username_var, 2)

        ttk.Label(form, text=TEXT_AUTH).grid(row=3, column=0, sticky="w", pady=4)
        auth_frame = ttk.Frame(form)
        auth_frame.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Radiobutton(auth_frame, text=TEXT_SSH_KEY, variable=self.auth_method_var, value="key", command=self._sync_auth_controls).pack(side="left")
        ttk.Radiobutton(auth_frame, text=TEXT_PASSWORD_AUTH, variable=self.auth_method_var, value="password", command=self._sync_auth_controls).pack(side="left", padx=(12, 0))

        ttk.Label(form, text=TEXT_KEY_PATH).grid(row=4, column=0, sticky="w", pady=4)
        key_frame = ttk.Frame(form)
        key_frame.grid(row=4, column=1, sticky="ew", pady=4)
        self.key_entry = ttk.Entry(key_frame, textvariable=self.identity_file_var, width=38)
        self.key_entry.pack(side="left", fill="x", expand=True)
        self._register_entry(self.key_entry)
        self.browse_button = ttk.Button(key_frame, text=TEXT_BROWSE, command=self._browse_key)
        self.browse_button.pack(side="left", padx=(6, 0))

        ttk.Label(form, text=TEXT_PASSWORD).grid(row=5, column=0, sticky="w", pady=4)
        password_frame = ttk.Frame(form)
        password_frame.grid(row=5, column=1, sticky="ew", pady=4)
        password_frame.columnconfigure(0, weight=1)
        self.password_entry = ttk.Entry(password_frame, width=42, show="*")
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self._register_entry(self.password_entry)
        ttk.Checkbutton(
            password_frame,
            text=TEXT_SHOW_PASSWORD,
            variable=self.show_password_var,
            command=self._sync_password_visibility,
        ).grid(row=0, column=1, padx=(8, 0))

        ttk.Separator(outer).grid(row=2, column=0, sticky="ew", pady=6)
        ttk.Label(outer, text=TEXT_MONITOR).grid(row=3, column=0, sticky="w")
        monitor = ttk.Frame(outer)
        monitor.grid(row=4, column=0, sticky="ew", pady=(8, 12))
        self._entry(monitor, TEXT_POLL_SECONDS, self.poll_var, 0, width=10)
        self._entry(monitor, TEXT_IDLE_UTIL, self.util_var, 1, width=10)
        self._entry(monitor, TEXT_IDLE_MEMORY, self.memory_var, 2, width=10)

        ttk.Separator(self).grid(row=1, column=0, sticky="ew")
        footer = ttk.Frame(self, padding=(14, 8, 14, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, foreground="#555", wraplength=620).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        buttons = ttk.Frame(footer)
        buttons.grid(row=1, column=0, sticky="e")
        self.test_button = ttk.Button(buttons, text=TEXT_TEST, command=self._test_connection)
        self.test_button.pack(side="left", padx=(0, 8))
        self.save_button = ttk.Button(buttons, text=TEXT_SAVE, command=self._save)
        self.save_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text=TEXT_CANCEL, command=self.destroy).pack(side="left")

    def _entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, width: int = 42) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)
        self._register_entry(entry)
        return entry

    def _register_entry(self, entry: ttk.Entry) -> None:
        self.entries.append(entry)
        bind_entry_select_all(entry)

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.content_window, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self.canvas.bbox("all") is None:
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _sync_auth_controls(self) -> None:
        use_key = self.auth_method_var.get() == "key"
        self.key_entry.configure(state="normal" if use_key else "disabled")
        self.browse_button.configure(state="normal" if use_key else "disabled")
        self.password_entry.configure(state="disabled" if use_key else "normal")
        self._sync_password_visibility()
        if use_key:
            self.status_var.set(TEXT_PASSWORD_HINT)
        else:
            self.status_var.set(TEXT_PASSWORD_HINT)
            self._check_saved_password_async()

    def _check_saved_password_async(self) -> None:
        self._credential_check_token += 1
        token = self._credential_check_token
        try:
            config = self._config_from_fields(allow_missing_password=True)
        except Exception:
            return
        if not config.host or not config.username:
            return

        def worker() -> None:
            try:
                has_password = bool(get_password(config.credential_service, config.credential_key))
            except Exception:
                has_password = False
            self.after(0, lambda: self._finish_saved_password_check(token, has_password))

        threading.Thread(target=worker, name="credential-check", daemon=True).start()

    def _finish_saved_password_check(self, token: int, has_password: bool) -> None:
        if not self.winfo_exists() or token != self._credential_check_token:
            return
        if self.auth_method_var.get() != "password":
            return
        self.status_var.set(TEXT_PASSWORD_SAVED if has_password else TEXT_PASSWORD_HINT)

    def _sync_password_visibility(self) -> None:
        if self.auth_method_var.get() == "key":
            self.password_entry.configure(show="*")
        else:
            self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def _browse_key(self) -> None:
        path = filedialog.askopenfilename(title=TEXT_PICK_KEY, initialdir=str(Path.home() / ".ssh"))
        if path:
            self.identity_file_var.set(path)

    def _save(self) -> None:
        try:
            config = self._config_from_fields()
            self._save_password_if_needed(config)
            save_config(config, self.config_path)
            self.on_saved(config)
            self.destroy()
        except Exception as exc:
            messagebox.showerror(TEXT_SAVE_FAILED, str(exc), parent=self)

    def _test_connection(self) -> None:
        try:
            config = self._config_from_fields()
            self._save_password_if_needed(config)
        except Exception as exc:
            messagebox.showerror(TEXT_INVALID_CONFIG, str(exc), parent=self)
            return

        self.status_var.set(TEXT_TESTING)
        self._set_busy(True)
        threading.Thread(target=self._test_connection_worker, args=(config,), daemon=True).start()

    def _test_connection_worker(self, config: AppConfig) -> None:
        result = SshGpuClient(config).test_connection()
        if result.returncode == 0:
            message = result.stdout.strip() or TEXT_CONNECTED
            self.after(0, lambda: self._finish_test(f"{TEXT_TEST_OK}\n{message[:240]}"))
        else:
            detail = (result.stderr or result.stdout or TEXT_CONNECT_FAILED).strip()
            self.after(0, lambda: self._finish_test(detail[:240]))

    def _finish_test(self, message: str) -> None:
        if not self.winfo_exists():
            return
        self.status_var.set(message)
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.test_button.configure(state=state)
        self.save_button.configure(state=state)

    def _config_from_fields(self, allow_missing_password: bool = False) -> AppConfig:
        host = self.host_var.get().strip()
        username = self.username_var.get().strip()
        auth_method = self.auth_method_var.get()
        port = int(self.port_var.get().strip() or "22")
        identity_text = self.identity_file_var.get().strip()
        identity_file = Path(identity_text).expanduser().resolve() if identity_text else None
        if bool(host) != bool(username):
            raise ValueError("\u670d\u52a1\u5668 IP \u548c\u8d26\u53f7\u9700\u8981\u540c\u65f6\u586b\u5199\u3002")
        if auth_method == "key" and host and not identity_file:
            raise ValueError("\u4f7f\u7528 SSH \u5bc6\u94a5\u65f6\u9700\u8981\u9009\u62e9\u79c1\u94a5\u6587\u4ef6\u3002")

        return replace(
            self.original_config,
            host=host,
            port=port,
            username=username,
            auth_method=auth_method,
            identity_file=identity_file,
            poll_interval_seconds=max(5, int(self.poll_var.get().strip() or "30")),
            idle_util_threshold=max(0, int(self.util_var.get().strip() or "5")),
            idle_memory_threshold_mb=max(0, int(self.memory_var.get().strip() or "512")),
        )

    def _save_password_if_needed(self, config: AppConfig) -> None:
        if config.auth_method != "password":
            return
        if not config.host or not config.username:
            return
        password = self.password_entry.get()
        if password:
            set_password(config.credential_service, config.credential_key, password)
            return
        if not get_password(config.credential_service, config.credential_key):
            raise ValueError("\u4f7f\u7528\u8d26\u53f7\u5bc6\u7801\u767b\u5f55\u65f6\u9700\u8981\u8f93\u5165\u5bc6\u7801\u3002\u5bc6\u7801\u4f1a\u4fdd\u5b58\u5230 Windows \u51ed\u636e\u5e93\uff0c\u4e0d\u4f1a\u5199\u5165 config.json\u3002")

    def _present(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(760, max(620, screen_width - 80))
        height = min(600, max(480, screen_height - 100))
        center_on_screen(self, width=width, height=height)
        lift_temporarily(self)
        if self.auth_method_var.get() == "password":
            self.password_entry.focus_set()
        else:
            self.host_var and self.entries[0].focus_set()
