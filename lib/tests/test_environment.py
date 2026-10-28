"""Tests for amt.dtp.ise.lib.environment: validate_directory, archive and extract enforce the package
rules (ADR-0020, see the package-validation spec). Synthetic fixtures only;
absolute paths are deliberately not user folders."""
import shutil
import textwrap
import zipfile

import pytest

from amt.dtp.ise.lib import apiclient
from amt.dtp.ise.lib.environment import (PackageValidationError, archive, extract, get_environment_usd_path,
                                         validate_directory)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "env"
    write(root / "assets" / "robot.usda", "#usda 1.0\n")
    write(root / "main.usda", '#usda 1.0\ndef "W" (prepend payload = @./assets/robot.usda@) {}\n')
    return root


def test_valid_environment(env):
    assert validate_directory(env) is None
    assert get_environment_usd_path(env) == env / "main.usda"


def test_error_fails_and_lists_each_issue(env):
    write(env / "main.usda", '#usda 1.0\ndef "W" (\n    prepend payload = @D:/assets/robot.usda@\n) {}\n')
    errors = validate_directory(env)
    assert errors is not None and len(errors) == 1
    (e,) = errors
    # rule id, file relative to the package root, line, the stored path, and the fix
    assert "main.usda:3:" in e and "PATH-ABSOLUTE" in e and "[D:/assets/robot.usda]" in e and "; fix:" in e
    assert str(env) not in e


def test_warnings_and_info_do_not_fail(env):
    write(env / "main.usda", """
        #usda 1.0
        def "W" (
            prepend payload = @./assets/robot.usda@
        )
        {
            asset missing = @./assets/missing.usda@
            asset mdl = @OmniPBR.mdl@
            asset remote = @omniverse://host/x.usd@
            string note = "D:/notes/notes.txt"
        }
    """)
    assert validate_directory(env) is None


def test_delete_list_op_absolute_path_does_not_fail(env):
    write(env / "main.usda", """
        #usda 1.0
        def "W" (
            delete payload = [@file:///srv/old/old.usd@, @/srv/old/x.usda@]
            prepend payload = @./assets/robot.usda@
        )
        {
        }
    """)
    assert validate_directory(env) is None


def test_stem_duplicate_fails(env):
    write(env / "main.usd", "#usda 1.0\n")
    (e,) = validate_directory(env)
    assert "USD-STEM-DUPLICATE" in e and "main.usd, main.usda" in e and "ambiguous" in e


@pytest.mark.parametrize("ext", [".usd", ".usda", ".usdc", ".usdz", ".USDZ"])
def test_entry_accepts_every_usd_extension(tmp_path, ext):
    root = tmp_path / "env"
    root.mkdir()
    # Placeholder content: the entry check only looks at the name, and .usdz layers are not parsed
    # (LAYER-NOT-CHECKED, info); a text .usd/.usdc is scanned like .usda.
    (root / f"main{ext}").write_bytes(b"PK\x03\x04" if ext.lower() == ".usdz" else b"#usda 1.0\n")
    assert validate_directory(root) is None
    assert get_environment_usd_path(root) == root / f"main{ext}"


def test_missing_entry_fails(tmp_path):
    root = tmp_path / "env"
    write(root / "scene.usda", "#usda 1.0\n")
    (e,) = validate_directory(root)
    assert "main.usd, main.usda, main.usdc, main.usdz" in e


def test_archive_and_extract_raise_with_the_issue_list(env, tmp_path):
    path = archive(env)
    try:
        out = extract(path)
        assert (out / "main.usda").is_file()
        shutil.rmtree(out.parent)
    finally:
        path.unlink()

    write(env / "main.usda", '#usda 1.0\n(subLayers = [@/srv/x.usda@])\n')
    with pytest.raises(PackageValidationError) as ei:
        archive(env)
    assert len(ei.value.errors) == 1 and "PATH-ABSOLUTE" in str(ei.value)

    # A zip built elsewhere is validated on extract.
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.write(env / "main.usda", "main.usda")
    with pytest.raises(PackageValidationError) as ei:
        extract(bad)
    assert "main.usda:2: error PATH-ABSOLUTE" in ei.value.errors[0]


def test_client_reports_server_validation_errors():
    import requests

    response = requests.Response()
    response.status_code = 422
    response._content = (b'{"detail": {"message": "Environment env is not valid", '
                         b'"errors": ["main.usda:2: error PATH-ABSOLUTE: path is absolute [/srv/x.usda]"]}}')
    with pytest.raises(requests.HTTPError) as ei:
        apiclient._raise_for_upload_status(response)
    assert "Environment env is not valid" in str(ei.value) and "PATH-ABSOLUTE" in str(ei.value)
