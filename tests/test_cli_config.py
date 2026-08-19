from pathlib import Path

from orbita_agent.cli import _config


def test_cli_config_preserves_language_limit_runtime(monkeypatch, tmp_path: Path):
    kernel = tmp_path / "frozen-kernel"
    wrapper = tmp_path / "offline-lean"
    monkeypatch.setenv("ORBITA_AGENT_HOME", str(tmp_path / "default-home"))
    monkeypatch.setenv("ORBITA_LANGUAGE_LIMIT_KERNEL_ROOT", str(kernel))
    monkeypatch.setenv("ORBITA_LEAN_EXECUTABLE", str(wrapper))

    config = _config(str(tmp_path / "explicit-home"))

    assert config.home == tmp_path / "explicit-home"
    assert config.lean_kernel_root == kernel
    assert config.lean_executable == str(wrapper)
