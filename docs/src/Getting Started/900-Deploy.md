---
title: 'Deploy'
---

# Architecture

The `AMT DTP Isaac Sim Service (ISS)` can be deployed in a container or on bare metal and functions as a `Server` which `Client`s can interact with.

``` mermaid
flowchart LR
    subgraph Client[Client Machine]
    WC[WebRTC Client]
    P[Python Application]
    end

    subgraph Server[Server Machine]
    S["Isaac Sim Application"]
    WS["WebRTC Server"] <--> S
    A["APIs"] <--> S
    end

    WC <--> WS
    P <--> A
```

There are three methods for interacting with Isaac Sim and the ISS:

| Method | Deployment Method | Local / Remote | Description |
|---|---|---|---|
| Desktop Application | Bare Metal Only | Local Only | Isaac Sim itself, providing a traditional desktop GUI application experience. |
| WebRTC Client | Container or Bare Metal | Local or Remote | A "remote desktop" client which connects to Isaac Sim running on a remote machine over a network connection, providing a similar experience to the Desktop Application method. |
| APIs | Container or Bare Metal | Local or Remote | APIs which can be utilized by networked applications. |

If you are just getting started with the ISS, a bare metal deployment, and trying all three three methods for interacting with Isaac Sim and the ISS, are recommended.

When the ISS is deployed on bare metal, both the WebRTC client and APIs are accessible through the `localhost` or `127.0.0.1` addresses, so all three methods are available.
However, when deployed in a container, while the WebRTC client and APIs are accessible, the desktop application method is not.

**Deployment Recommendations**

A key advantage of bare metal deployment during testbed, reusable asset, or `AMT DTP Isaac Sim Extension (ISE)` development is direct access to local files.
As a result, bare metal deployment provides a much smoother development and debugging experience than a container deployment.
However, containers provide a much smoother experience for production deployments. As a result, container deployments are recommended for production, while bare metal is recommended for development.

| Environment | Deployment | Operating System | Clients |
|---|---|---|---|
| Production | Container | Linux | APIs |
| Testing | Container | Linux | WebRTC & APIs |
| Development | Bare Metal | Windows or Linux | Desktop & APIs |


# Deployment

Due to Isaac Sim licensing restrictions, AMT can not distribute Isaac Sim with pre-installed extensions.

## Container

Container deployment is only supported on Linux.
Container deployment on Windows and/or Windows Subsystem for Linux (WSL) is not currently feasible or supported due to
GPU pass-through limitations on Windows.

