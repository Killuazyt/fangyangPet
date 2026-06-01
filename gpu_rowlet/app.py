from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from typing import Protocol

from PIL import Image, ImageTk
from PIL import ImageDraw

from .config import AppConfig, STATE_ROWS
from .config import load_config
from .gpu import GpuStatus
from .settings_dialog import SettingsDialog
from .ssh_client import SshGpuClient
from .status_dialog import StatusDialog


CELL_WIDTH = 192
CELL_HEIGHT = 208
COLS = 8
ALPHA_THRESHOLD = 128
SLOW_DRAG_ANIMATION_MS = 150
FAST_DRAG_ANIMATION_MS = 200
FAST_DRAG_PIXELS_PER_SECOND = 700

TEXT_SETUP_HINT = "\u53f3\u952e Rowlet \u6253\u5f00\u8bbe\u7f6e\uff0c\u586b\u5199\u670d\u52a1\u5668 IP\u3001\u7aef\u53e3\u3001\u8d26\u53f7\u548c\u8ba4\u8bc1\u65b9\u5f0f\u3002"
TEXT_QUERY_FAILED = "GPU \u67e5\u8be2\u5931\u8d25\uff1a"
TEXT_SETTINGS = "\u8bbe\u7f6e"
TEXT_STATUS = "\u67e5\u770b\u72b6\u6001"
TEXT_QUERY_NOW = "\u7acb\u5373\u67e5\u8be2"
TEXT_EXIT = "\u9000\u51fa"
TEXT_SAVED = "\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u6b63\u5728\u6309\u65b0\u914d\u7f6e\u76d1\u63a7 GPU\u3002"
TEXT_NOT_CONFIGURED = "\u8fd8\u6ca1\u6709\u914d\u7f6e\u670d\u52a1\u5668\u8fde\u63a5\u3002\u53f3\u952e\u6253\u5f00\u8bbe\u7f6e\u3002"


class GpuClient(Protocol):
    def poll(self) -> GpuStatus:
        ...


@dataclass(frozen=True)
class MonitorEvent:
    status: GpuStatus | None
    error: str | None = None


