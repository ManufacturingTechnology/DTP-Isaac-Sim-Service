"""Tests for amt.dtp.ise.lib.validation (package path rules). Synthetic fixtures only.

Absolute paths in the fixtures are deliberately not user folders (D:/assets, /srv, /opt, a UNC
share), so this file stays clean under the repo's privacy grep."""
import json
import textwrap

import pytest

from amt.dtp.ise.lib import validation
from amt.dtp.ise.lib.validation import Severity, check_package_paths, has_errors, main


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


def behavior_prim(name, script_json, key=("amt", "dtp", "behavior", "data")):
    """A prim whose customData holds ``script_json`` (already USDA-quoted) at the nested ``key``."""
    *dicts, leaf = key
    opening = "".join(f"dictionary {d} = {{\n" for d in dicts)
    closing = "}\n" * len(dicts)
    return f'def "{name}" (\n customData = {{\n{opening} string {leaf} = {script_json}\n{closing} }}\n)\n{{\n}}\n'


@pytest.fixture
def pkg(tmp_path):
    root = tmp_path / "pkg"
    write(root / "assets" / "robot" / "robot.usda", "#usda 1.0\n")
    write(root / "assets" / "_global" / "spawner.py", "")
    write(root / "assets" / "conveyor" / "conveyor.py", "")
    return root


def test_clean_package_has_no_issues(pkg):
    write(pkg / "main.usda", """
        #usda 1.0
        (
            subLayers = [@./assets/robot/robot.usda@]
        )
        def Xform "World" (
            prepend payload = @./assets/robot/robot.usda@</Robot>
        )
        {
        }
    """)
    assert check_package_paths(pkg) == []


def test_absolute_file_url_backslash_and_escaping(pkg):
    write(pkg / "main.usda", r"""
        #usda 1.0
        def "A" (
            prepend payload = @D:/assets/robot.usda@
            references = @file:///srv/assets/x.usda@
        )
        {
            asset a = @/opt/models/tex.png@
            asset b = @\\server\share\x.usd@
            asset c = @.\assets\robot\robot.usda@
            asset d = @../outside.usda@
            asset e = @@@./we@ird.usda@@@
            asset f = @omniverse://host/x.usd@
            asset g = @OmniPBR.mdl@
            asset h = @./assets/missing.usda@
        }
    """)
    issues = check_package_paths(pkg)
    by_path = {i.path: i.rule for i in issues}
    assert by_path["D:/assets/robot.usda"] == "PATH-ABSOLUTE"
    assert by_path["file:///srv/assets/x.usda"] == "PATH-FILE-URL"
    assert by_path["/opt/models/tex.png"] == "PATH-ABSOLUTE"
    assert by_path[r"\\server\share\x.usd"] == "PATH-ABSOLUTE"
    assert by_path[r".\assets\robot\robot.usda"] == "PATH-BACKSLASH"  # exists once slashes are fixed
    assert by_path["../outside.usda"] == "PATH-ESCAPES-ROOT"
    assert by_path["./we@ird.usda"] == "PATH-UNRESOLVED"  # @@@ delimiter parsed
    assert by_path["omniverse://host/x.usd"] == "PATH-REMOTE"
    assert by_path["OmniPBR.mdl"] == "PATH-SEARCH"
    assert by_path["./assets/missing.usda"] == "PATH-UNRESOLVED"
    # line numbers point into the file (header is line 1)
    abs_issue = next(i for i in issues if i.path == "D:/assets/robot.usda")
    assert abs_issue.line == 3 and abs_issue.file == "main.usda"
    assert abs_issue.context == "prepend payload"
    assert has_errors(issues)


def test_delete_list_op_is_info_and_not_resolved(pkg):
    # ADR-0020: non-portable paths left in delete list ops don't affect loading, so
    # they are info (PATH-DELETE-LIST-OP), not errors; deleted entries are never checked for existence.
    write(pkg / "main.usda", r"""
        #usda 1.0
        def "A" (
            delete payload = [
                @file:///srv/old/old.usd@,
                @D:/assets/old.usda@,
                @../outside.usda@,
                @.\old\x.usda@,
                @./gone.usda@
            ]
            prepend payload = @./assets/robot/robot.usda@
        )
        {
        }
    """)
    issues = check_package_paths(pkg)
    assert [(i.rule, i.path) for i in issues] == [  # ./gone.usda is deleted: not required to exist
        ("PATH-DELETE-LIST-OP", "file:///srv/old/old.usd"),
        ("PATH-DELETE-LIST-OP", "D:/assets/old.usda"),
        ("PATH-DELETE-LIST-OP", "../outside.usda"),
        ("PATH-DELETE-LIST-OP", r".\old\x.usda"),
    ]
    assert all(i.severity == Severity.INFO and i.context == "delete payload" for i in issues)
    assert issues[0].line == 4 and "PATH-FILE-URL" in issues[0].message
    assert "does not affect loading" in issues[0].message
    assert not has_errors(issues)


