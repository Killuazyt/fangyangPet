from __future__ import annotations

import tkinter as tk


def center_on_screen(window: tk.Toplevel | tk.Tk, width: int | None = None, height: int | None = None) -> None:
    window.update_idletasks()
    screen_owner = window.master if getattr(window, "master", None) is not None else window
    screen_width = screen_owner.winfo_screenwidth()
    screen_height = screen_owner.winfo_screenheight()
    target_width = width or window.winfo_width() or window.winfo_reqwidth()
    target_height = height or window.winfo_height() or window.winfo_reqheight()
    x = max(0, (screen_width - target_width) // 2)
    y = max(0, (screen_height - target_height) // 2)
    window.geometry(f"{target_width}x{target_height}+{x}+{y}")


def clamp_to_screen(window: tk.Toplevel | tk.Tk) -> None:
    window.update_idletasks()
    width = window.winfo_width() or window.winfo_reqwidth()
    height = window.winfo_height() or window.winfo_reqheight()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = min(max(window.winfo_x(), 0), max(0, screen_width - width))
    y = min(max(window.winfo_y(), 0), max(0, screen_height - height))
    window.geometry(f"+{x}+{y}")


def bind_entry_select_all(entry: tk.Entry) -> None:
    def select_all(_event: tk.Event) -> str:
        entry.select_range(0, "end")
        entry.icursor("end")
        return "break"

    entry.bind("<Control-a>", select_all)
    entry.bind("<Control-A>", select_all)


def lift_temporarily(window: tk.Toplevel) -> None:
    window.attributes("-topmost", True)
    window.lift()
    window.focus_force()
    window.after(300, lambda: _clear_topmost(window))


def _clear_topmost(window: tk.Toplevel) -> None:
    if window.winfo_exists():
        window.attributes("-topmost", False)