class RowletApp:
    def __init__(
        self,
        config: AppConfig,
        gpu_client: GpuClient,
        config_path: Path,
        use_mock: bool = False,
    ) -> None:
        self.config = config
        self.gpu_client = gpu_client
        self.config_path = config_path
        self.use_mock = use_mock
        self._closing = False

        self.root = tk.Tk()
        self.root.title("GPU Rowlet")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Tk's transparent color is binary. Keep it away from sprite edge colors,
        # and binarize sprite alpha so semi-transparent pixels do not blend purple.
        self.transparent_color = "#010203"
        self.root.configure(bg=self.transparent_color)
        try:
            self.root.wm_attributes("-transparentcolor", self.transparent_color)
        except tk.TclError:
            pass

        self.frames = load_frames(config.spritesheet_path, config.window_scale)
        self.sleep_frames = load_sleep_frames(config.spritesheet_path, config.window_scale, config.active_state)
        self.state = config.active_state
        self.sleep_bubble_active = False
        self.dragging = False
        self.drag_state = "running-right"
        self.drag_animation_ms = SLOW_DRAG_ANIMATION_MS
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._last_drag_pointer_x: int | None = None
        self._last_drag_sample_at: float | None = None
        self.frame_index = 0
        self.events: queue.Queue[MonitorEvent] = queue.Queue()
        self.stop_event = threading.Event()
        self.last_idle_indices: tuple[int, ...] = ()
        self.last_bubble_at = 0.0
        self.bubble: tk.Toplevel | None = None
        self.bubble_label: tk.Label | None = None
        self.bubble_hide_after_id: str | None = None
        self.context_menu: tk.Toplevel | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.status_dialog: StatusDialog | None = None

        self.image_label = tk.Label(
            self.root,
            bg=self.transparent_color,
            borderwidth=0,
            highlightthickness=0,
        )
        self.image_label.pack()
        self._bind_drag()
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.bind("<Button-3>", self._show_context_menu)
        self.image_label.bind("<Button-3>", self._show_context_menu)

    def run(self) -> None:
        self._draw_next_frame()
        self._center_on_screen()
        self._reload_config_from_disk()
        if not self.use_mock and not self.config.is_connection_configured:
            self._show_bubble(TEXT_SETUP_HINT, force=True)
        threading.Thread(target=self._monitor_loop, name="gpu-monitor", daemon=True).start()
        self.root.after(self.config.animation_ms, self._animate)
        self._drain_events()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.mainloop()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.stop_event.set()
        self.dragging = False
        try:
            self.root.withdraw()
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass
        self._destroy_child_windows()
        self.root.after_idle(self._destroy_root)

    def _destroy_child_windows(self) -> None:
        for child in [self.bubble, self.settings_dialog, self.status_dialog]:
            if child is not None:
                try:
                    if child.winfo_exists():
                        child.destroy()
                except tk.TclError:
                    pass
        self.bubble = None
        self.bubble_label = None
        self.settings_dialog = None
        self.status_dialog = None
        self._hide_context_menu()

    def _destroy_root(self) -> None:
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self.use_mock and not self.config.is_connection_configured:
                self._reload_config_from_disk()
            if not self.use_mock and not self.config.is_connection_configured:
                self.stop_event.wait(2)
                continue
            try:
                status = self.gpu_client.poll()
                self.events.put(MonitorEvent(status=status))
            except Exception as exc:
                self.events.put(MonitorEvent(status=None, error=str(exc)))
            self.stop_event.wait(self.config.poll_interval_seconds)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._apply_event(event)
        self.root.after(300, self._drain_events)

    def _apply_event(self, event: MonitorEvent) -> None:
        if event.error:
            self._set_state(self.config.error_state)
            self._show_bubble(f"{TEXT_QUERY_FAILED}{event.error}", force=True)
            return
        if not event.status:
            return

        status = event.status
        if status.any_idle:
            self.sleep_bubble_active = True
            self._set_state(self.config.active_state)
            idle_tuple = tuple(status.idle_indices)
            now = time.monotonic()
            should_repeat = now - self.last_bubble_at >= self.config.bubble_repeat_seconds
            if idle_tuple != self.last_idle_indices or should_repeat:
                self._show_bubble(status.bubble_message(), force=idle_tuple != self.last_idle_indices, duration_ms=None)
                self.last_idle_indices = idle_tuple
                self.last_bubble_at = now
        else:
            self.sleep_bubble_active = False
            self._set_state(self.config.active_state)
            self.last_idle_indices = ()
            self._hide_bubble()

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.frame_index = 0

    def _animate(self) -> None:
        self._draw_next_frame()
        delay = self.drag_animation_ms if self.dragging else self.config.animation_ms
        self.root.after(delay, self._animate)

    def _draw_next_frame(self) -> None:
        if self.dragging:
            state_frames = self.frames[self.drag_state]
        elif self.sleep_bubble_active:
            state_frames = self.sleep_frames
        else:
            state_frames = self.frames[self.state]
        frame = state_frames[self.frame_index % len(state_frames)]
        self.image_label.configure(image=frame)
        self.image_label.image = frame
        self.frame_index += 1

    def _show_bubble(self, text: str, force: bool = False, duration_ms: int | None = 7000) -> None:
        if self.bubble is not None and force:
            self._hide_bubble()
        self._cancel_bubble_hide()
        if self.bubble is None:
            self.bubble = tk.Toplevel(self.root)
            self.bubble.overrideredirect(True)
            self.bubble.attributes("-topmost", True)
            self.bubble_label = tk.Label(
                self.bubble,
                bg="#ffffff",
                fg="#202124",
                text=text,
                font=("Microsoft YaHei UI", 10),
                padx=12,
                pady=8,
                relief="solid",
                borderwidth=1,
                wraplength=280,
                justify="left",
            )
            self.bubble_label.pack()
        elif self.bubble_label is not None:
            self.bubble_label.configure(text=text)

        self._position_bubble()
        self.bubble.deiconify()
        if duration_ms is not None:
            self.bubble_hide_after_id = self.root.after(duration_ms, self._hide_bubble)

    def _position_bubble(self) -> None:
        if self.bubble is None:
            return
        self.root.update_idletasks()
        x = self.root.winfo_x() - 20
        y = max(0, self.root.winfo_y() - self.bubble.winfo_reqheight() - 8)
        self.bubble.geometry(f"+{x}+{y}")

    def _hide_bubble(self) -> None:
        self._cancel_bubble_hide()
        if self.bubble is not None:
            self.bubble.withdraw()

    def _cancel_bubble_hide(self) -> None:
        if self.bubble_hide_after_id is None:
            return
        try:
            self.root.after_cancel(self.bubble_hide_after_id)
        except tk.TclError:
            pass
        self.bubble_hide_after_id = None

    def _bind_drag(self) -> None:
        def start(event: tk.Event) -> None:
            self._drag_start_x = event.x
            self._drag_start_y = event.y
            pointer_x = self.root.winfo_pointerx()
            self._last_drag_pointer_x = pointer_x
            self._last_drag_sample_at = time.monotonic()
            self.drag_animation_ms = SLOW_DRAG_ANIMATION_MS
            self.dragging = True
            self.frame_index = 0
            self._draw_next_frame()

        def drag(event: tk.Event) -> None:
            pointer_x = self.root.winfo_pointerx()
            if self._last_drag_pointer_x is not None:
                delta_x = pointer_x - self._last_drag_pointer_x
                self._update_drag_speed(delta_x)
                next_state = drag_state_for_delta(delta_x, self.drag_state)
                if next_state != self.drag_state:
                    self.drag_state = next_state
                    self.frame_index = 0
                    self._draw_next_frame()
            self._last_drag_pointer_x = pointer_x

            x = pointer_x - self._drag_start_x
            y = self.root.winfo_pointery() - self._drag_start_y
            self.root.geometry(f"+{x}+{y}")
            if self.bubble is not None:
                self._position_bubble()

        def end(_event: tk.Event) -> None:
            if self.dragging:
                self.dragging = False
                self._last_drag_pointer_x = None
                self._last_drag_sample_at = None
                self.drag_animation_ms = SLOW_DRAG_ANIMATION_MS
                self.frame_index = 0

        self.image_label.bind("<Button-1>", start)
        self.image_label.bind("<B1-Motion>", drag)
        self.image_label.bind("<ButtonRelease-1>", end)
        self.root.bind("<ButtonRelease-1>", end)

    def _update_drag_speed(self, delta_x: int) -> None:
        now = time.monotonic()
        if self._last_drag_sample_at is None:
            self._last_drag_sample_at = now
            return
        elapsed = max(0.001, now - self._last_drag_sample_at)
        speed = abs(delta_x) / elapsed
        self.drag_animation_ms = FAST_DRAG_ANIMATION_MS if speed >= FAST_DRAG_PIXELS_PER_SECOND else SLOW_DRAG_ANIMATION_MS
        self._last_drag_sample_at = now

    def _close_from_menu(self) -> None:
        self._hide_context_menu()
        self.root.after(10, self.close)

    def _show_context_menu(self, event: tk.Event) -> None:
        if self._closing:
            return
        self._hide_context_menu()
        menu = tk.Toplevel(self.root)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg="#f8f8f8")
        menu.bind("<FocusOut>", lambda _event: self._hide_context_menu())
        self.context_menu = menu

        frame = tk.Frame(menu, bg="#f8f8f8", bd=1, relief="solid")
        frame.pack(fill="both", expand=True)
        self._add_menu_button(frame, TEXT_STATUS, self._open_status_from_menu)
        self._add_menu_separator(frame)
        self._add_menu_button(frame, TEXT_SETTINGS, self._open_settings_from_menu)
        self._add_menu_button(frame, TEXT_QUERY_NOW, self._poll_once_from_menu)
        self._add_menu_separator(frame)
        self._add_menu_button(frame, TEXT_EXIT, self._close_from_menu)

        menu.geometry(f"+{event.x_root}+{event.y_root}")
        menu.update_idletasks()
        menu.focus_force()

    def _add_menu_button(self, parent: tk.Frame, text: str, command) -> None:
        button = tk.Button(
            parent,
            text=text,
            anchor="w",
            width=14,
            relief="flat",
            bg="#f8f8f8",
            activebackground="#e8e8e8",
            command=command,
        )
        button.pack(fill="x", padx=1, pady=1)

    def _add_menu_separator(self, parent: tk.Frame) -> None:
        tk.Frame(parent, height=1, bg="#d0d0d0").pack(fill="x", padx=4, pady=3)

    def _hide_context_menu(self) -> None:
        if self.context_menu is not None:
            try:
                if self.context_menu.winfo_exists():
                    self.context_menu.destroy()
            except tk.TclError:
                pass
        self.context_menu = None

    def _open_status_from_menu(self) -> None:
        self._hide_context_menu()
        self._open_status()

    def _open_settings_from_menu(self) -> None:
        self._hide_context_menu()
        self._open_settings()

    def _poll_once_from_menu(self) -> None:
        self._hide_context_menu()
        self._poll_once_async()

    def _open_settings(self) -> None:
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():
            self.settings_dialog.lift()
            return
        self.settings_dialog = SettingsDialog(self.root, self.config, self.config_path, self._on_config_saved)

    def _open_status(self) -> None:
        if not self.use_mock:
            self._reload_config_from_disk()
        if self.status_dialog is not None and self.status_dialog.winfo_exists():
            self.status_dialog.lift()
            self.status_dialog.refresh()
            return
        self.status_dialog = StatusDialog(
            self.root,
            gpu_client=lambda: self.gpu_client,
            is_configured=lambda: self.use_mock or self.config.is_connection_configured,
        )

    def _on_config_saved(self, config: AppConfig) -> None:
        self.config = config
        if not self.use_mock:
            self.gpu_client = SshGpuClient(config)
        self.sleep_frames = load_sleep_frames(config.spritesheet_path, config.window_scale, config.active_state)
        self.last_idle_indices = ()
        self.last_bubble_at = 0.0
        self._show_bubble(TEXT_SAVED, force=True)
        self._poll_once_async()

    def _poll_once_async(self) -> None:
        if not self.use_mock:
            self._reload_config_from_disk()
        if not self.use_mock and not self.config.is_connection_configured:
            self._show_bubble(TEXT_NOT_CONFIGURED, force=True)
            return

        def worker() -> None:
            try:
                self.events.put(MonitorEvent(status=self.gpu_client.poll()))
            except Exception as exc:
                self.events.put(MonitorEvent(status=None, error=str(exc)))

        threading.Thread(target=worker, name="gpu-poll-once", daemon=True).start()

    def _reload_config_from_disk(self) -> None:
        try:
            config = load_config(self.config_path)
        except Exception:
            return
        if config != self.config:
            self.config = config
            self.gpu_client = SshGpuClient(config)
            self.sleep_frames = load_sleep_frames(config.spritesheet_path, config.window_scale, config.active_state)

    def _center_on_screen(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        height = self.root.winfo_reqheight()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = screen_width - width - 80
        y = screen_height - height - 100
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")


def load_frames(spritesheet_path, scale: float) -> dict[str, list[ImageTk.PhotoImage]]:
    image = Image.open(spritesheet_path).convert("RGBA")
    state_images = extract_state_images(image, scale)
    return {
        state: [ImageTk.PhotoImage(frame) for frame in frames]
        for state, frames in state_images.items()
    }


def load_sleep_frames(spritesheet_path, scale: float, base_state: str) -> list[ImageTk.PhotoImage]:
    image = Image.open(spritesheet_path).convert("RGBA")
    state_images = extract_state_images(image, scale)
    frames = state_images.get(base_state) or state_images["idle"]
    return [
        ImageTk.PhotoImage(add_sleep_effect(frame, index, scale))
        for index, frame in enumerate(frames)
    ]


def extract_state_images(image: Image.Image, scale: float) -> dict[str, list[Image.Image]]:
    image = image.convert("RGBA")
    expected = (CELL_WIDTH * COLS, CELL_HEIGHT * len(STATE_ROWS))
    if image.size != expected:
        raise ValueError(f"Unexpected spritesheet size {image.size}; expected {expected}.")

    result: dict[str, list[Image.Image]] = {}
    for state, row in STATE_ROWS.items():
        frames: list[Image.Image] = []
        for col in range(COLS):
            box = (
                col * CELL_WIDTH,
                row * CELL_HEIGHT,
                (col + 1) * CELL_WIDTH,
                (row + 1) * CELL_HEIGHT,
            )
            frame = image.crop(box)
            if frame.getchannel("A").getbbox() is None:
                continue
            frame = normalize_frame_alpha(frame)
            if scale != 1.0:
                frame = frame.resize(
                    (int(CELL_WIDTH * scale), int(CELL_HEIGHT * scale)),
                    Image.Resampling.NEAREST,
                )
            frames.append(frame)
        if not frames:
            frames.append(Image.new("RGBA", (int(CELL_WIDTH * scale), int(CELL_HEIGHT * scale)), (0, 0, 0, 0)))
        result[state] = frames
    return result


def normalize_frame_alpha(frame: Image.Image) -> Image.Image:
    frame = frame.convert("RGBA")
    r, g, b, a = frame.split()
    a = a.point(lambda value: 255 if value >= ALPHA_THRESHOLD else 0)
    frame = Image.merge("RGBA", (r, g, b, a))
    transparent = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    transparent.alpha_composite(frame)
    return transparent


def add_sleep_effect(frame: Image.Image, frame_index: int, scale: float) -> Image.Image:
    result = make_sleepy_pose(frame, frame_index).convert("RGBA")
    draw = ImageDraw.Draw(result, "RGBA")

    width, height = result.size
    phase = frame_index % 4
    radius = max(5, int((8 + phase * 2) * scale / 0.5))
    draw_sleepy_mouth(draw, width, height, scale, phase)

    cx = int(width * 0.64)
    cy = int(height * 0.30) - int(phase * 1.2)
    ellipse = (cx - radius, cy - radius, cx + radius, cy + radius)

    # The reference images use a pale blue sleep bubble attached near Rowlet's beak.
    draw.ellipse(ellipse, fill=(185, 242, 246, 185), outline=(113, 207, 216, 210), width=max(1, int(2 * scale / 0.5)))
    shine_radius = max(2, radius // 4)
    draw.ellipse(
        (
            cx + radius // 4,
            cy - radius // 2,
            cx + radius // 4 + shine_radius,
            cy - radius // 2 + shine_radius,
        ),
        fill=(255, 255, 255, 185),
    )
    neck = [
        (cx - radius // 2, cy + radius // 3),
        (cx - radius, cy + radius + max(1, radius // 5)),
        (cx - radius // 5, cy + radius // 2),
    ]
    draw.polygon(neck, fill=(185, 242, 246, 160), outline=(113, 207, 216, 180))
    return result


def make_sleepy_pose(frame: Image.Image, frame_index: int) -> Image.Image:
    phase = frame_index % 4
    dy = 1 if phase in {1, 2} else 0
    if dy == 0:
        return frame.copy()
    shifted = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    shifted.alpha_composite(frame, (0, dy))
    return shifted


def draw_sleepy_mouth(draw: ImageDraw.ImageDraw, width: int, height: int, scale: float, phase: int) -> None:
    line_width = max(1, int(2 * scale / 0.5))
    bob = 1 if phase in {1, 2} else 0
    mouth = (
        int(width * 0.485),
        int(height * 0.405) + bob,
        int(width * 0.555),
        int(height * 0.485) + bob,
    )
    draw.ellipse(mouth, fill=(72, 45, 41, 230), outline=(112, 74, 42, 220), width=line_width)
    tongue = (
        int(width * 0.495),
        int(height * 0.445) + bob,
        int(width * 0.555),
        int(height * 0.500) + bob,
    )
    draw.pieslice(tongue, start=0, end=180, fill=(235, 124, 51, 235))


def drag_state_for_delta(delta_x: int, current_state: str) -> str:
    if delta_x > 0:
        return "running-right"
    if delta_x < 0:
        return "running-left"
    return current_state