def test_usd_stem_duplicate_is_an_error(pkg):
    write(pkg / "main.usd", "#usda 1.0\n")
    write(pkg / "main.usda", "#usda 1.0\n")
    write(pkg / "assets" / "robot" / "Robot.USDC", "#usda 1.0\n")  # same stem as robot.usda, any case
    write(pkg / "assets" / "conveyor" / "conveyor.usda", "#usda 1.0\n")
    write(pkg / "assets" / "conveyor" / "conveyor.json", "{}")  # not a USD file: fine
    write(pkg / "assets" / "other" / "main.usda", "#usda 1.0\n")  # same stem, other folder: fine
    issues = check_package_paths(pkg)
    assert [(i.rule, i.file, i.path) for i in issues] == [
        ("USD-STEM-DUPLICATE", "assets/robot/Robot.USDC", "assets/robot/Robot.USDC, assets/robot/robot.usda"),
        ("USD-STEM-DUPLICATE", "main.usd", "main.usd, main.usda"),
    ]
    assert all(i.severity == Severity.ERROR for i in issues)
    assert "entry layer (main.*) is ambiguous" in issues[1].message
    assert "ambiguous" not in issues[0].message


def test_script_path_in_custom_data(pkg):
    # USDA single-quoted string holding JSON; \\\\ in the file -> \\ in the string -> \ in JSON.
    prims = [
        behavior_prim("ok", """'{"script_path":"conveyor.py"}'"""),
        behavior_prim("up", """'{"script_path":"../_global/spawner.py"}'"""),
        behavior_prim("bs", r"""'{"script_path":"..\\\\_global\\\\spawner.py"}'"""),
        behavior_prim("dq", r'''"{\"script_path\": \"/srv/scripts/x.py\"}"'''),
        behavior_prim("gone", """'{"script_path":"missing.py"}'"""),
        behavior_prim("out", """'{"script_path":"../../../x.py"}'"""),
        behavior_prim("bad", """'script_path is not json'"""),
    ]
    write(pkg / "assets" / "conveyor" / "conveyor.usda", "#usda 1.0\n" + "".join(prims))
    issues = check_package_paths(pkg)
    found = {(i.rule, i.path) for i in issues}
    assert found == {
        ("SCRIPT-PATH-BACKSLASH", r"..\_global\spawner.py"),
        ("SCRIPT-PATH-ABSOLUTE", "/srv/scripts/x.py"),
        ("SCRIPT-PATH-UNRESOLVED", "missing.py"),
        ("SCRIPT-PATH-ESCAPES-ROOT", "../../../x.py"),
        ("SCRIPT-PATH-JSON", None),
    }
    assert all(i.file == "assets/conveyor/conveyor.usda" for i in issues)
    assert all(i.context == "amt:dtp:behavior:data" for i in issues)


def test_script_path_under_legacy_key_is_info_and_form_only(pkg):
    write(pkg / "assets" / "conveyor" / "conveyor.usda", "#usda 1.0\n"
          + behavior_prim("legacy", """'{"script_path": "conveyor_auto.py"}'""", key=("amt", "behavior", "script"))
          + behavior_prim("legacy_abs", """'{"script_path": "D:/scripts/x.py"}'""", key=("amt", "behavior", "script")))
    issues = check_package_paths(pkg)
    assert sorted((i.rule, i.path) for i in issues) == [
        ("SCRIPT-PATH-ABSOLUTE", "D:/scripts/x.py"),
        ("SCRIPT-PATH-LEGACY-KEY", "D:/scripts/x.py"),
        ("SCRIPT-PATH-LEGACY-KEY", "conveyor_auto.py"),  # missing file not reported: legacy data
    ]
    assert {i.context for i in issues} == {"amt:behavior:script"}


def test_script_path_relative_to_composed_layer(pkg):
    # An override in the testbed root on a prim that comes from a payloaded asset: at runtime the
    # script resolves next to the payloaded file (assets/conveyor/), not next to main.usda.
    write(pkg / "assets" / "conveyor" / "conveyor.usda", "#usda 1.0\n")
    write(pkg / "assets" / "cell.usda", '#usda 1.0\ndef "C" (prepend payload = @./conveyor/conveyor.usda@) {}\n')
    write(pkg / "main.usda", '#usda 1.0\ndef "W" (prepend payload = @./assets/cell.usda@) {}\n'
          + behavior_prim("S", """'{"script_path":"../_global/spawner.py"}'""")
          + behavior_prim("T", """'{"script_path":"../../nowhere.py"}'"""))
    issues = check_package_paths(pkg)
    assert [(i.rule, i.path) for i in issues] == [("SCRIPT-PATH-UNRESOLVED", "../../nowhere.py")]


