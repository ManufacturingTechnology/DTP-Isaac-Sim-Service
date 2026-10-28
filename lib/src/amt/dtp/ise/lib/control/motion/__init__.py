"""The motion layer: the solver interface (:mod:`.kinematics`), interpolation and time
parameterization (:mod:`.trajectory`), the motion planner interface and library-neutral obstacles
(:mod:`.planning`), end-effector XRDF fragments and their merge (:mod:`.endeffector`), and the kinematics service
(:mod:`.registry`, :mod:`.server`, :mod:`.client`; models in :mod:`.models` and :mod:`.trajectory_models`): a web API
over solvers created from uploaded URDF + XRDF files, independent of Isaac Sim's scene and of segments. Design:
``docs/src/Agents/Specs/robot-control.md``."""
