---
title: 'Terminology'
---

# Terminology

The terms used for machine control in this project, and how they relate to the vocabularies of industrial
control (EtherCAT, Sercos, CiA 402, PROFINET), industrial information models (OPC UA FX, ISA-88/95), and
Industrie 4.0.

## Decided Terms

| Term | Meaning | Status |
|---|---|---|
| **Segment** | One synchronization domain: a set of devices that exchange process data in one frame per cycle (one physics step), with one writer per device per cycle. Named after EtherCAT's segment, in which "the EtherCAT MainDevice is the only node within a segment allowed to actively send" a frame ([ETG](https://www.ethercat.org/en/technology.html)). | Decided, implemented |
| **Segment engine** and **SegmentApi** | The engine (`SegmentEngine`) holds the devices, the frame queue, and the process image, and runs `step()`; the API is its HTTP interface. | Decided, implemented |
| **Process data** | Cyclic data, exchanged every cycle in a fixed layout: what is written to actuators and what is read back. The fieldbus word: EtherCAT's "cyclic process data structure" ([ETG](https://www.ethercat.org/en/technology.html)). Used instead of "telemetry", which only means the measurement direction (the command direction is "telecommand"; [ECSS-S-ST-00-01C](https://ecss.nl/wp-content/uploads/2023/10/ECSS-S-ST-00-01C_Rev.1%2811October2023%29.pdf)). | Decided |
| **Configuration** | Acyclic data: parameters read and written on request, beside the cyclic exchange. Optional per transducer. | Decided, implemented |
| **Transducer** | A group of I/O points (a `Sensor`, an `Actuator`, or both) attached to a prim, e.g. an articulation's joints or a conveyor. | Decided, implemented |
| **Frame** | One row of process data for every device in a segment: a request frame (written) or a response frame (state after a step). | Decided, implemented |
| **KinematicsSolver** | Computes forward and inverse kinematics (batched, in radians and meters inside). Not a planner: a motion planner *uses* a solver. | Decided, implemented (cuMotion backend) |
| **Kinematics service** | The web API over kinematics solvers created from uploaded URDF and XRDF files (`KinematicsApi`, `/api/kinematics/...`, port 8014): forward and inverse kinematics, Jacobians, sampled moves, tool frames, self-collision. Independent of the segment and of the scene: it never plans around obstacles. | Decided, implemented |
| **RobotAPI** | TCP-level robot control on a segment (`RobotApi`, `/api/robot/...`): robot commands (`set`, `ptp`, `lin` moves) are compiled into joint frames when written, all or nothing; `plan` moves avoid obstacles. | Decided, implemented |
| **Robot command** | One row of a robot program: a move per robot, commands for other components, a dwell. It compiles to one or more frames. | Decided, implemented |
| **AutomationComponent** | Owns a set of transducers exclusively and takes part in one segment (see below). | Decided, implemented |
| **CoreApi** | The machine-control core API: identity, assets, the API layers it composes (e.g. a segment), and description files (later discovery endpoints). Like every API, it runs its server on its own thread. | Decided, implemented |
| **Command / target / actual** | Values written each cycle / a joint's commanded field / values read back (see below). | Decided, implemented |

## Values: Command, Target, Actual

