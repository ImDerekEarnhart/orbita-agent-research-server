"""Prove tenant isolation against a deployed build, using only the installed package.

The test suite does not ship in the runtime image, so G21's requirement that isolation be
demonstrated "against the deployed build" cannot be met by running pytest on the container.
This script is the substitute: it imports the installed orbita_agent, builds two tenant
gateways from whatever code is actually running, and asserts that neither can reach the
other's research.

It writes only to a temporary directory and never touches ORBITA_AGENT_HOME, so running it
against production cannot alter production data.

Emits a JSON receipt on stdout. Exit code 0 means isolation held.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from orbita_agent import AgentConfig, AgentGateway
from orbita_agent import __version__
from orbita_agent.config import tenant_slug

CHECKS: list[dict[str, object]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="orbita-isolation-") as scratch:
        base = AgentConfig(home=Path(scratch) / "home")

        alice_config = base.for_tenant("alice-tenant")
        bob_config = base.for_tenant("bob-tenant")

        check(
            "separate_databases",
            alice_config.db_path != bob_config.db_path,
            f"{alice_config.db_path.name} vs {bob_config.db_path.name}",
        )
        check(
            "separate_workspaces",
            alice_config.workspace != bob_config.workspace,
        )
        check(
            "neither_home_contains_the_other",
            alice_config.home not in bob_config.home.parents
            and bob_config.home not in alice_config.home.parents,
        )
        check(
            "colliding_names_stay_distinct",
            tenant_slug("Ada Lovelace") != tenant_slug("ada-lovelace"),
        )

        contained = True
        for hostile in ("../escape", "..", "a/../../b", "C:\\Windows", "./."):
            resolved = base.for_tenant(hostile).home.resolve()
            if (Path(scratch) / "home" / "tenants").resolve() not in resolved.parents:
                contained = False
        check("hostile_tenant_names_contained", contained)

        alice = AgentGateway(alice_config)
        bob = AgentGateway(bob_config)
        try:
            case = alice.create_case(name="Alice private research", goal="")
            case_id = case["id"]

            check("owner_can_read_own_case", alice.case_context(case_id)["case"]["id"] == case_id)

            try:
                bob.case_context(case_id)
                check("cross_tenant_read_refused", False, "bob read alice's case")
            except KeyError:
                check("cross_tenant_read_refused", True, "KeyError as expected")

            try:
                bob.add_inline_file(case_id=case_id, filename="x.csv", content="a\n1\n")
                check("cross_tenant_write_refused", False, "bob wrote to alice's case")
            except KeyError:
                check("cross_tenant_write_refused", True, "KeyError as expected")

            try:
                bob.compile_plan(case_id)
                check("cross_tenant_compile_refused", False, "bob compiled alice's case")
            except KeyError:
                check("cross_tenant_compile_refused", True, "KeyError as expected")

            check("cross_tenant_listing_empty", bob.list_cases() == [])
            check("owner_listing_intact", len(alice.list_cases()) == 1)
        finally:
            alice.close()
            bob.close()

    passed = all(entry["passed"] for entry in CHECKS)
    receipt = {
        "isolation_verified": passed,
        "package_version": __version__,
        "git_commit": os.getenv("GIT_COMMIT_SHA", "unset"),
        "python": sys.version.split()[0],
        "checks": CHECKS,
        "checks_passed": sum(1 for entry in CHECKS if entry["passed"]),
        "checks_total": len(CHECKS),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
