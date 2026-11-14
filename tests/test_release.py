"""Exercise release planning and publication against disposable local Git repositories."""

import json
from hashlib import sha256
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from awesomeversion import AwesomeVersion

from scripts import release


def prior(tag, *, branch="main", sha="abc", draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "body": release.marker(branch, sha),
        "draft": draft,
        "prerelease": prerelease,
    }


def test_stable_plan_skips_existing_version_even_on_a_new_commit():
    result = release.plan("main", "0.1.5", "42", "new", [prior("v0.1.5")])
    assert result["publish"] == "false"
    assert result["tag"] == "v0.1.5"
    assert result["prerelease"] == "false"


def test_develop_plan_and_duplicate_merged_pr_event():
    result = release.plan("develop", "0.1.5", "42", "abc", [])
    assert result["tag"] == "v0.1.6-dev.42"
    assert result["publish"] == "true"
    assert result["prerelease"] == "true"
    version = AwesomeVersion(result["version"])
    assert version > AwesomeVersion("0.1.5")
    assert version < AwesomeVersion("0.1.6")
    existing = [prior(result["tag"], branch="develop", prerelease=True)]
    duplicate = release.plan("develop", "0.1.5", "43", "abc", existing)
    assert duplicate["publish"] == "false"
    assert duplicate["tag"] == result["tag"]
    assert release.plan("develop", "0.1.5", "44", "different", existing)["publish"] == "true"


def test_draft_retry_reuses_its_original_version():
    existing = [prior("v0.1.6-dev.42", branch="develop", prerelease=True, draft=True)]
    result = release.plan("develop", "0.1.5", "43", "abc", existing)
    assert result["publish"] == "true"
    assert result["tag"] == "v0.1.6-dev.42"


@pytest.mark.parametrize(
    "branch,version,build",
    [("feature", "0.1.5", "42"), ("main", "not-a-version", "42"), ("develop", "0.1.5", "0")],
)
def test_invalid_release_inputs(branch, version, build):
    with pytest.raises(ValueError):
        release.plan(branch, version, build, "abc", [])


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    release.MANIFEST.parent.mkdir(parents=True)
    release.MANIFEST.write_text('{"domain": "gtasks", "version": "0.1.5"}\n')
    real_run = release.run
    real_run("git", "init", "-b", "main")
    real_run("git", "add", ".")
    real_run(
        "git",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "Initial",
    )
    source = real_run("git", "rev-parse", "HEAD")
    real_run("git", "checkout", "--detach", source)
    external = Mock(return_value="")

    def boundary(*args):
        if args[0] == "gh" or args[:2] == ("git", "push"):
            return external(*args)
        return real_run(*args)

    monkeypatch.setattr(release, "run", boundary)
    monkeypatch.setattr(release, "releases", lambda: [])
    return tmp_path, source, real_run, external


def configure_package(root, source, branch, monkeypatch):
    metadata = release.plan(branch, "0.1.5", "42", source, [])
    for key, value in {
        "RELEASE_BRANCH": branch,
        "RELEASE_SHA": source,
        "RELEASE_TAG": metadata["tag"],
        "RELEASE_VERSION": metadata["version"],
        "RELEASE_PRERELEASE": metadata["prerelease"],
    }.items():
        monkeypatch.setenv(key, value)
    archive = root / "dist/gtasks.zip"
    archive.parent.mkdir()
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(release.MANIFEST.as_posix(), json.dumps({"version": metadata["version"]}))
    archive.with_suffix(".zip.sha256").write_text(sha256(archive.read_bytes()).hexdigest())
    return metadata


@pytest.mark.parametrize("branch", ["main", "develop"])
def test_publish_tag_package_version_and_stable_selection(repository, monkeypatch, branch):
    root, source, git, external = repository
    metadata = configure_package(root, source, branch, monkeypatch)
    release.publish()
    tag = metadata["tag"]
    assert (
        json.loads(git("git", "show", f"{tag}:{release.MANIFEST.as_posix()}"))["version"]
        == (metadata["version"])
    )
    assert git("git", "rev-parse", "main") == source
    if branch == "develop":
        assert git("git", "rev-parse", f"{tag}^") == source
    else:
        assert git("git", "rev-parse", tag) == source
    commands = [call.args for call in external.call_args_list]
    assert ("git", "push", "origin", f"refs/tags/{tag}") in commands
    assert not any(command[:3] == ("git", "push", "--force") for command in commands)
    final = commands[-1]
    assert final[:4] == ("gh", "release", "edit", tag)
    assert "--draft=false" in final
    assert f"--latest={'false' if branch == 'develop' else 'true'}" in final
    assert f"--prerelease={'true' if branch == 'develop' else 'false'}" in final


def test_retry_draft_does_not_move_tag(repository, monkeypatch):
    root, source, git, external = repository
    metadata = configure_package(root, source, "develop", monkeypatch)
    release.publish()
    tag = metadata["tag"]
    original_tag = git("git", "rev-parse", tag)
    external.reset_mock()
    git("git", "checkout", "--detach", source)
    monkeypatch.setattr(
        release,
        "releases",
        lambda: [prior(tag, branch="develop", sha=source, prerelease=True, draft=True)],
    )
    release.publish()
    assert git("git", "rev-parse", tag) == original_tag
    commands = [call.args for call in external.call_args_list]
    assert all(command[:2] != ("git", "push") for command in commands)
    assert all(command[:3] != ("gh", "release", "create") for command in commands)
    assert commands[0][:3] == ("gh", "release", "upload")


@pytest.mark.parametrize("damage", ["checksum", "version", "tag"])
def test_invalid_artifacts_or_existing_tags_never_publish(repository, monkeypatch, damage):
    root, source, git, external = repository
    metadata = configure_package(root, source, "main", monkeypatch)
    if damage == "checksum":
        (root / "dist/gtasks.zip.sha256").write_text("invalid")
    elif damage == "version":
        monkeypatch.setenv("RELEASE_VERSION", "0.1.6")
        monkeypatch.setenv("RELEASE_TAG", "v0.1.6")
    else:
        release.MANIFEST.write_text('{"domain": "gtasks", "version": "0.1.4"}\n')
        git("git", "add", release.MANIFEST.as_posix())
        git(
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "Different source",
        )
        git("git", "tag", metadata["tag"])
        git("git", "checkout", "--detach", source)
    with pytest.raises(ValueError):
        release.publish()
    external.assert_not_called()


def test_published_release_is_immutable(repository, monkeypatch):
    root, source, git, external = repository
    configure_package(root, source, "main", monkeypatch)
    monkeypatch.setattr(release, "releases", lambda: [prior("v0.1.5")])
    release.publish()
    external.assert_not_called()
    assert git("git", "tag", "--list") == ""