- **Command:** what a client writes for an automation component each cycle (process data out), e.g. a frame's
  `components`. "Command" is the IEC control term for a value introduced from outside that the plant should
  follow ([IEC 60050-351-48-09](https://www.electropedia.org/iev/iev.nsf/display?openform&ievref=351-48-09)),
  and the Sercos term for cyclic controller-to-drive values.
- **Target:** the value a joint command sets, as in CiA 402 ("target position") and USD drive attributes
  (`targetPosition`). A joint command's fields are named `position` and `velocity`, like the actual values:
  being in a command already makes them targets.
- **Actual:** what is read back (process data in): the measured values.
- **Reference:** what actually reaches a drive's control loop after interpolation or offsets; only relevant
  once a drive-side interpolator or a custom low-level controller exists.
- **Setpoint:** reserved for held desired values, such as a temperature or a configuration parameter.
- **State:** a component's state in a response frame: its actual values, the readback of its commands, its
  status, and its errors.

Names in code:

| Concept | Transducer | Automation component |
|---|---|---|
| Write commands | `actuator_write(value)` | `actuator_write(value)` |
| Read back the commanded targets | `actuator_read()` | `actuator_read()` |
| Read measured (actual) values | `sensor_read()` | `sensor_read()` |
| Configuration | `config_read()`, `config_write(value)` | `config_read()`, `config_write(value)` |
| Models | `command_model_type`, `actual_model_type`, `config_model_type` | `command_model`, `actual_model`, `config_model` |

Methods use the physical nouns (the `Sensor` and `Actuator` classes stay); models use the value names.

## Cross-Walk

How the roles in this project map to other vocabularies. Rows marked *proposed* use proposed names.

| This project | EtherCAT | Sercos III | CiA 402 | PROFINET | OPC UA FX | ISA-88 / ISA-95 | Industrie 4.0 / MTP |
|---|---|---|---|---|---|---|---|
| Controller (client program) | MainDevice application (PLC task) | master control | host controller | IO-Controller | Controller | control system | orchestration (POL) |
| Segment (sync domain) | segment | ring/line network, one master | network | IO system (per AR, not one frame) | Connections | — | — |
| Frame (request / response) | frame, process image | MDT / AT | RPDO / TPDO | cyclic IO data | cyclic data of a Connection | — | — |
| AutomationComponent | SubDevice | slave | device (drive) | IO-Device | AutomationComponent | Equipment Module / Unit; Work Unit | I4.0 component; PEA |
| Transducer | MDP module, PDO entries | profile parameter set | axis | (sub)module | FunctionalEntity (+ Asset) | Control Module | submodel aspect |
| Commands (process data written) | outputs (RxPDO) | command values (MDT) | target values (RPDO) | outputs | output data | setpoint (SP) ❓ | — |
| Actual values (process data read) | inputs (TxPDO) | feedback values (AT) | actual values (TPDO) | inputs | input data | process variable (PV) ❓ | — |
| Configuration | CoE object dictionary via SDO | parameters (IDNs) via service channel | object dictionary via SDO | record data | configuration data | — | AAS submodel (static) |
| Device description (schema, `models.py`, URDF) | ESI | device description ❓ | EDS ❓ | GSD | (information model) | — | AAS |
| RobotAPI | — | — | — | — | FunctionalEntity | phase-like | skill |
| Kinematics service (KinematicsSolver) | — | — | — | — | Compute (Controller-to-Compute) | — | service |

Sources for this table:
- [ETG technology](https://www.ethercat.org/en/technology.html), [ETG poster](https://www.ethercat.org/download/documents/EtherCAT_Device_Protocol_Poster.pdf)
- Sercos III brochure (2016); [CiA 402](https://www.can-cia.org/can-knowledge/cia-402-series-canopen-device-profile-for-drives-and-motion-control)
- [OPC 30140 §4.1 (PROFINET)](https://reference.opcfoundation.org/specs/OPC-30140/4.1); [OPC 10000-80 §3.1](https://reference.opcfoundation.org/UAFX/Part80/v100/docs/3.1)
- [IOF (ISA-88/95 terms)](https://spec.industrialontologies.org/portal/release/202601/biopharma/ControlModule.html) ❓ for the attribution to ISA
- [AAS](https://industrialdigitaltwin.org/wp-content/uploads/2025/08/IDTA-01001-3-1-1_AAS-Specification_Part1_Metamodel.pdf), [capabilities and skills](https://arxiv.org/pdf/2209.09632), [MTP (NAMUR/ZVEI)](https://www.spiratec.com/wp-content/uploads/NAMUR_ZVEI_Positionpaper_Process_Orchestration.pdf)
