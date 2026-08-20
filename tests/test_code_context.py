from __future__ import annotations

import pytest

from orbita_agent.code_context import (
    CodeContextError,
    compress_code_context,
)


def test_code_context_retains_source_and_test_while_dropping_distractors() -> None:
    files = [
        {
            "path": "retry_logic.py",
            "content": "def run_with_retries(operation, max_attempts):\n    pass\n",
        },
        {
            "path": "tests/test_retry_logic.py",
            "content": "from retry_logic import run_with_retries\n",
        },
        {
            "path": "weather_report.py",
            "content": "def rainfall_total(values):\n    return sum(values)\n",
        },
    ]

    result = compress_code_context(
        "run_with_retries exceeds max_attempts",
        files,
        max_files=2,
    )

    assert result["receipt"]["retained_paths"] == [
        "retry_logic.py",
        "tests/test_retry_logic.py",
    ]
    assert result["receipt"]["dropped_paths"] == ["weather_report.py"]
    assert result["receipt"]["model_calls"] == 0


def test_code_context_is_deterministic_and_bounded() -> None:
    files = [{"path": "module.py", "content": "def target():\n    return 1\n"}]
    assert compress_code_context("target is wrong", files) == compress_code_context(
        "target is wrong",
        files,
    )
    with pytest.raises(CodeContextError, match="between 1 and 32"):
        compress_code_context("target is wrong", files, max_files=0)
    with pytest.raises(CodeContextError, match="duplicate file path"):
        compress_code_context("target is wrong", files + files)
