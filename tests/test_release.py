"""T40: inspect both workflows and execute their offline release commands verbatim."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TAG_ONLY = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def workflow(name: str = "release") -> dict[str, Any]:
    # JSON-compatible YAML needs no additional Python dependency.
    return json.loads((ROOT / f".github/workflows/{name}.yml").read_text())


def named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def action_steps(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [
        step for step in job["steps"] if step.get("uses", "").startswith(f"{action}@")
    ]


def run_command(
    command: str, cwd: Path, **environment: str
) -> subprocess.CompletedProcess[str]:
    # Use the same explicit bash failure semantics as the hosted runners. Only
    # the extracted version/awk/checksum/gh commands run; never uv/npm/network.
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
        cwd=cwd,
        env={
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            **environment,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def version_check(
    cwd: Path,
    output: Path,
    *,
    event: str = "push",
    ref: str = "refs/tags/v0.3.0",
    tag: str = "v0.3.0",
) -> subprocess.CompletedProcess[str]:
    command = named_step(workflow()["jobs"]["check"], "Check release versions")["run"]
    return run_command(
        command,
        cwd,
        GITHUB_OUTPUT=str(output),
        GITHUB_EVENT_NAME=event,
        GITHUB_REF=ref,
        GITHUB_REF_NAME=tag,
    )


def write_versions(root: Path, python: str, desktop: str) -> None:
    (root / "apps/desktop").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{python}"\n')
    (root / "apps/desktop/package.json").write_text(json.dumps({"version": desktop}))


def test_release_triggers_only_tags_or_manual_builds() -> None:
    release = workflow()
    assert release["on"] == {
        "push": {"tags": ["v*.*.*"]},
        "workflow_dispatch": {},
    }
    jobs = release["jobs"]
    assert set(jobs) == {
        "check",
        "build-python",
        "publish-pypi",
        "build-desktop",
        "release",
    }
    for name in ("publish-pypi", "release"):
        assert jobs[name]["if"] == TAG_ONLY
    # Manual dispatch must still build both kinds of artifact, including when
    # dispatching at a tag; it must not depend on a skipped publishing job.
    assert "if" not in jobs["check"]
    assert "needs" not in jobs["check"]
    for name in ("build-python", "build-desktop"):
        assert jobs[name]["needs"] == "check"
        assert "if" not in jobs[name]
    assert set(jobs["publish-pypi"]["needs"]) == {"check", "build-python"}
    assert set(jobs["release"]["needs"]) == set(jobs) - {"release"}


def test_trusted_publishing_and_minimal_permissions() -> None:
    for name in ("release", "ci"):
        definition = workflow(name)
        assert definition["permissions"] == {"contents": "read"}
        for job_id, job in definition["jobs"].items():
            expected = {"contents": "read"}
            if name == "release" and job_id == "publish-pypi":
                expected = {"id-token": "write"}
            elif name == "release" and job_id == "release":
                expected = {"contents": "write"}
            assert job.get("permissions", definition["permissions"]) == expected
            for step in action_steps(job, "actions/checkout"):
                assert step["with"]["persist-credentials"] is False
    publish = workflow()["jobs"]["publish-pypi"]
    assert publish["runs-on"] == "ubuntu-latest"
    assert publish["environment"] == "pypi"
    assert len(publish["steps"]) == 2
    (download,) = action_steps(publish, "actions/download-artifact")
    assert download["with"] == {"name": "python-dist", "path": "dist"}
    (pypi,) = action_steps(publish, "pypa/gh-action-pypi-publish")
    assert set(pypi) == {"name", "uses"}  # OIDC defaults; no alternate credentials.
    body = json.dumps(
        {key: value for key, value in publish.items() if key != "permissions"}
    )
    assert not re.search(r"password|token|secrets\s*[.\[]", body, re.I)


def test_only_github_release_step_receives_github_token() -> None:
    release = workflow()
    publish = named_step(release["jobs"]["release"], "Create GitHub Release")
    assert publish.pop("env") == {
        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
        "GH_REPO": "${{ github.repository }}",
    }
    for definition in (release, workflow("ci")):
        body = json.dumps(definition)
        assert not re.search(r"secrets\s*[.\[]|github\.token|password", body, re.I)
        assert "GITHUB_TOKEN" not in body
        assert "GH_TOKEN" not in body


@pytest.mark.parametrize("name", ["release", "ci"])
def test_every_action_is_sha_pinned(name: str) -> None:
    for job in workflow(name)["jobs"].values():
        assert "uses" not in job  # No uninspected reusable workflow.
        for step in job["steps"]:
            if "uses" not in step:
                continue
            action = step["uses"]
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
            assert re.search(r"\bv\d+\.\d+\.\d+\b", step["name"]), action


def test_version_check_accepts_real_package_versions(tmp_path: Path) -> None:
    output = tmp_path / "outputs"
    result = version_check(ROOT, output)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "version=0.3.0\n"


@pytest.mark.parametrize(
    ("tag", "python", "desktop"),
    [
        ("v0.3.1", "0.3.0", "0.3.0"),
        ("v0.3.0", "0.3.1", "0.3.0"),
        ("v0.3.0", "0.3.0", "0.3.1"),
        ("v0.3.0", "0.3.1", "0.3.1"),
        ("v0.3.1", "0.3.1", "0.3.0"),
        ("v0.3.1", "0.3.0", "0.3.1"),
    ],
)
def test_version_check_rejects_disagreement(
    tmp_path: Path, tag: str, python: str, desktop: str
) -> None:
    write_versions(tmp_path, python, desktop)
    output = tmp_path / "outputs"
    result = version_check(tmp_path, output, tag=tag, ref=f"refs/tags/{tag}")
    assert result.returncode != 0
    assert "versions disagree" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "tag", ["v0.3", "v0.3.0.1", "v0.3.0-rc1", "v00.3.0", "0.3.0", "v0.3.0;exit 0"]
)
def test_version_check_rejects_non_release_versions(tmp_path: Path, tag: str) -> None:
    write_versions(tmp_path, tag.removeprefix("v"), tag.removeprefix("v"))
    result = version_check(
        tmp_path, tmp_path / "outputs", tag=tag, ref=f"refs/tags/{tag}"
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("event", "ref", "tag", "accepted"),
    [
        ("workflow_dispatch", "refs/heads/main", "main", True),
        ("workflow_dispatch", "refs/tags/v0.3.0", "v0.3.0", True),
        ("push", "refs/heads/v0.3.0", "v0.3.0", False),
        ("pull_request", "refs/pull/1/merge", "1/merge", False),
    ],
)
def test_version_check_dispatch_and_non_tag_events(
    tmp_path: Path, event: str, ref: str, tag: str, accepted: bool
) -> None:
    result = version_check(ROOT, tmp_path / "outputs", event=event, ref=ref, tag=tag)
    assert (result.returncode == 0) is accepted, result.stderr


def test_ci_builds_same_artifacts_without_publishing() -> None:
    ci = workflow("ci")
    assert ci["on"] == {"push": {"branches": ["main"]}, "pull_request": {}}
    assert {"checks", "wheel", "postgres", "postgres-e2e", "desktop-check"} <= ci[
        "jobs"
    ].keys()
    release = workflow()["jobs"]
    desktop = {
        key: value for key, value in release["build-desktop"].items() if key != "needs"
    }
    assert ci["jobs"]["build-desktop"] == desktop
    for job in (ci["jobs"]["wheel"], release["build-python"]):
        assert job["runs-on"] == "ubuntu-latest"
        steps = job["steps"]
        build = next(step for step in steps if step.get("run") == "uv build")
        (upload,) = action_steps(job, "actions/upload-artifact")
        assert steps.index(build) < steps.index(upload)
        assert upload["with"] == {
            "name": "python-dist",
            "path": "dist/*.tar.gz\ndist/*.whl",
            "if-no-files-found": "error",
        }
        assert "if" not in build and "if" not in upload
    for name, job in ci["jobs"].items():
        assert "if" not in job and "needs" not in job, name
        body = json.dumps(job)
        assert "gh-action-pypi-publish" not in body
        assert "gh release" not in body
        assert "secrets." not in body
        assert "id-token" not in body
        for step in job["steps"]:
            command = step.get("run", "")
            assert not re.search(r"(?:uv|npm|twine) (?:publish|upload)", command)
            if "--publish" in command:
                assert command.endswith("--publish never")


def test_desktop_builds_each_platform_without_publishing() -> None:
    build = workflow()["jobs"]["build-desktop"]
    assert build["runs-on"] == "${{ matrix.os }}"
    assert build["strategy"] == {
        "fail-fast": False,
        "matrix": {
            "include": [
                {
                    "os": "macos-15",
                    "platform": "mac",
                    "arch": "arm64",
                    "extension": "dmg",
                    "target": "darwin-arm64",
                },
                {
                    "os": "macos-15-intel",
                    "platform": "mac",
                    "arch": "x64",
                    "extension": "dmg",
                    "target": "darwin-x64",
                },
                {
                    "os": "ubuntu-latest",
                    "platform": "linux",
                    "arch": "x64",
                    "extension": "AppImage",
                    "target": "linux-x64",
                },
            ]
        },
    }
    assert build["defaults"] == {"run": {"working-directory": "apps/desktop"}}
    assert build["env"] == {"CSC_IDENTITY_AUTO_DISCOVERY": "false"}
    commands = [step["run"] for step in build["steps"] if "run" in step]
    assert len(commands) == 7
    assert commands[0:6] == [
        "uv build --wheel",
        "mkdir -p apps/desktop/build\n"
        "uv export --frozen --no-dev --no-emit-project --no-hashes "
        "--output-file apps/desktop/build/runtime-requirements.txt",
        "npm run bundle-runtime -- --target ${{ matrix.target }} "
        "--wheel ../../dist/*.whl --requirements build/runtime-requirements.txt",
        "npm ci",
        "npm run make-icon",
        "./node_modules/.bin/electron-builder --${{ matrix.platform }} "
        "--${{ matrix.arch }} --publish never",
    ]
    assert all(
        "if" not in step
        for step in build["steps"]
        if step["name"] != "Check compressed dmg size"
    )
    assert (
        named_step(build, "Check compressed dmg size")["if"]
        == "matrix.platform == 'mac'"
    )
    (node,) = action_steps(build, "actions/setup-node")
    assert node["with"]["node-version"] == "22"
    (upload,) = action_steps(build, "actions/upload-artifact")
    assert upload["with"] == {
        "name": "desktop-${{ matrix.platform }}-${{ matrix.arch }}",
        "path": "apps/desktop/dist/specvania-*-${{ matrix.arch }}."
        "${{ matrix.extension }}",
        "if-no-files-found": "error",
    }


def test_changelog_has_initial_release_and_required_history() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert "https://keepachangelog.com/en/1.1.0/" in changelog
    assert "https://semver.org/spec/v2.0.0.html" in changelog
    assert changelog.index("## [Unreleased]\n") < changelog.index(
        "## [0.3.0] - Unreleased\n"
    )
    for term in (
        "desktop game",
        "studio",
        "castle",
        "Monaco",
        "token budget",
        "stamina",
        "English",
        "Brazilian Portuguese",
        "Watch demo",
        "Check the party",
        "0.2.0",
    ):
        assert term in changelog


def test_release_extracts_real_changelog_section(tmp_path: Path) -> None:
    jobs = workflow()["jobs"]
    check = named_step(jobs["check"], "Extract changelog section")
    release = named_step(jobs["release"], "Extract changelog section")
    assert jobs["check"]["outputs"] == {
        "version": "${{ steps.versions.outputs.version }}"
    }
    assert named_step(jobs["check"], "Check release versions")["id"] == "versions"
    assert check["env"] == {"VERSION": "${{ steps.versions.outputs.version }}"}
    assert release["env"] == {"VERSION": "${{ needs.check.outputs.version }}"}
    assert check["run"] == release["run"]
    real = (ROOT / "CHANGELOG.md").read_text()
    # Exercise the exact command on the real changelog, then with an older
    # section appended to prove that extraction stops at the next heading.
    for suffix in ("", "\n## [0.2.0] - Unreleased\n\nOlder history sentinel.\n"):
        (tmp_path / "CHANGELOG.md").write_text(real + suffix)
        result = run_command(release["run"], tmp_path, VERSION="0.3.0")
        assert result.returncode == 0, result.stderr
        extracted = (tmp_path / "release-notes.md").read_text()
        assert (
            extracted.strip() == ("## [0.3.0]" + real.split("## [0.3.0]", 1)[1]).strip()
        )
        assert "## [Unreleased]" not in extracted
        assert "Older history sentinel" not in extracted
    result = run_command(release["run"], tmp_path, VERSION="0.3.1")
    assert result.returncode != 0
    assert (tmp_path / "release-notes.md").read_text() == ""


def release_artifacts(root: Path) -> list[Path]:
    artifacts = root / "artifacts"
    artifacts.mkdir()
    paths = [
        artifacts / f"specvania-0.3.0{suffix}"
        for suffix in (
            ".tar.gz",
            "-py3-none-any.whl",
            "-arm64.dmg",
            "-x64.dmg",
            "-x64.AppImage",
        )
    ]
    for path in paths:
        path.write_bytes(f"Offline artifact fixture: {path.name}".encode())
    return paths


def test_release_checksums_and_attaches_every_artifact(tmp_path: Path) -> None:
    release = workflow()["jobs"]["release"]
    assert [
        step["with"] for step in action_steps(release, "actions/download-artifact")
    ] == [
        {"name": "python-dist", "path": "artifacts"},
        {"pattern": "desktop-*", "path": "artifacts", "merge-multiple": True},
    ]
    paths = release_artifacts(tmp_path)
    checksum_step = named_step(release, "Generate SHA256SUMS")
    result = run_command(checksum_step["run"], tmp_path)
    assert result.returncode == 0, result.stderr
    expected = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    manifest = tmp_path / "artifacts/SHA256SUMS"
    actual = {
        name: digest
        for digest, name in (line.split() for line in manifest.read_text().splitlines())
    }
    assert actual == expected
    # gh is an offline recorder. Run the actual workflow command and inspect
    # argv, so missing assets, flags, notes, or unsafe shell quoting fail here.
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        "from pathlib import Path\n"
        "Path('gh-argv.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    gh.chmod(0o755)
    (tmp_path / "release-notes.md").write_text("release notes fixture")
    publish = named_step(release, "Create GitHub Release")
    assert release["steps"].index(checksum_step) < release["steps"].index(publish)
    assert release["steps"].index(
        named_step(release, "Extract changelog section")
    ) < release["steps"].index(publish)
    result = run_command(
        publish["run"],
        tmp_path,
        GITHUB_REF_NAME="v0.3.0",
        PATH=f"{binary}{os.pathsep}{os.environ['PATH']}",
    )
    assert result.returncode == 0, result.stderr
    args = json.loads((tmp_path / "gh-argv.json").read_text())
    assert args[:8] == [
        "release",
        "create",
        "v0.3.0",
        "--verify-tag",
        "--title",
        "v0.3.0",
        "--notes-file",
        "release-notes.md",
    ]
    assert set(args[8:]) == {
        str(path.relative_to(tmp_path)) for path in [*paths, manifest]
    }


@pytest.mark.parametrize("missing", range(5))
def test_release_fails_if_any_artifact_is_missing(tmp_path: Path, missing: int) -> None:
    paths = release_artifacts(tmp_path)
    paths[missing].unlink()
    command = named_step(workflow()["jobs"]["release"], "Generate SHA256SUMS")["run"]
    result = run_command(command, tmp_path)
    assert result.returncode != 0


@pytest.mark.parametrize("name", ["release", "ci"])
def test_desktop_runtime_built_from_frozen_export_for_every_target(name: str) -> None:
    job = workflow(name)["jobs"]["build-desktop"]
    steps = job["steps"]
    rows = job["strategy"]["matrix"]["include"]
    assert {row["target"] for row in rows} == {
        "darwin-arm64",
        "darwin-x64",
        "linux-x64",
    }
    pins = json.loads((ROOT / "apps/desktop/runtime.json").read_text())
    assert set(pins["targets"]) == {row["target"] for row in rows}
    wheel = named_step(job, "Build runtime wheel")
    export = named_step(job, "Export locked runtime dependencies")
    bundle = named_step(job, "Bundle pinned runtime")
    installer = named_step(job, "Build unsigned installer without publishing")
    assert wheel["working-directory"] == "./"
    assert wheel["run"] == "uv build --wheel"
    assert export["working-directory"] == "./"
    assert export["run"] == (
        "mkdir -p apps/desktop/build\n"
        "uv export --frozen --no-dev --no-emit-project --no-hashes "
        "--output-file apps/desktop/build/runtime-requirements.txt"
    )
    assert bundle["run"] == (
        "npm run bundle-runtime -- --target ${{ matrix.target }} "
        "--wheel ../../dist/*.whl --requirements build/runtime-requirements.txt"
    )
    assert steps.index(wheel) < steps.index(bundle) < steps.index(installer)
    assert steps.index(export) < steps.index(bundle)
    size = named_step(job, "Check compressed dmg size")
    (upload,) = action_steps(job, "actions/upload-artifact")
    assert steps.index(installer) < steps.index(size) < steps.index(upload)
    assert action_steps(job, "astral-sh/setup-uv")
    for row in rows:
        assert row["target"].endswith(row["arch"])
        assert row["target"].startswith(
            "darwin-" if row["platform"] == "mac" else "linux-"
        )


@pytest.mark.parametrize("name", ["release", "ci"])
def test_dmg_size_gate_rejects_250_mb_and_requires_artifact(
    name: str, tmp_path: Path
) -> None:
    command = named_step(
        workflow(name)["jobs"]["build-desktop"], "Check compressed dmg size"
    )["run"]
    artifacts = tmp_path / "dist"
    artifacts.mkdir()
    result = run_command(command, tmp_path)
    assert result.returncode != 0
    dmg = artifacts / "specvania-0.3.0-arm64.dmg"
    dmg.write_bytes(b"small offline fixture")
    assert run_command(command, tmp_path).returncode == 0
    with dmg.open("r+b") as stream:
        stream.truncate(250_000_000)
    result = run_command(command, tmp_path)
    assert result.returncode != 0
    assert "250 MB" in result.stderr
