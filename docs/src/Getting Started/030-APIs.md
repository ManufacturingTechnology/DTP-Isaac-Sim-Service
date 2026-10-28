---
title: 'APIs'
---

# Introduction

Multiple APIs are provided for interacting with the `AMT DTP Isaac Sim Service (ISS)`.
APIs enable you to remotely interact with the ISS over a network connection.

``` mermaid
flowchart LR
    subgraph Client[Client Machine]
        P[Python Application]
    end

    subgraph Server[Server Machine]
        subgraph IS[Isaac Sim]
            S["Application"]
            A["APIs"] <--> S
        end
        A2["APIs"] <--> S
    end

    P <--> A
    P <--> A2
```

However, the APIs can also be used with Isaac Sim running on the same machine.

``` mermaid
flowchart TD
    subgraph Server[Machine]
        P[Python Application]

        subgraph IS[Isaac Sim]
            S["Application"]
            A["APIs"] <--> S
        end
        A2["APIs"] <--> S
    end

    P <--> A
    P <--> A2
```

The available APIs are:

| Name | Documentation | Description |
|---|---|---|
| Isaac Sim | [http://localhost:8011/docs](http://localhost:8011/docs) | An API provided natively by Isaac Sim. |
| [Isaac Sim Extension (ISE)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) | [http://localhost:8012/api/docs](http://localhost:8012/api/docs) | An API for remotely interacting with AMT DTP Testbeds. |
| [Isaac Sim Co-Processor (ISCP)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/coprocessor) | [http://localhost:8013/api/docs](http://localhost:8013/api/docs) | Used by the [ISE](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) to extend Isaac Sim capabilities. |
| [Kinematics Service](#kinematics-service) | [http://localhost:8014/api/docs](http://localhost:8014/api/docs) | Robot kinematics without a scene: upload a robot's URDF and XRDF, then compute FK, IK, Jacobians, sampled moves, and self-collision. Started with the extension; the port is set by `kinematics_service_port` in the [configuration](050-Configure.md). |
| [Segment API](../Asset Development/050-Segments-and-Control-APIs.md#segment-api) | e.g. [http://localhost:8020/api/docs](http://localhost:8020/api/docs) | Started by a testbed for one or more [automation components](../Asset Development/040-Automation-Components.md), on the port the testbed chooses. |


## Isaac Sim Extension (ISE) API

The `AMT DTP Isaac Sim Extension (ISE)` provides an API for various remote interaction tasks.

## Isaac Sim Co-Processor (ISCP) API

The `AMT DTP Isaac Sim Co-Processor (ISCP)` is an external application bundled with the `AMT DTP Isaac Sim Extension (ISE)`.
It is used to extend Isaac Sim capabilities by providing additional functionality, such as boolean geometry operations.

Some python libraries depend on specific versions of other python libraries.
Since Isaac Sim uses outdated python libraries which are not compatible with the dependencies of other libraries, some libraries can not be installed in the Isaac Sim python environment.
To work around this issue, the ISCP is an external application which is bundled with the `AMT DTP Isaac Sim Extension (ISE)`.
The ISCP provides an API which the ISE uses for various purposes, but it can also be used by end user applications.

## Kinematics Service

The `AMT DTP Isaac Sim Extension (ISE)` also starts a **kinematics service**: a robot kinematics API, similar to
MoveIt's kinematics services, that a controller can use without a simulation. Upload a robot's URDF and XRDF to
create a solver, then compute forward and inverse kinematics, Jacobians, reachability, sampled `ptp` and `lin`
moves, and self-collision with the robot's own collision spheres. It knows nothing about the scene, so it never
plans around obstacles. Solvers stay in memory until they are deleted or the extension stops.
See [Manipulator Robots](../Asset Development/070-Manipulator-Robots.md#kinematics-service).

## Segment API

A testbed can serve a **segment API** for one or more [automation components](../Asset Development/040-Automation-Components.md)
it simulates. Clients queue frames of commands for all of the components (one frame is applied per
simulation step, to every machine at once), read or stream their state, and clear the queue. The API
documents each component's own commands and actual values, serves files such as robot descriptions, and
serves a generated Python module with typed models and a client for that API.
See [Segments and the Control APIs](../Asset Development/050-Segments-and-Control-APIs.md#the-control-apis).


# AMT DTP Isaac Sim Python Library (ISPL)

The `AMT DTP Isaac Sim Python Library (ISPL)` provides multiple clients to make interacting with the `AMT DTP Isaac Sim Service (ISS)` easier.

```python
#! pip install amt.dtp.ise.lib
from requests.exceptions import HTTPError
from amt.dtp.ise.lib.apiclient import ApiClient

client = ApiClient("http://127.0.0.1:8012/")
try:
    client.extension_ping()
    if client.version != client.extension_version():
        raise Exception("client and ISE version to not match.")
except HTTPError as e:
    print(f"Error occured communicating with the ISE API: {e}")
    raise e
except Exception as e:
    print(f"Error occurred: {e}")
    raise e
```

```python
#! pip install amt.dtp.ise.lib
import requests
from amt.dtp.ise.lib.coprocessorclient import CoProcessorClient

client = CoProcessorClient("http://127.0.0.1:8013/")
try:
    client.application_ping()
    if client.version != client.application_version():
        raise Exception("client and ISE version to not match.")
except HTTPError as e:
    print(f"Error occured communicating with the Co-Processor API: {e}")
    raise e
except Exception as e:
    print(f"Error occurred: {e}")
    raise e
```
