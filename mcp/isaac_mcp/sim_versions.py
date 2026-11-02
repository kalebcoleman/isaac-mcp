"""Pure identity reconciliation for pip and standalone Isaac Sim installations.

The public get_version()[0] omits the fourth package component. In particular,
both 6.0.0 packages report '6.0.0'. Never use it alone to select native adapters.
Build records are data, so another release can be qualified without another
version-specific implementation. API capabilities are checked separately in Kit.
"""

import re

# Official package -> full VERSION shipped in the installations tested here.
# These are not inferred from the major version or the fork's support claims.
RELEASE_BUILDS = {
    "6.0.0.0": "6.0.0-rc.22+release.33481.407f3ea1.gl",
    "6.0.0.1": "6.0.0-rc.59+release.41464.5f2772bc.gl",
    "6.0.1.0": "6.0.1-rc.7+release.42383.32955d8d.gl",
    "6.1.0.0": "6.1.0-rc.26+release.49347.2d230af4.gl",
    "6.2.0.0": "6.2.0-rc.14+release.52805.02189a84.gl",
}

# The owned-CPU adapter also requires the shipped Kit ABI and Replicator build,
# plus live API signature/lifecycle checks. Extension overrides cannot quietly
# inherit a release's qualification. Adding a release is data + live evidence.
CPU_SENSOR_STACKS = {
    "6.0.0.0": ("110.0.0", "1.13.4", "15.14.1"),
    "6.0.0.1": ("110.1.1", "1.13.25", "15.17.0"),
    "6.0.1.0": ("110.1.2", "1.13.27", "15.17.1"),
    "6.1.0.0": ("110.3.0", "1.13.36", "15.17.5"),
    "6.2.0.0": ("110.4.0", "1.13.38", "15.17.5"),
}

# (SimulationManager, Newton extension, loaded Warp). Tensor API availability is
# checked after enabling the installed tensor extension before first Play.
NEWTON_STACKS = {
    "6.0.0.0": ("1.12.0", "0.6.0", "1.12.0"),
    "6.0.0.1": ("1.15.4", "0.7.14", "1.13.0"),
    "6.0.1.0": ("1.15.6", "0.8.1", "1.13.0"),
    "6.1.0.0": ("1.17.1", "1.0.6", "1.16.0"),
    "6.2.0.0": ("1.17.4", "1.1.1", "1.17.0"),
}
NEWTON_LIBRARIES = {
    "6.0.0.0": ("1.0.0", "3.5.0", "3.5.0.2"),
    "6.0.0.1": ("1.2.0", "3.8.0", "3.8.0.3"),
    "6.0.1.0": ("1.2.1", "3.8.0", "3.8.0.3"),
    "6.1.0.0": ("1.5.0", "3.11.0", "3.11.0"),
    "6.2.0.0": ("1.6.1", "3.12.0", "3.12.0"),
}


def newton_stack_issues(identity):
    if not identity.get("qualified_build"):
        return ["Full Isaac Sim build is unqualified or conflicting"]
    expected = NEWTON_STACKS.get(identity.get("release"))
    if expected is None:
        return ["Release has no qualified Newton adapter"]
    extensions = identity.get("extensions", {})
    issues = []
    for name, version in zip(
        ("isaacsim.core.simulation_manager", "isaacsim.physics.newton"), expected
    ):
        actual = extensions.get(name, {})
        if not actual.get("enabled") or actual.get("version") != version:
            issues.append(f"{name}: expected enabled {version}, observed {actual}")
    if (identity.get("warp") or {}).get("version") != expected[2]:
        issues.append("Loaded Warp does not match the qualified Newton stack")
    for name, version in zip(
        ("newton", "mujoco", "mujoco_warp"), NEWTON_LIBRARIES[identity["release"]]
    ):
        actual = identity.get("physics_packages", {}).get(name, {})
        observed = actual.get("loaded_version") or actual.get("installed_version")
        if observed != version:
            issues.append(f"{name}: expected {version}, observed {observed!r}")
        if (
            actual.get("installed_version")
            and actual.get("loaded_version")
            and actual["installed_version"] != actual["loaded_version"]
        ):
            issues.append(f"{name}: loaded and installed versions disagree")
    return issues


def sensor_stack_issues(identity, lidar=False):
    if not identity.get("qualified_build"):
        return ["Full Isaac Sim build is unqualified or conflicting"]
    expected = CPU_SENSOR_STACKS.get(identity.get("release"))
    if expected is None:
        return ["Release has no qualified owned-CPU sensor stack"]
    kit = identity.get("kit_build") or ""
    extensions = identity.get("extensions", {})
    issues = []
    if kit.split("+", 1)[0] != expected[0]:
        issues.append(f"Kit ABI mismatch: expected {expected[0]}, observed {kit!r}")
    for name, version in [("omni.replicator.core", expected[1])] + (
        [("isaacsim.sensors.rtx", expected[2])] if lidar else []
    ):
        actual = extensions.get(name, {})
        if not actual.get("enabled") or actual.get("version") != version:
            issues.append(f"{name}: expected enabled {version}, observed {actual}")
    return issues


def reconcile_identity(packages, builds, reported_core=None, kit=None, extensions=None):
    """Return evidence and conflicts; missing evidence is never guessed.

    `builds` maps the loaded package/app/ISAAC_PATH VERSION locations to text.
    `packages` contains relevant installed distribution versions, when present.
    A standalone distribution can be identified by its exact full build alone.
    """
    issues = []
    versions = set(packages.values())
    if len(versions) > 1:
        issues.append("Isaac Sim package versions disagree")
    package = next(iter(versions)) if len(versions) == 1 else None
    if package and not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", package):
        issues.append("Malformed or non-release Isaac Sim package version")
    full_builds = {x.strip() for x in builds.values() if x and x.strip()}
    if len(full_builds) > 1:
        issues.append("Loaded package/app/ISAAC_PATH VERSION files disagree")
    build = next(iter(full_builds)) if len(full_builds) == 1 else None
    build_release = next((v for v, b in RELEASE_BUILDS.items() if b == build), None)
    if package and build_release and package != build_release:
        issues.append("Package version conflicts with the full standalone build")
    core = build.split("-", 1)[0] if build else None
    if reported_core and core and reported_core != core:
        issues.append("get_version() disagrees with the loaded VERSION file")
    if package and core and ".".join(package.split(".")[:3]) != core:
        issues.append("Package and loaded build release cores disagree")
    release = package or build_release
    qualified = bool(
        release and build and RELEASE_BUILDS.get(release) == build and not issues
    )
    if not build:
        issues.append("Full build identity unavailable")
    if not release:
        issues.append("Four-component release cannot be established")
    return {
        "release": release,
        "build": build,
        "reported_core": reported_core,
        "identity_source": "package and VERSION"
        if package
        else "exact standalone VERSION",
        "packages": dict(packages),
        "version_files": dict(builds),
        "kit_build": kit,
        "extensions": dict(extensions or {}),
        "qualified_build": qualified,
        "issues": issues,
    }
