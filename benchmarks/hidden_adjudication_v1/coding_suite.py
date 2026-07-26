from __future__ import annotations

from typing import Any

_DISTRACTOR_FILES = (
    ("catalog.py", "def sort_titles(titles):\n    return sorted(titles, key=str.casefold)\n"),
    ("weather.py", "def rainfall_total(values):\n    return sum(values)\n"),
    ("geometry.py", "def rectangle_area(width, height):\n    return width * height\n"),
    ("formatting.py", "def normalize_heading(value):\n    return value.strip().title()\n"),
    ("inventory.py", "def remaining_stock(received, sold):\n    return received - sold\n"),
    ("calendar_math.py", "def whole_weeks(days):\n    return days // 7\n"),
    ("currency.py", "def cents_to_dollars(cents):\n    return cents / 100\n"),
    ("temperature.py", "def celsius_to_kelvin(value):\n    return value + 273.15\n"),
    ("pagination.py", "def page_count(items, size):\n    return (items + size - 1) // size\n"),
    ("coordinates.py", "def manhattan_distance(x1, y1, x2, y2):\n    return abs(x1-x2) + abs(y1-y2)\n"),
    ("labels.py", "def canonical_label(value):\n    return '-'.join(value.lower().split())\n"),
    ("statistics.py", "def arithmetic_mean(values):\n    return sum(values) / len(values)\n"),
)


def _with_distractors(files: list[dict[str, str]]) -> list[dict[str, str]]:
    return files + [
        {"path": path, "content": content}
        for path, content in _DISTRACTOR_FILES
    ]


def coding_tasks() -> list[dict[str, Any]]:
    return [
        {
            "id": "retry_attempt_limit",
            "issue": (
                "run_with_retries in retry_logic.py treats max_attempts as retries plus one. "
                "max_attempts must be the total number of operation calls."
            ),
            "target_path": "retry_logic.py",
            "files": _with_distractors(
                [
                    {
                        "path": "retry_logic.py",
                        "content": (
                            "def run_with_retries(operation, max_attempts):\n"
                            "    last_error = None\n"
                            "    for _attempt in range(max_attempts + 1):\n"
                            "        try:\n"
                            "            return operation()\n"
                            "        except Exception as exc:\n"
                            "            last_error = exc\n"
                            "    raise last_error\n"
                        ),
                    },
                    {
                        "path": "tests/test_retry_logic.py",
                        "content": (
                            "import pytest\n\n"
                            "from retry_logic import run_with_retries\n\n\n"
                            "def test_max_attempts_is_total_calls():\n"
                            "    calls = 0\n\n"
                            "    def fail():\n"
                            "        nonlocal calls\n"
                            "        calls += 1\n"
                            "        raise RuntimeError('no')\n\n"
                            "    with pytest.raises(RuntimeError):\n"
                            "        run_with_retries(fail, 3)\n"
                            "    assert calls == 3\n\n\n"
                            "def test_returns_after_eventual_success():\n"
                            "    outcomes = iter([RuntimeError('first'), 'ok'])\n\n"
                            "    def operation():\n"
                            "        value = next(outcomes)\n"
                            "        if isinstance(value, Exception):\n"
                            "            raise value\n"
                            "        return value\n\n"
                            "    assert run_with_retries(operation, 2) == 'ok'\n"
                        ),
                    },
                ]
            ),
        },
        {
            "id": "cache_time_units",
            "issue": (
                "is_fresh in cache_policy.py subtracts created_at_seconds from "
                "now_milliseconds without converting units, so fresh entries appear stale."
            ),
            "target_path": "cache_policy.py",
            "files": _with_distractors(
                [
                    {
                        "path": "cache_policy.py",
                        "content": (
                            "def is_fresh(created_at_seconds, ttl_seconds, now_milliseconds):\n"
                            "    age = now_milliseconds - created_at_seconds\n"
                            "    return age <= ttl_seconds\n"
                        ),
                    },
                    {
                        "path": "tests/test_cache_policy.py",
                        "content": (
                            "from cache_policy import is_fresh\n\n\n"
                            "def test_recent_entry_is_fresh():\n"
                            "    assert is_fresh(1_000, 60, 1_030_000) is True\n\n\n"
                            "def test_expired_entry_is_not_fresh():\n"
                            "    assert is_fresh(1_000, 60, 1_061_000) is False\n"
                        ),
                    },
                ]
            ),
        },
        {
            "id": "inactive_admin_delete",
            "issue": (
                "can_delete in permissions.py incorrectly allows an inactive admin because "
                "the active check does not apply to both privileged roles."
            ),
            "target_path": "permissions.py",
            "files": _with_distractors(
                [
                    {
                        "path": "permissions.py",
                        "content": (
                            "def can_delete(user):\n"
                            "    role = user.get('role')\n"
                            "    return user.get('active', False) and role == 'owner' or role == 'admin'\n"
                        ),
                    },
                    {
                        "path": "tests/test_permissions.py",
                        "content": (
                            "from permissions import can_delete\n\n\n"
                            "def test_inactive_admin_cannot_delete():\n"
                            "    assert can_delete({'active': False, 'role': 'admin'}) is False\n\n\n"
                            "def test_active_admin_and_owner_can_delete():\n"
                            "    assert can_delete({'active': True, 'role': 'admin'}) is True\n"
                            "    assert can_delete({'active': True, 'role': 'owner'}) is True\n\n\n"
                            "def test_active_member_cannot_delete():\n"
                            "    assert can_delete({'active': True, 'role': 'member'}) is False\n"
                        ),
                    },
                ]
            ),
        },
    ]

