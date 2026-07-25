"""Migration runs against a volume holding research that cannot be regenerated.

So the properties under test are not "did it copy" but "does it refuse when it should,
does it verify what it wrote, and is the pre-migration state still there afterwards".
"""

from __future__ import annotations

import pytest

from orbita_agent import AgentConfig, AgentGateway
from orbita_agent.workspace_migration import apply_migration, plan_migration

TENANT = "dkscr711"


@pytest.fixture
def legacy_home(tmp_path):
    """A pre-tenancy home: one database and one workspace, holding two cases."""
    config = AgentConfig(home=tmp_path / "agent-home")
    gateway = AgentGateway(config)
    try:
        first = gateway.create_case(name="Operation Golden Gate", goal="")
        gateway.add_inline_file(
            case_id=first["id"], filename="rows.csv", content="x,y\n1,2\n3,4\n5,6\n"
        )
        gateway.create_case(name="Language Tower", goal="")
    finally:
        gateway.close()
    return config


def test_a_dry_run_reports_the_work_without_doing_it(legacy_home):
    plan = plan_migration(legacy_home, TENANT)

    assert plan.blocked is None
    assert "orbita_agent.db" in [source.name for source, _ in plan.files]
    assert "workspace" in [source.name for source, _ in plan.directories]
    assert len(plan.source_case_ids) == 2
    # Nothing exists on the target side yet.
    assert not plan.target_home.exists()


def test_migration_copies_verifies_and_preserves_the_original(legacy_home):
    plan = plan_migration(legacy_home, TENANT)
    result = apply_migration(plan)

    assert result["migrated"] is True
    assert result["case_count"] == 2
    assert result["case_ids"] == plan.source_case_ids

    # The original is untouched, so rollback needs no action.
    assert legacy_home.db_path.exists()
    assert (legacy_home.home / "workspace").is_dir()

    # And the tenant now reads its own copy through a normal gateway.
    tenant_gateway = AgentGateway(legacy_home.for_tenant(TENANT))
    try:
        names = {case["name"] for case in tenant_gateway.list_cases()}
        assert names == {"Operation Golden Gate", "Language Tower"}
    finally:
        tenant_gateway.close()


def test_uploaded_files_survive_the_move(legacy_home):
    apply_migration(plan_migration(legacy_home, TENANT))

    tenant_gateway = AgentGateway(legacy_home.for_tenant(TENANT))
    try:
        case = next(
            c for c in tenant_gateway.list_cases() if c["name"] == "Operation Golden Gate"
        )
        context = tenant_gateway.case_context(case["id"])
        assert context["files"][0]["name"] == "rows.csv"
        assert context["files"][0]["profile"]["rows"] == 3
    finally:
        tenant_gateway.close()


def test_migration_refuses_a_tenant_that_already_has_research(legacy_home):
    apply_migration(plan_migration(legacy_home, TENANT))

    second = plan_migration(legacy_home, TENANT)
    assert second.blocked is not None
    assert "already exists" in second.blocked
    with pytest.raises(RuntimeError, match="already exists"):
        apply_migration(second)


def test_migration_is_not_silently_repeatable(legacy_home):
    """Running it twice must not append, duplicate, or overwrite."""
    first = apply_migration(plan_migration(legacy_home, TENANT))
    blocked = plan_migration(legacy_home, TENANT)

    assert blocked.blocked
    tenant_gateway = AgentGateway(legacy_home.for_tenant(TENANT))
    try:
        assert len(tenant_gateway.list_cases()) == first["case_count"]
    finally:
        tenant_gateway.close()


def test_a_failed_verification_is_raised_not_reported_as_success(legacy_home, monkeypatch):
    plan = plan_migration(legacy_home, TENANT)

    digests = iter(["aaa", "bbb"] * 50)
    monkeypatch.setattr(
        "orbita_agent.workspace_migration._sha256", lambda path: next(digests)
    )
    with pytest.raises(RuntimeError, match="did not verify"):
        apply_migration(plan)


def test_a_case_count_mismatch_is_caught(legacy_home, monkeypatch):
    plan = plan_migration(legacy_home, TENANT)
    plan.source_case_ids = [*plan.source_case_ids, "case_that_will_not_arrive"]

    with pytest.raises(RuntimeError, match="did not match after migration"):
        apply_migration(plan)


def test_an_empty_home_migrates_nothing_and_says_so(tmp_path):
    empty = AgentConfig(home=tmp_path / "empty").ensure()
    result = apply_migration(plan_migration(empty, TENANT))

    assert result["migrated"] is False
    assert result["reason"] == "nothing to migrate"


def test_deployment_level_databases_stay_at_the_base_home(legacy_home):
    """Identity and tenancy records decide who may sign in; they are not tenant state."""
    (legacy_home.home / "orbita_oauth.db").write_bytes(b"oauth")
    (legacy_home.home / "orbita_tenants.db").write_bytes(b"tenants")

    plan = plan_migration(legacy_home, TENANT)
    copied = [source.name for source, _ in plan.files]

    assert "orbita_oauth.db" not in copied
    assert "orbita_tenants.db" not in copied

    apply_migration(plan)
    assert not (plan.target_home / "orbita_oauth.db").exists()
    assert not (plan.target_home / "orbita_tenants.db").exists()


def test_two_tenants_can_each_adopt_from_a_shared_history_only_deliberately(legacy_home):
    """Adopting the same legacy workspace twice is possible but never implicit."""
    apply_migration(plan_migration(legacy_home, "first-tenant"))
    second = plan_migration(legacy_home, "second-tenant")

    # A different tenant is a different directory, so it is not blocked, but it is a
    # separate explicit invocation rather than something that happens on its own.
    assert second.blocked is None
    assert second.target_home != legacy_home.for_tenant("first-tenant").home
