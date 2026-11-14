"""Plan idempotent stable/development releases and publish only validated artifacts."""

import argparse
import json
import os
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

MANIFEST = Path("custom_components/gtasks/manifest.json")
STABLE_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def marker(branch, sha):
    return f"<!-- release-source:{branch}:{sha} -->"


def releases():
    pages = json.loads(
        run(
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{os.environ['GITHUB_REPOSITORY']}/releases",
        )
    )
    return [release for page in pages for release in page]


def plan(branch, base_version, build_number, sha, existing):
    """Use one stable release per manifest version and one prerelease per source commit."""
    match = STABLE_VERSION.fullmatch(base_version)
    if branch not in ("main", "develop") or not match:
        raise ValueError("Release branch must be main/develop and manifest version must be X.Y.Z.")
    prerelease = branch == "develop"
    if prerelease:
        major, minor, patch = map(int, match.groups())
        if not re.fullmatch(r"[1-9]\d*", build_number):
            raise ValueError("A positive workflow run number is required.")
        version = f"{major}.{minor}.{patch + 1}-dev.{build_number}"
        prior = next(
            (
                item
                for item in existing
                if item["prerelease"] and marker(branch, sha) in (item.get("body") or "")
            ),
            None,
        )
    else:
        version = base_version
        prior = next((item for item in existing if item["tag_name"] == f"v{version}"), None)
    if prior:
        # A rerun can resume a draft, or simply skip an already published release.
        version = prior["tag_name"].removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev\.[1-9]\d*)?", version):
        raise ValueError("Unexpected existing release version.")
    return {
        "sha": sha,
        "version": version,
        "tag": f"v{version}",
        "prerelease": str(prerelease).lower(),
        "publish": str(prior is None or prior["draft"]).lower(),
    }


def set_version(version):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["version"] = version
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def prepare():
    metadata = plan(
        os.environ["RELEASE_BRANCH"],
        json.loads(MANIFEST.read_text(encoding="utf-8"))["version"],
        os.environ["GITHUB_RUN_NUMBER"],
        run("git", "rev-parse", "HEAD"),
        releases(),
    )
    if metadata["publish"] == "true":
        set_version(metadata["version"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for key, value in metadata.items():
            output.write(f"{key}={value}\n")
    action = "validate and publish" if metadata["publish"] == "true" else "already published"
    print(f"{metadata['tag']}: {action}")


def verify_package(version):
    """Refuse missing, mismatched or damaged artifacts before creating any public tag."""
    archive = Path("dist/gtasks.zip")
    expected = archive.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0]
    if sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("Release ZIP checksum mismatch.")
    with ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("Release ZIP is damaged.")
        if json.loads(zipped.read(MANIFEST.as_posix()))["version"] != version:
            raise ValueError("Release ZIP version does not match the tag.")


def publish():
    branch, source = os.environ["RELEASE_BRANCH"], os.environ["RELEASE_SHA"]
    tag, version = os.environ["RELEASE_TAG"], os.environ["RELEASE_VERSION"]
    prerelease = os.environ["RELEASE_PRERELEASE"] == "true"
    if (
        branch not in ("main", "develop")
        or prerelease != (branch == "develop")
        or tag != f"v{version}"
        or not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev\.[1-9]\d*)?", version)
        or run("git", "rev-parse", "HEAD") != source
    ):
        raise ValueError("Release metadata does not match the checked-out source.")
    existing = next((item for item in releases() if item["tag_name"] == tag), None)
    if existing and not existing["draft"]:
        print(f"{tag} is already published; leaving it unchanged.")
        return
    if existing and marker(branch, source) not in (existing.get("body") or ""):
        raise ValueError("Existing draft belongs to a different source commit.")
    verify_package(version)
    set_version(version)
    if prerelease:
        # Only the release tag receives this commit. Never push a change to the branch.
        run("git", "add", MANIFEST.as_posix())
        run(
            "git",
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            f"Prepare {tag}",
        )
    refs = run("git", "tag", "--list", tag)
    if refs:
        # A failed publication may already have pushed the tag. Its complete tree,
        # and (for generated versions) parent must still match this validated build.
        if run("git", "rev-parse", f"{tag}^{{tree}}") != run("git", "rev-parse", "HEAD^{tree}"):
            raise ValueError("Existing tag contains different source; it will not be moved.")
        if run("git", "rev-parse", f"{tag}^" if prerelease else f"{tag}^{{commit}}") != source:
            raise ValueError("Existing tag belongs to a different source commit.")
    else:
        run("git", "tag", tag)
        run("git", "push", "origin", f"refs/tags/{tag}")
    if not existing:
        with TemporaryDirectory() as temporary:
            notes = Path(temporary) / "notes.md"
            notes.write_text(
                f"{marker(branch, source)}\n\n"
                + ("Development build for testing.\n" if prerelease else ""),
                encoding="utf-8",
            )
            run(
                "gh",
                "release",
                "create",
                tag,
                "--verify-tag",
                "--draft",
                "--title",
                tag,
                "--target",
                run("git", "rev-parse", f"{tag}^{{commit}}"),
                "--generate-notes",
                "--notes-file",
                str(notes),
                *(["--prerelease"] if prerelease else []),
            )
    run("gh", "release", "upload", tag, "dist/gtasks.zip", "dist/gtasks.zip.sha256", "--clobber")
    run(
        "gh",
        "release",
        "edit",
        tag,
        "--draft=false",
        f"--prerelease={str(prerelease).lower()}",
        f"--latest={str(not prerelease).lower()}",
    )
    print(f"Published {tag}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish"))
    arguments = parser.parse_args()
    (prepare if arguments.command == "prepare" else publish)()
