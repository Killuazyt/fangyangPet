from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageChops

from gpu_rowlet.config import load_config
from gpu_rowlet.app import (
    CELL_HEIGHT,
    CELL_WIDTH,
    COLS,
    FAST_DRAG_ANIMATION_MS,
    SLOW_DRAG_ANIMATION_MS,
    add_sleep_effect,
    drag_state_for_delta,
    extract_state_images,
)
from gpu_rowlet.gpu import (
    GpuSample,
    ComputeProcess,
    evaluate_status,
    parse_compute_process_csv,
    parse_gpu_csv,
    parse_process_info,
    status_from_remote_output,
)
from gpu_rowlet.ssh_client import REMOTE_GPU_SCRIPT, build_remote_shell_command, build_ssh_command


class GpuParsingTests(unittest.TestCase):
    def test_rowlet_spritesheet_geometry(self) -> None:
        spritesheet = Path(__file__).resolve().parents[2] / "rowlet" / "spritesheet.webp"
        with Image.open(spritesheet) as image:
            self.assertEqual(image.size, (CELL_WIDTH * COLS, CELL_HEIGHT * 9))

    def test_extract_state_images_skips_blank_slots(self) -> None:
        spritesheet = Path(__file__).resolve().parents[2] / "rowlet" / "spritesheet.webp"
        with Image.open(spritesheet) as image:
            frames = extract_state_images(image, 0.5)

        self.assertEqual(len(frames["idle"]), 6)
        self.assertEqual(len(frames["waving"]), 4)
        self.assertEqual(frames["idle"][0].size, (96, 104))

    def test_extracted_frames_use_binary_alpha(self) -> None:
        spritesheet = Path(__file__).resolve().parents[2] / "rowlet" / "spritesheet.webp"
        with Image.open(spritesheet) as image:
            frame = extract_state_images(image, 0.5)["idle"][0]

        histogram = frame.getchannel("A").histogram()
        nonzero_alpha_values = {index for index, count in enumerate(histogram) if count}
        self.assertLessEqual(nonzero_alpha_values, {0, 255})

    def test_sleep_bubble_overlay_changes_idle_frame(self) -> None:
        spritesheet = Path(__file__).resolve().parents[2] / "rowlet" / "spritesheet.webp"
        with Image.open(spritesheet) as image:
            frame = extract_state_images(image, 0.5)["idle"][0]

        sleep_frame = add_sleep_effect(frame, 0, 0.5)

        self.assertEqual(sleep_frame.size, frame.size)
        self.assertIsNotNone(ImageChops.difference(sleep_frame, frame).getbbox())

    def test_drag_state_tracks_horizontal_direction(self) -> None:
        self.assertEqual(drag_state_for_delta(1, "running-left"), "running-right")
        self.assertEqual(drag_state_for_delta(-1, "running-right"), "running-left")
        self.assertEqual(drag_state_for_delta(0, "running-left"), "running-left")

    def test_drag_animation_constants_keep_fast_drag_slower_than_slow_drag(self) -> None:
        self.assertEqual(SLOW_DRAG_ANIMATION_MS, 150)
        self.assertEqual(FAST_DRAG_ANIMATION_MS, 200)
        self.assertGreater(FAST_DRAG_ANIMATION_MS, SLOW_DRAG_ANIMATION_MS)

    def test_parse_gpu_csv_with_uuid(self) -> None:
        samples = parse_gpu_csv(
            "0, GPU-abc, NVIDIA GeForce RTX 4090, 1, 128, 24576\n"
            "1, GPU-def, NVIDIA GeForce RTX 4090, 88, 19000, 24576\n"
        )

        self.assertEqual([sample.index for sample in samples], [0, 1])
        self.assertEqual(samples[0].uuid, "GPU-abc")
        self.assertEqual(samples[0].name, "NVIDIA GeForce RTX 4090")
        self.assertEqual(samples[1].utilization_gpu, 88)

    def test_parse_compute_process_csv(self) -> None:
        processes = parse_compute_process_csv("GPU-def, 1234, python, 12000 MiB\n")

        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].pid, 1234)
        self.assertEqual(processes[0].used_memory_mb, 12000)

    def test_parse_process_info(self) -> None:
        infos = parse_process_info("1234|alice|python|python train.py --gpu 1\n")

        self.assertEqual(infos[1234].user, "alice")
        self.assertEqual(infos[1234].command, "python train.py --gpu 1")

    def test_any_idle_rule_with_process_mapping(self) -> None:
        status = evaluate_status(
            [
                GpuSample(0, "GPU-0", 1, 128, 24576),
                GpuSample(1, "GPU-1", 1, 128, 24576),
                GpuSample(2, "GPU-2", 99, 20000, 24576),
            ],
            [ComputeProcess("GPU-1", 222, "python", 100)],
            idle_util_threshold=5,
            idle_memory_threshold_mb=512,
        )

        self.assertTrue(status.any_idle)
        self.assertEqual(status.idle_indices, [0])
        self.assertEqual(status.busy_indices, [1, 2])
        self.assertIn("GPU 0", status.bubble_message())

    def test_status_from_remote_output(self) -> None:
        output = """__GPU__
0, GPU-0, NVIDIA GeForce RTX 4090, 0, 100, 24576
1, GPU-1, NVIDIA GeForce RTX 4090, 65, 16000, 24576
__PROCS__
GPU-1, 1234, python, 10000
__PS__
1234|alice|python|python train.py
"""
        status = status_from_remote_output(output, 5, 512)

        self.assertEqual(status.idle_indices, [0])
        self.assertEqual(status.busy_indices, [1])
        self.assertEqual(status.processes[0].user, "alice")
        self.assertEqual(status.processes[0].command, "python train.py")


class ConfigTests(unittest.TestCase):
    def test_rejects_password_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "host": "example.com",
                        "port": 22,
                        "username": "ubuntu",
                        "identity_file": "id_ed25519",
                        "password": "unsafe",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_config(path)

    def test_allows_blank_config_for_ui_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "host": "",
                        "port": 22,
                        "username": "",
                        "auth_method": "key",
                        "identity_file": "",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertFalse(config.is_connection_configured)

    def test_password_auth_uses_credential_key_without_password_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "host": "10.0.0.2",
                        "port": 2222,
                        "username": "ubuntu",
                        "auth_method": "password",
                        "identity_file": "",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertTrue(config.is_connection_configured)
        self.assertEqual(config.credential_key, "ubuntu@10.0.0.2:2222")

    def test_ssh_command_disables_password_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            key_path = Path(tmp) / "id_ed25519"
            key_path.write_text("placeholder", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "host": "10.0.0.2",
                        "port": 2222,
                        "username": "ubuntu",
                        "identity_file": str(key_path),
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)

        command = build_ssh_command(config, "true", ssh_path="ssh")

        self.assertIn("BatchMode=yes", command)
        self.assertIn("PasswordAuthentication=no", command)
        self.assertIn("ubuntu@10.0.0.2", command)
        self.assertIn("2222", command)

    def test_paramiko_shell_command_quotes_multiline_script(self) -> None:
        command = build_remote_shell_command(REMOTE_GPU_SCRIPT)

        self.assertTrue(command.startswith("bash -lc "))
        self.assertIn("nvidia-smi", command)
        self.assertIn("__PS__", command)
        self.assertNotIn("truen", command)


if __name__ == "__main__":
    unittest.main()
