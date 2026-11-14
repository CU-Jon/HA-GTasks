"""Check actual Marketplace schemas and prevent source/test data leaking into ZIPs."""

import json
from pathlib import Path
from zipfile import ZipFile

from homeassistant.components.marketplace.utils.validate import (
    INTEGRATION_MANIFEST_VALUES,
    REPOSITORY_MANIFEST_VALUES,
    VALID_DOMAIN,
)

from scripts import package


def test_marketplace_metadata_and_archive():
    root = Path(__file__).resolve().parents[1]
    hacs = json.loads((root / "hacs.json").read_text())
    manifest = json.loads((root / "custom_components/gtasks/manifest.json").read_text())
    for key, value in hacs.items():
        assert REPOSITORY_MANIFEST_VALUES[key](value) == value
    for key, validator in INTEGRATION_MANIFEST_VALUES.items():
        assert validator(manifest[key]) == manifest[key]
    assert VALID_DOMAIN.fullmatch(manifest["domain"])
    assert hacs["homeassistant"] == "2026.11.0"
    package.main()
    with ZipFile(root / "dist/gtasks.zip") as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert all(name.startswith("custom_components/gtasks/") for name in names)
        assert all(name.endswith((".py", ".json", ".png", "/LICENSE", "/NOTICE")) for name in names)
        assert not any(
            part in name
            for name in names
            for part in ("tests/", "__pycache__", ".local/", "credentials.json", ".har", ".apk")
        )
        assert "custom_components/gtasks/brand/icon.png" in names
        assert "custom_components/gtasks/brand/icon@2x.png" in names