def test_strings_and_comments_are_not_asset_paths(pkg):
    write(pkg / "main.usda", """
        #usda 1.0
        (
            doc = "mail me@example.com or @nobody@ in a string"
        )
        # a comment with @D:/assets/comment.usd@
        def "A" {
            string note = "D:/notes/notes.txt"
        }
    """)
    issues = check_package_paths(pkg)
    assert [i.rule for i in issues] == ["TEXT-ABSOLUTE-PATH"]
    assert issues[0].severity == Severity.WARNING


def test_urdf_mesh_paths(pkg):
    write(pkg / "robot" / "meshes" / "base.stl", "")
    write(pkg / "robot" / "robot.urdf", """
        <robot name="r">
          <link name="a"><visual><geometry><mesh filename="meshes/base.stl"/></geometry></visual></link>
          <link name="b"><visual><geometry><mesh filename="file:///opt/meshes/b.stl"/></geometry></visual></link>
          <link name="c"><visual><geometry><mesh filename="package://robot/meshes/c.stl"/></geometry></visual></link>
          <link name="d"><visual><geometry><mesh filename="meshes/missing.stl"/></geometry></visual></link>
          <link name="e"><visual><geometry><mesh filename="/opt/meshes/e.stl"/></geometry></visual></link>
        </robot>
    """)
    issues = check_package_paths(pkg)
    assert {(i.rule, i.line) for i in issues} == {
        ("PATH-FILE-URL", 3), ("PATH-PACKAGE-URL", 4), ("PATH-UNRESOLVED", 5), ("PATH-ABSOLUTE", 6)}


def test_json_yaml_text_scan(pkg):
    write(pkg / "cfg.yaml", "urdf_path: file:///srv/robot.urdf\nroot_link: /World/robot\n")
    write(pkg / "cfg.json", json.dumps({"p": "D:\\assets\\x"}))
    issues = check_package_paths(pkg)
    assert {(i.file, i.rule) for i in issues} == {
        ("cfg.yaml", "TEXT-ABSOLUTE-PATH"), ("cfg.json", "TEXT-ABSOLUTE-PATH")}


def test_text_dot_usd_is_scanned_and_binary_without_pxr_is_reported(pkg):
    write(pkg / "text.usd", "#usda 1.0\ndef \"A\" (references = @/srv/x.usd@) {}\n")
    (pkg / "bin.usdc").write_bytes(b"PXR-USDC" + b"\0" * 32)
    issues = check_package_paths(pkg, use_pxr=False)
    assert {(i.file, i.rule) for i in issues} == {
        ("text.usd", "PATH-ABSOLUTE"), ("bin.usdc", "LAYER-NOT-CHECKED")}


def test_binary_layer_with_pxr(pkg):
    Sdf = pytest.importorskip("pxr.Sdf")
    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.ImportFromString(
        '#usda 1.0\ndef "A" (\n delete payload = [@file:///srv/x.usd@]\n'
        ' prepend payload = @./assets/robot/robot.usda@\n) {}\n'
        + behavior_prim("B", """'{"script_path": "missing.py"}'"""))
    layer.Export(str(pkg / "bin.usdc"))
    assert (pkg / "bin.usdc").read_bytes().startswith(b"PXR-USDC")
    issues = check_package_paths(pkg)
    assert {(i.rule, i.line) for i in issues} == {("PATH-DELETE-LIST-OP", None), ("SCRIPT-PATH-UNRESOLVED", None)}


def test_skipped_dirs(pkg):
    write(pkg / "__pycache__" / "x.usda", "#usda 1.0\n(subLayers = [@/srv/abs.usda@])\n")
    assert check_package_paths(pkg) == []


@pytest.mark.parametrize("venv", ["venv", ".venv", "venv-linux", "venv-windows", "venv-linux-test", "venv-windows-test"])
def test_venv_dirs_are_skipped(pkg, venv):
    write(pkg / "tools" / venv / "lib" / "x.usda", "#usda 1.0\n(subLayers = [@/srv/abs.usda@])\n")
    assert check_package_paths(pkg) == []


def test_every_rule_in_catalog_has_severity():
    assert all(isinstance(sev, Severity) for sev, _ in validation.RULES.values())


def test_cli_exit_codes_and_output(pkg, capsys):
    assert main([str(pkg)]) == 0
    write(pkg / "main.usda", "#usda 1.0\n(subLayers = [@/srv/x.usda@])\n")
    assert main([str(pkg)]) == 1
    out = capsys.readouterr().out
    assert "main.usda:2: error PATH-ABSOLUTE" in out
    assert main(["--json", "--min-severity", "error", str(pkg)]) == 1
    data = json.loads(capsys.readouterr().out)
    (issues,) = data.values()
    assert issues[0]["rule"] == "PATH-ABSOLUTE" and issues[0]["file"] == "main.usda"
    assert main(["--list-rules"]) == 0
