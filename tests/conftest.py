from __future__ import annotations

import pytest

from orbita_agent import AgentConfig, AgentGateway


@pytest.fixture
def gateway(tmp_path):
    instance = AgentGateway(AgentConfig(home=tmp_path / "agent-home"))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def sample_csv() -> str:
    rows = ["subject_id,group,x,y,noise"]
    for i in range(1, 41):
        group = "A" if i <= 20 else "B"
        x = i / 3
        y = 2.5 * x + (0.05 if i % 2 else -0.05)
        noise = ((i * 17) % 13) - 6
        rows.append(f"s{i:02d},{group},{x:.4f},{y:.4f},{noise}")
    return "\n".join(rows) + "\n"
