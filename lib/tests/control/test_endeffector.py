"""End-effector packages (ADR-0012, Specs/end-effector-package.md): the XRDF fragment model (v0.1), the merge rules
(prefixes, the mount pose from the composition, frames, spheres into the collision geometry, generated
cross-boundary ignores, tool-frame order, stacking), the validation errors, and the deterministic written-out XRDF.
Loading a merged XRDF in cuMotion, TCP FK vs USD Sites, and self-collision at the default pose are checked offline by
docs/src/Agents/Scratch/harnesses/upper_layer_harness.py."""
import json

import pytest

from amt.dtp.ise.lib.control.motion.endeffector import (
    Combination,
    FragmentError,
    load_combinations,
    load_fragment,
    merge_xrdf,
    render_xrdf,
    write_merged_xrdf,
)

ROBOT = {
    "format": "xrdf", "format_version": 2.0,
    "cspace": {"joint_names": ["j1", "j2"], "acceleration_limits": [10, 10], "jerk_limits": [100, 100]},
    "modifiers": [{"add_frame": {"frame_name": "tool0", "parent_frame_name": "link6", "joint_name": "tool0_joint",
                                 "joint_type": "FIXED", "fixed_transform": {"position": [0, 0, 0.06],
                                                                            "orientation": {"w": 1, "xyz": [0, 0, 0]}}}}],
    "tool_frames": ["tool0"],
    "world_collision": {"geometry": "spheres"},
    "self_collision": {"geometry": "spheres", "ignore": {"link5": ["link6"]}},
    "geometry": {"spheres": {"spheres": {"link6": [{"center": [0, 0, 0], "radius": 0.04}]}}},
}

GRIPPER = {
    "format": "xrdf-fragment", "format_version": 0.1, "name": "G1", "mount_frame": "mount",
    "frames": [{"frame_name": "tcp", "parent_frame_name": "mount",
                "fixed_transform": {"position": [0, 0, 0.1], "orientation": {"w": 1, "xyz": [0, 0, 0]}}},
               {"frame_name": "tip", "parent_frame_name": "tcp", "fixed_transform": {"position": [0, 0, 0.02]}}],
    "tool_frames": ["tcp"],
    "collision": {"spheres": {"mount": [{"center": [0, 0, 0.02], "radius": 0.03}],
                              "tcp": [{"center": [0, 0, -0.02], "radius": 0.015}]},
                  "buffer_distance": {"mount": 0.005}},
    "self_collision": {"ignore": {"mount": ["tcp"]}},
}

CHANGER = {"format": "xrdf-fragment", "format_version": "0.1", "name": "TC1", "mount_frame": "mount",
           "frames": [{"frame_name": "clamp", "parent_frame_name": "mount", "fixed_transform": {"position": [0, 0, 0.03]}}],
           "tool_frames": [], "collision": {"spheres": {"mount": [{"center": [0, 0, 0.015], "radius": 0.035}]}}}


def frames_of(xrdf):
    return {m["add_frame"]["frame_name"]: m["add_frame"] for m in xrdf["modifiers"]}


def test_single_fragment_merge():
    fragments = {"G1": load_fragment(GRIPPER)}
    merged = merge_xrdf(ROBOT, fragments, Combination(stack=[{"fragment": "G1", "prefix": "g1", "parent": "tool0",
                                                              "offset": {"position": [0, 0, 0.01]}}],
                                                      active_tcp="g1/tcp"))
    added = frames_of(merged)
    assert added["g1/mount"]["parent_frame_name"] == "tool0"
    assert added["g1/mount"]["fixed_transform"]["position"] == [0.0, 0.0, 0.01]  # the mount pose from the composition
    assert added["g1/tcp"]["parent_frame_name"] == "g1/mount" and added["g1/tip"]["parent_frame_name"] == "g1/tcp"
    assert added["g1/tcp"]["joint_type"] == "FIXED" and added["g1/tcp"]["joint_name"] == "g1/tcp_joint"
    assert merged["tool_frames"] == ["g1/tcp", "tool0"]  # the active TCP first
    spheres = merged["geometry"]["spheres"]["spheres"]
    assert set(spheres) == {"link6", "g1/mount", "g1/tcp"}  # one geometry group
    ignore = merged["self_collision"]["ignore"]
    assert ignore["link5"] == ["link6"]  # the robot's own
    assert "g1/tcp" in ignore["g1/mount"]  # the fragment's own
    assert "tool0" in ignore["g1/mount"]  # mount <-> its parent frame
    assert {"g1/mount", "g1/tcp"} <= set(ignore["link6"]) and {"g1/mount", "g1/tcp"} <= set(ignore["tool0"])  # one hop
    assert "g1/tip" not in ignore["link6"]
    assert merged["self_collision"]["buffer_distance"] == {"g1/mount": 0.005}
    assert ROBOT["tool_frames"] == ["tool0"]  # the input is not changed


