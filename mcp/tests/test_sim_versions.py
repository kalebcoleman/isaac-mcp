"""Identity reconciliation is independent of Kit and cannot collapse 6.0.0 builds."""

import pytest
from isaac_mcp.sim_versions import (
    RELEASE_BUILDS,
    CPU_SENSOR_STACKS,
    reconcile_identity,
    sensor_stack_issues,
)


@pytest.mark.parametrize("release,build", RELEASE_BUILDS.items())
def test_package_and_full_build_agree(release, build):
    result = reconcile_identity(
        {"isaacsim": release, "isaacsim-core": release},
        {"loaded/VERSION": build},
        ".".join(release.split(".")[:3]),
        "110.test",
    )
    assert result["release"] == release and result["qualified_build"]
    assert result["issues"] == []


@pytest.mark.parametrize("release,build", RELEASE_BUILDS.items())
def test_standalone_identity_without_pip(release, build):
    result = reconcile_identity({}, {"/standalone/VERSION": build})
    assert result["release"] == release and result["qualified_build"]


@pytest.mark.parametrize(
    "packages,builds,core",
    [
        ({"isaacsim": "6.0.0.1"}, {"a": RELEASE_BUILDS["6.0.0.0"]}, "6.0.0"),
        (
            {"isaacsim": "6.0.0.1", "isaacsim-core": "6.0.0.0"},
            {"a": RELEASE_BUILDS["6.0.0.1"]},
            "6.0.0",
        ),
        (
            {"isaacsim": "6.2.0.0"},
            {"a": RELEASE_BUILDS["6.2.0.0"], "b": RELEASE_BUILDS["6.1.0.0"]},
            "6.2.0",
        ),
        ({"isaacsim": "6.2.0.0"}, {"a": RELEASE_BUILDS["6.2.0.0"]}, "6.1.0"),
        ({}, {}, "6.0.0"),
        ({"isaacsim": "6.2.0.0"}, {}, "6.2.0"),
        ({"isaacsim": "6.2.0"}, {"a": RELEASE_BUILDS["6.2.0.0"]}, "6.2.0"),
        ({}, {"a": "6.0.0"}, "6.0.0"),
    ],
)
def test_missing_ambiguous_or_conflicting_identity_never_qualifies(
    packages, builds, core
):
    result = reconcile_identity(packages, builds, core)
    assert not result["qualified_build"] and result["issues"]


def test_future_build_is_identified_but_not_qualified():
    result = reconcile_identity(
        {"isaacsim": "6.3.0.0"}, {"a": "6.3.0-rc.1+release.future"}, "6.3.0"
    )
    assert result["release"] == "6.3.0.0" and not result["qualified_build"]


def test_different_new_build_of_known_release_is_not_assumed_validated():
    result = reconcile_identity(
        {"isaacsim": "6.2.0.0"}, {"a": "6.2.0-rc.15+release.changed"}, "6.2.0"
    )
    assert not result["qualified_build"]


@pytest.mark.parametrize("release,stack", CPU_SENSOR_STACKS.items())
def test_sensor_adapter_requires_expected_kit_and_enabled_extension_versions(
    release, stack
):
    kit, rep, rtx = stack
    identity = reconcile_identity(
        {"isaacsim": release},
        {"a": RELEASE_BUILDS[release]},
        kit=kit + "+measured-build",
        extensions={
            "omni.replicator.core": {"enabled": True, "version": rep},
            "isaacsim.sensors.rtx": {"enabled": True, "version": rtx},
        },
    )
    assert not sensor_stack_issues(identity, lidar=True)
    identity["extensions"]["isaacsim.sensors.rtx"]["enabled"] = False
    assert sensor_stack_issues(identity, lidar=True)
    assert not sensor_stack_issues(
        identity
    )  # a USD camera does not depend on RTX sensor Python
    identity["extensions"]["omni.replicator.core"]["version"] = "0.0.0"
    assert sensor_stack_issues(identity)
    identity["extensions"]["omni.replicator.core"]["version"] = rep
    identity["kit_build"] = "unknown"
    assert sensor_stack_issues(identity)


@pytest.mark.parametrize("release", RELEASE_BUILDS)
def test_newton_qualification_rejects_extension_and_loaded_warp_drift(release):
    from isaac_mcp.sim_versions import (
        NEWTON_STACKS,
        NEWTON_LIBRARIES,
        newton_stack_issues,
    )

    sm, newton, warp = NEWTON_STACKS[release]
    identity = reconcile_identity(
        {"isaacsim": release},
        {"a": RELEASE_BUILDS[release]},
        extensions={
            "isaacsim.core.simulation_manager": {"enabled": True, "version": sm},
            "isaacsim.physics.newton": {"enabled": True, "version": newton},
        },
    )
    identity["warp"] = {"version": warp}
    identity["physics_packages"] = {
        name: {"installed_version": version}
        for name, version in zip(
            ("newton", "mujoco", "mujoco_warp"), NEWTON_LIBRARIES[release]
        )
    }
    assert not newton_stack_issues(identity)
    identity["warp"]["version"] = "unqualified-nightly"
    assert newton_stack_issues(identity)
    identity["warp"]["version"] = warp
    identity["extensions"]["isaacsim.physics.newton"]["enabled"] = False
    assert newton_stack_issues(identity)
