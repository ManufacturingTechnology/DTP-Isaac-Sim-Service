from amt.dtp.ise.lib.package_cleanup import fix_script_path_backslashes
from amt.dtp.ise.lib.validation import check_package_paths

# Raw .usda text: the JSON backslash is escaped again inside the single-quoted USD string, so one path
# separator appears as four backslashes in the file.
LAYER = """#usda 1.0

def Xform "Cell" (
    customData = {
        dictionary amt = {
            dictionary dtp = {
                dictionary behavior = {
                    string data = '{"script_path":"..\\\\\\\\_global\\\\\\\\disposer.py"}'
                }
            }
        }
    }
)
{
    def Xform "Other" (
        customData = {
            dictionary amt = {
                dictionary dtp = {
                    dictionary behavior = {
                        string data = '{"script_path": "_global/ok.py"}'
                    }
                }
            }
        }
    )
    {
    }
}
"""


def _package(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "_global").mkdir(parents=True)
    (pkg / "sub").mkdir()
    (pkg / "_global" / "disposer.py").write_text("")
    (pkg / "_global" / "ok.py").write_text("")
    layer = pkg / "sub" / "cell.usda"
    layer.write_bytes(LAYER.replace("\n", "\r\n").encode("utf-8"))
    (pkg / "main.usda").write_text('#usda 1.0\n(\n    subLayers = [@./sub/cell.usda@]\n)\n')
    return pkg, layer


def test_dry_run_reports_without_changing(tmp_path):
    pkg, layer = _package(tmp_path)
    before = layer.read_bytes()
    fixes = fix_script_path_backslashes(pkg)
    assert [(f.file, f.old, f.new, f.applied) for f in fixes] == [
        ("sub/cell.usda", "..\\_global\\disposer.py", "../_global/disposer.py", False)]
    assert layer.read_bytes() == before


def test_apply_fixes_only_the_value(tmp_path):
    pkg, layer = _package(tmp_path)
    assert any(i.rule == "SCRIPT-PATH-BACKSLASH" for i in check_package_paths(pkg, use_pxr=False))
    before = layer.read_bytes()
    fixes = fix_script_path_backslashes(pkg, apply=True)
    after = layer.read_bytes()
    assert len(fixes) == 1 and fixes[0].applied
    assert b'"script_path":"../_global/disposer.py"' in after
    assert b"\r\n" in after and after.count(b"\r\n") == before.count(b"\r\n")  # newline style kept
    assert len(after) == len(before) - 6  # only the two separators changed (4 chars -> 1 each)
    assert not any(i.rule == "SCRIPT-PATH-BACKSLASH" for i in check_package_paths(pkg, use_pxr=False))
    assert fix_script_path_backslashes(pkg) == []  # idempotent


def test_validation_never_changes_files(tmp_path):
    pkg, layer = _package(tmp_path)
    before = {p: p.read_bytes() for p in pkg.rglob("*") if p.is_file()}
    check_package_paths(pkg, use_pxr=False)
    assert {p: p.read_bytes() for p in pkg.rglob("*") if p.is_file()} == before
