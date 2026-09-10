from __future__ import annotations

import os

import io
import json
import tempfile
import unittest
from pathlib import Path
from controller import runtime_supervisor
from types import SimpleNamespace
from unittest import mock

from controller.runtime_preflight import (
    DEFAULT_OLLAMA_MODEL,
    StartupPreflightError,
    run_startup_preflight,
)
from controller.runtime_supervisor import main


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def good_git_runner(*_args, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def good_ollama(_url, timeout=0):
    assert timeout == 5
    return FakeResponse({
        "models": [{"name": DEFAULT_OLLAMA_MODEL}]
    })


class RuntimeStartupPreflightTests(unittest.TestCase):
    def environment(self, root):
        return {
            "RVSC_ROOT": str(root),
            "RVSC_RUNTIME_STATE_DIR": str(root),
        }

    def test_default_ollama_environment_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            evidence = run_startup_preflight(
                {"RVSC_RVSC_REPO": str(root)},
                mission_store_path=root / "store.json",
                environ=self.environment(root),
                executable=__import__("sys").executable,
                which=lambda name: "git.exe" if name == "git" else None,
                runner=good_git_runner,
                opener=good_ollama,
            )

        self.assertIn("provider:ollama", evidence)
        self.assertIn(
            "ollama_model:%s" % DEFAULT_OLLAMA_MODEL,
            evidence,
        )

    def test_ollama_missing_model_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(
                StartupPreflightError,
                "required Ollama model unavailable",
            ):
                run_startup_preflight(
                    {"RVSC_RVSC_REPO": str(root)},
                    mission_store_path=root / "store.json",
                    environ=self.environment(root),
                    executable=__import__("sys").executable,
                    which=lambda _name: "git.exe",
                    runner=good_git_runner,
                    opener=lambda *_args, **_kwargs: FakeResponse({
                        "models": [{"name": "another-model"}]
                    }),
                )

    def test_ollama_unavailable_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def unavailable(*_args, **_kwargs):
                raise OSError("connection refused")

            with self.assertRaisesRegex(
                StartupPreflightError,
                "Ollama service unavailable",
            ):
                run_startup_preflight(
                    {"RVSC_RVSC_REPO": str(root)},
                    mission_store_path=root / "store.json",
                    environ=self.environment(root),
                    executable=__import__("sys").executable,
                    which=lambda _name: "git.exe",
                    runner=good_git_runner,
                    opener=unavailable,
                )

    def test_openai_requires_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.environment(root)
            env["RVSC_AI_PROVIDER"] = "openai"

            with self.assertRaisesRegex(
                StartupPreflightError,
                "OPENAI_API_KEY",
            ):
                run_startup_preflight(
                    {"RVSC_RVSC_REPO": str(root)},
                    mission_store_path=root / "store.json",
                    environ=env,
                    executable=__import__("sys").executable,
                    which=lambda _name: "git.exe",
                    runner=good_git_runner,
                )

    def test_openai_with_credential_does_not_probe_ollama(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.environment(root)
            env["RVSC_AI_PROVIDER"] = "openai"
            env["OPENAI_API_KEY"] = "test-key"

            def must_not_open(*_args, **_kwargs):
                raise AssertionError("Ollama must not be probed")

            evidence = run_startup_preflight(
                {"RVSC_RVSC_REPO": str(root)},
                mission_store_path=root / "store.json",
                environ=env,
                executable=__import__("sys").executable,
                which=lambda _name: "git.exe",
                runner=good_git_runner,
                opener=must_not_open,
            )

        self.assertIn("provider:openai", evidence)
        self.assertIn("provider_credential:true", evidence)

    def test_unsupported_provider_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.environment(root)
            env["RVSC_AI_PROVIDER"] = "unsupported"

            with self.assertRaisesRegex(
                StartupPreflightError,
                "unsupported engineering provider",
            ):
                run_startup_preflight(
                    {"RVSC_RVSC_REPO": str(root)},
                    mission_store_path=root / "store.json",
                    environ=env,
                    executable=__import__("sys").executable,
                    which=lambda _name: "git.exe",
                    runner=good_git_runner,
                )

    def test_missing_repository_mapping_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(
                StartupPreflightError,
                "no controlled repository mapping configured",
            ):
                run_startup_preflight(
                    {},
                    mission_store_path=root / "store.json",
                    environ=self.environment(root),
                    executable=__import__("sys").executable,
                    which=lambda _name: "git.exe",
                    runner=good_git_runner,
                    opener=good_ollama,
                )

    def test_git_ownership_or_trust_failure_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def rejected(*_args, **_kwargs):
                return SimpleNamespace(
                    returncode=128,
                    stdout="",
                    stderr=(
                        "fatal: detected dubious ownership "
                        "in repository at 'D:/RAMTech/example'"
                    ),
                )

            with self.assertRaisesRegex(
                StartupPreflightError,
                "dubious ownership",
            ):
                run_startup_preflight(
                    {"RVSC_RVSC_REPO": str(root)},
                    mission_store_path=root / "store.json",
                    environ=self.environment(root),
                    executable=__import__("sys").executable,
                    which=lambda _name: "git.exe",
                    runner=rejected,
                    opener=good_ollama,
                )

    @mock.patch(
        "controller.runtime_supervisor.run_startup_preflight"
    )
    @mock.patch(
        "controller.runtime_supervisor.RuntimeSupervisor"
    )
    def test_status_does_not_require_execution_preflight(
        self,
        supervisor_type,
        preflight,
    ):
        instance = supervisor_type.return_value
        instance.status_dicts.return_value = []
        instance.work_control_status = {
            "state": "IDLE",
            "reason": "test",
        }
        instance.queue_status.return_value = {
            "state": "IDLE",
            "missions": [],
            "next_eligible_work": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            rc = main([
                "status",
                "--mission-store",
                str(Path(tmp) / "store.json"),
            ])

        self.assertEqual(rc, 0)
        preflight.assert_not_called()

    @mock.patch(
        "controller.runtime_supervisor.RuntimeSupervisor"
    )
    @mock.patch(
        "controller.runtime_supervisor.run_startup_preflight",
        side_effect=StartupPreflightError("profile not ready"),
    )
    def test_run_preflight_failure_blocks_supervisor_construction(
        self,
        preflight,
        supervisor_type,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "sys.stderr",
                new_callable=io.StringIO,
            ) as stderr:
                controller_root = str(
                    Path(runtime_supervisor.__file__).resolve().parent.parent
                )
                with mock.patch.dict(
                    os.environ,
                    {
                        "RVSC_RVSC_REPO": controller_root,
                        "RVSC_CONTROLLER_SHA": "TEST-CONTROLLER-SHA",
                    },
                    clear=False,
                ), mock.patch(
                    "controller.runtime_supervisor.subprocess.run"
                ) as git_run:
                    git_run.side_effect = (
                        mock.Mock(returncode=0, stdout="TEST-CONTROLLER-SHA\n", stderr=""),
                        mock.Mock(returncode=0, stdout="", stderr=""),
                    )
                    rc = main([
                        "run",
                        "--mission-store",
                        str(Path(tmp) / "store.json"),
                    ])

        self.assertEqual(rc, 2)
        self.assertIn("profile not ready", stderr.getvalue())
        preflight.assert_called_once()
        supervisor_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
