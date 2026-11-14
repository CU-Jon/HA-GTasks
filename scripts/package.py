"""Package only the integration and its license into an installable archive."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "custom_components/gtasks"
    output = root / "dist/gtasks.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and (
                path.suffix in (".py", ".json", ".png") or path.name in ("LICENSE", "NOTICE")
            ):
                archive.write(path, path.relative_to(root).as_posix())
        for name in ("LICENSE", "NOTICE"):
            archive.write(root / name, f"custom_components/gtasks/{name}")
    digest = sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"Created {output.name}; SHA-256: {digest}")


if __name__ == "__main__":
    main()
