# PyOxidizer configuration for Companion.
#
# Produces a single-file binary (~80 MB) containing CPython + all wheels.
# The resulting binary is placed in packaging/dist/companion-bin and then
# bundled into the Tauri app by the BeforeBundle hook.
#
# Usage:
#   pip install pyoxidizer          # or: cargo install pyoxidizer
#   cd packaging && pyoxidizer build --release
#
# The binary lands at:
#   build/<triple>/release/install/companion-bin[.exe]
# Copy it to packaging/dist/ before running `cargo tauri build`.

def make_dist():
    return default_python_distribution()

def make_exe(dist):
    policy = dist.make_python_packaging_policy()

    # Include the stdlib but strip test suites and tcl/tk to keep size down.
    policy.include_distribution_sources = False
    policy.include_distribution_resources = False
    policy.include_test = False
    policy.resources_location = "in-memory"
    policy.resources_location_fallback = "filesystem-relative:lib"

    # Use the distribution-provided pip to install our package.
    python_config = dist.make_python_interpreter_config()

    # Run `fcc-server` as __main__ — equivalent to `python -m cli.entrypoints serve`.
    python_config.run_module = "cli.entrypoints"

    exe = dist.to_python_executable(
        name = "companion-bin",
        packaging_policy = policy,
        config = python_config,
    )

    # Install the project and all its dependencies from pyproject.toml.
    for resource in exe.pip_install(["--no-deps", "-e", ".."]):
        resource.add_location = "in-memory"
        exe.add_python_resource(resource)

    # Install runtime dependencies.
    for resource in exe.pip_install(["-r", "../requirements-frozen.txt"]):
        resource.add_location = "in-memory"
        exe.add_python_resource(resource)

    return exe

def make_install(exe):
    m = FileManifest()
    m.add_python_resource(".", exe)
    return m


# ---------------------------------------------------------------------------
# Register build targets
# ---------------------------------------------------------------------------

register_target("dist", make_dist)
register_target("exe", make_exe, depends = ["dist"], default = True)
register_target("install", make_install, depends = ["exe"], default_build_script = True)

resolve_targets()