def test_stacking_and_a_clone_geometry():
    robot = json.loads(json.dumps(ROBOT))
    robot["self_collision"]["geometry"] = "self"  # a group that clones the world group plus its own
    robot["geometry"]["self_only"] = {"spheres": {"link1": [{"center": [0, 0, 0], "radius": 0.1}]}}
    robot["geometry"]["self"] = {"clone": ["spheres", "self_only"]}
    fragments = {"G1": load_fragment(GRIPPER), "TC1": load_fragment(CHANGER)}
    combination = Combination(stack=[{"fragment": "TC1", "prefix": "tc", "parent": "tool0"},
                                     {"fragment": "G1", "prefix": "g1", "parent": "tc/clamp",
                                      "offset": {"position": [0, 0, 0.02]}}], active_tcp="g1/tcp")
    merged = merge_xrdf(robot, fragments, combination)
    added = frames_of(merged)
    assert added["g1/mount"]["parent_frame_name"] == "tc/clamp"
    assert "tc/mount" in merged["geometry"]["spheres"]["spheres"]
    assert "self_end_effectors" not in merged["geometry"]  # the clone already includes the world group
    ignore = merged["self_collision"]["ignore"]
    assert "tc/clamp" in ignore["g1/mount"] and "g1/mount" in ignore["link6"]  # the clamp's link is link6
    assert merged["tool_frames"] == ["g1/tcp", "tool0"]
    # deterministic output with a header
    text = render_xrdf(merged, ["header"])
    assert text == render_xrdf(merge_xrdf(robot, fragments, combination), ["header"]) and text.startswith("# header\n")
    assert '"g1/tcp":' in text or "g1/tcp" in text


def test_robot_without_spheres_gets_a_group():
    robot = {k: v for k, v in ROBOT.items() if k not in ("geometry", "world_collision", "self_collision")}
    merged = merge_xrdf(robot, {"G1": load_fragment(GRIPPER)}, Combination(
        stack=[{"fragment": "G1", "prefix": "g1", "parent": "tool0"}]))
    assert merged["world_collision"]["geometry"] == merged["self_collision"]["geometry"] == "collision_spheres"
    assert set(merged["geometry"]["collision_spheres"]["spheres"]) == {"g1/mount", "g1/tcp"}
    assert merged["tool_frames"] == ["tool0", "g1/tcp"]  # no active TCP: the robot's first


@pytest.mark.parametrize("change,expected", [
    (lambda f: f["frames"][0].update(parent_frame_name="nowhere"), "not the mount frame or an earlier frame"),
    (lambda f: f["tool_frames"].append("ghost"), "tool_frames names 'ghost'"),
    (lambda f: f["frames"].append(dict(f["frames"][0])), "defined twice"),
])
def test_fragment_errors(change, expected):
    fragment = json.loads(json.dumps(GRIPPER))
    change(fragment)
    with pytest.raises(FragmentError) as caught:
        load_fragment(fragment)
    assert expected in str(caught.value)


def test_merge_errors():
    fragments = {"G1": load_fragment(GRIPPER)}
    for stack, active, expected in [
        ([{"fragment": "G2", "prefix": "g1", "parent": "tool0"}], None, "unknown fragment"),
        ([{"fragment": "G1", "prefix": "g1", "parent": "tc/clamp"}], None, "is not a robot frame"),
        ([{"fragment": "G1", "prefix": "g1", "parent": "tool0"}, {"fragment": "G1", "prefix": "g1", "parent": "g1/tcp"}],
         None, "used twice"),
        ([{"fragment": "G1", "prefix": "g1", "parent": "tool0"}], "g1/nope", "active_tcp"),
    ]:
        with pytest.raises(FragmentError) as caught:
            merge_xrdf(ROBOT, fragments, Combination(stack=stack, active_tcp=active))
        assert expected in str(caught.value)
    with pytest.raises(FragmentError):  # robot frames known: an unknown first parent is caught too
        merge_xrdf(ROBOT, fragments, Combination(stack=[{"fragment": "G1", "prefix": "g1", "parent": "elbow"}]),
                   robot_frames=["link5", "link6"])
    with pytest.raises(Exception):
        Combination(stack=[{"fragment": "G1", "prefix": "bad prefix", "parent": "tool0"}])
    with pytest.raises(FragmentError):
        load_fragment({**GRIPPER, "format_version": "0.2"})


def test_written_out_and_cached(tmp_path):
    merged = merge_xrdf(ROBOT, {"G1": load_fragment(GRIPPER)},
                        Combination(stack=[{"fragment": "G1", "prefix": "g1", "parent": "tool0"}]))
    path = write_merged_xrdf(tmp_path / "cache", "AMT MR-A1", "gripper", merged,
                             sources=[("AMT MR-A1.xrdf", "robot"), ("G1.fragment.yaml", "fragment")])
    assert path.name == "AMT MR-A1__gripper.xrdf"
    text = path.read_text()
    assert "sha256" in text and "AMT MR-A1.xrdf" in text
    stamp = path.stat().st_mtime_ns
    write_merged_xrdf(tmp_path / "cache", "AMT MR-A1", "gripper", merged,
                      sources=[("AMT MR-A1.xrdf", "robot"), ("G1.fragment.yaml", "fragment")])
    assert path.stat().st_mtime_ns == stamp  # unchanged: not rewritten
    combos = load_combinations(json.dumps({"robot": "AMT MR-A1", "combinations": {
        "gripper": {"stack": [{"fragment": "G1", "prefix": "g1", "parent": "tool0"}], "active_tcp": "g1/tcp"}}}))
    assert combos.combinations["gripper"].active_tcp == "g1/tcp" and combos.separator == "/"


def test_yaml_text_when_pyyaml_is_available():
    yaml = pytest.importorskip("yaml")
    fragment = load_fragment(yaml.safe_dump(GRIPPER))
    assert fragment.name == "G1" and fragment.frame_names == ["mount", "tcp", "tip"]
    merged = merge_xrdf(ROBOT, {"G1": fragment}, Combination(stack=[{"fragment": "G1", "prefix": "g1", "parent": "tool0"}]))
    assert yaml.safe_load(render_xrdf(merged, ["x"])) == json.loads(json.dumps(merged))  # the emitter round-trips