1. [Install Docker Engine](https://docs.docker.com/engine/install/) and run the `hello-world` image to ensure that Docker is working correctly.

    ```bash
    docker run --rm hello-world
    ```

    The container output should indicate that it is working correctly:
    ```
    Hello from Docker!
    This message shows that your installation appears to be working correctly.
    ...
    ```

1. Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

1. Run the `nvidia-smi` command in the `cuda` docker image and ensure that your GPU is being recognized from inside containers.

    ```bash
    docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi
    ```

    The command output should display the name of your GPU (e.g., `NVIDIA GeForce RTX 5060 Ti`):
    ``` hl_lines="8"
    +-----------------------------------------------------------------------------------------+
    | NVIDIA-SMI 590.57                 Driver Version: 591.86         CUDA Version: 13.1     |
    +-----------------------------------------+------------------------+----------------------+
    | GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
    | Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
    |                                         |                        |               MIG M. |
    |=========================================+========================+======================|
    |   0  NVIDIA GeForce RTX 5060 Ti     On  |   00000000:01:00.0  On |                  N/A |
    |  0%   36C    P1             50W /  180W |    4389MiB /  16311MiB |      0%      Default |
    |                                         |                        |                  N/A |
    +-----------------------------------------+------------------------+----------------------+
    ```

1. Clone the AMT DTP ISM repository.

    ```bash
    git clone https://github.com/manufacturingtechnology/DTP-Isaac-Sim-Service.git
    ```

1. Start the container.

    ```bash
    cd docker
    make
    docker compose up -d
    ```

1. If errors occur, you can check the logs to aid in diagnosing issues by running:

    ```bash
    docker logs amt-dtp-ise
    ```

## Bare Metal

Bare metal deployment is supported on Linux and Windows.

1. Download and extract [Isaac Sim 5.1.0](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/download.html) (e.g., `isaac-sim/`).
1. Download the [latest ISE release](https://github.com/manufacturingtechnology/DTP-Isaac-Sim-Service/releases/latest) (`amt.dtp.ise-x.x.x.zip`).
1. Extract the the ISE zip file to the `exts` directory in the Isaac Sim installation folder to create (`isaac-sim/exts/amt.dtp.ise/`).
1. Start the Isaac Sim application.

    | OS | Description |
    |---|---|
    | Linux | run `./isaac-sim/isaac-sim.sh` |
    | Windows | double dlick `isaac-sim/isaac-sim.bat` |

    Isaac Sim may take some time to start; then the Isaac Sim window appears.

1. Open the Extensions Manager (`window > extensions`).
1. Search for `amt.dtp.ise` in the search bar.
1. Enable the `AMT DTP Isaac Sim Extension` (`amt.dtp.ise`) extension.
1. Enable `Autoload` for the `AMT DTP Isaac Sim Extension` so it loads every time Isaac Sim starts.
1. **Optional**: A [3Dconnexion](https://3dconnexion.com/) SpaceMouse works without extra installs; on Linux, add the udev rule described in [Install › SpaceMouse](020-Install.md#spacemouse-optional).


# Interaction

There are three methods for interacting with this service:

| Name | Deploy/Install Method | Description |
|---|---|---|
| Desktop Application | Bare Metal Only | Traditional desktop application experience. |
| WebRTC Client | Container or Bare Metal | X |
| REST API | Container or Bare Metal | X |

1. The Isaac Sim desktop application (Bare metal installation only).
1. A WebRTC client which allows you to interact with the Isaac Sim GUI similar to a desktop application (Container deployment or bare metal installation).
1. Through REST APIs (Container deployment or bare metal installation).

When deployed, this module is intended to be used through REST APIs. However, the desktop application and WebRTC clients provide a more user-friendly interface for interacting with the module.

## Desktop Application

After installing Isaac Sim and the ISE, the Isaac Sim desktop application can be started from the Isaac Sim installation directory (`isaac-sim`) using:

- Linux: run `./isaac-sim.sh`
- Windows: double click `isaac-sim.bat`

After running the application, a the Isaac Sim Graphical User Interface (GUI) window should open.

If developing testbeds, reusable assets, or the ISE, using the desktop application on bare metal provides the most seamless experience.
In this case, all software runs on a single physical machine and provides a traditional desktop application experience.
However, the ISS is accessible through the local network.

## WebRTC Client

The Isaac Sim WebRTC client allows you to interact with the Isaac Sim GUI similar to a desktop application. Unlike the desktop application, the WebRTC client
can be run on a networked computer and connect to an instance of Isaac Sim running on a networked computer. To use the WebRTC Client, follow these steps:

1. If Isaac Sim is deployed via docker, the WebRTC server is enabled by default. If Isaac Sim was installed locally, the WebRTC server must be manually enabled:

    1. Start the Isaac Sim desktop application
        - Linux: run `./isaac-sim.sh` from the Isaac Sim installation directory
        - Windows: double click `isaac-sim.bat` in the Isaac Sim installation directory
    1. Open the Extensions Manager (`window > extensions`)
    1. Search for the `Livestream Webrtc Backend` extension (`omni.kit.livestream.webrtc`)
    1. Enable the `Livestream Webrtc Backend` extension
    1. Enable autoload if you would like the WebRTC server to start every time Isaac Sim starts.

1. Download, install, and start the [Isaac Sim WebRTC Streaming Client](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/download.html)
and click `Connect`.
    - Connection Parameters:
        - Server: `127.0.0.1` (Replace this with the IP address of the remote computer that is running the Isaac Sim desktop application or docker container)
        - Resolution: `1280 x 720 (HD)`
    
    The streaming client does not inform the user if it is unable to connect to Isaac Sim.
    If deployed in a docker container and the streaming client appears to be a blank window, check the docker logs for errors to aid in diagnosing issues:

    ```bash
    docker logs amt-dtp-ise
    ```

## APIs

After the [Isaac Sim Extension (ISE)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) has been deployed or installed and is running, you can remotely 
interact with it using the following REST APIs:

| Name | Docs | Description |
|---|---|---|
| [Isaac Sim Extension (ISE)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) | [http://localhost:8010/docs/](http://localhost:8010/docs/) | An API for remotely interacting with AMT DTP Testbeds. |
| [Isaac Sim 5.1.0 Application](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/download.html) | [http://localhost:8011/docs/](http://localhost:8011/docs/) | An API provided navitely by Isaac Sim. |
| [Isaac Sim Co-Processor (ISCP)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/coprocessor) | [http://localhost:8012/docs/](http://localhost:8012/docs/) | Used by the [ISE](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) to extend Isaac Sim capabilities. |

To aid in using these APIs, the [Isaac Sim Library (ISL)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/lib) contains multiple python clients. You can download
the wheel package (`amt_dtp_ise_lib-0.0.1-py3-none-any.whl`) from the releases in this repository.

```bash
python -m venv venv
venv/bin/pip install amt-dtp-ise-lib*.whl
```

```python
#! pip install amt-dtp-ise-lib
from amt.dtp.ise.lib.apiclient import ApiClient

client = ApiClient("http://127.0.0.1:8012/")
client.application_ping()
```

```python
#! pip install amt-dtp-ise-lib
from amt.dtp.ise.lib.cosimclient import CoSimulatorClient

client = CoSimulatorClient("http://127.0.0.1:8013/")
client.application_ping()
```
