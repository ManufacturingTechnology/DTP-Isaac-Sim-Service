---
title: 'Install'
---

!!! info "Requirements and third-party terms"

    The extension runs on NVIDIA software that it doesn't include: Isaac Sim, the Omniverse Kit it is built on, and
    cuMotion (used for robot kinematics and motion planning). You download them from NVIDIA and use them under
    NVIDIA's license terms, which you accept when you install or run them. This extension itself is licensed under
    the Apache License 2.0; the third-party components it does include are listed in `THIRD-PARTY-NOTICES.md`.

    NVIDIA, Isaac Sim and Omniverse are trademarks or registered trademarks of NVIDIA Corporation. MTConnect is a
    registered trademark of AMT - The Association For Manufacturing Technology. Other product and company names are the
    trademarks of their respective owners and are used only to identify those products. This project is developed by AMT
    and is not affiliated with, sponsored by, or endorsed by NVIDIA.

Bare metal deployment is supported on Linux and Windows. Container (e.g., Docker) deployments are also supported, and are detailed in the later [Deploy](900-Deploy.md) section.


# Isaac Sim

Each version of the `AMT DTP Isaac Sim Extension (ISE)` only works with a specific version of Isaac Sim and is not backwards comaptible with older versions of Isaac Sim.
Ensure that you are following installation instructions for the specific version of the ISE you are installing, and installing the appropriate version of Isaac Sim.

1. Download and extract [Isaac Sim 6.0.1](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/download.html) to any directory. (e.g., `isaac-sim/`).

    ???+ tip

        The deeply nested directory structure of Isaac Sim can result in long file paths which exceed the path length limit of windows, causing unexpected Isaac Sim behavior.
        To avoid this issue on Windows, the recommended location to install Isaac Sim to is: `C:\Applications\isaac-sim`.
        The `C:\Applications\` directory does not exist in Windows by default and must be manually created.

    ???+ warning

        Administrator privileges are required to modify files in the `C:\Program Files\` directory. The ISE must be able to modify files at run time.
        As a result, if you install Isaac Sim in the `C:\Program Files\` directory, the ISE may crash because it can not modify files in the `isaac-sim` installation directory.
        While not recommended, this issue can be sidestepped by always running Isaac Sim with administrator privileges.
        A better solution is to install Isaac Sim in a directory which does not require administrator privileges, such as `C:\Applications\`.

# AMT DTP Isaac Sim Extension (ISE)

1. Download and install Python 3.13 or newer, then reboot your computer.

    | OS | Description |
    |---|---|
    | Linux | Debian: `sudo apt update && sudo apt install -y python3 python3-venv python3-pip` |
    | Windows | [App Store Download](https://apps.microsoft.com/detail/9PNRBTZXMB4Z) <br> [Python Website Download](https://www.python.org/downloads/) |

1. Check the python version to confirm that python is installed and in your `PATH` environment variable.

    | OS | Description |
    |---|---|
    | Linux | `python --version` and/or `py --version` |
    | Windows | In the Windows start bar, search for `Command Prompt` (or press `Windows + R` and enter `cmd`) and run `python --version` and/or `py --version` |

    The ISE will identify and use the appropriate command (`python` or `py`) on your system. At least one command must work.

    ??? note "Troubleshooting"

        The `PATH` environment variable is a list of directories that should be searched when running a command from the Linux Terminal or Windows Command Prompt.
        When you run the `python --version` command, the terminal (or command prompt) will search all of the directories in the `PATH` environment variable
            for `python` (or `python.exe` on Windows), and if it finds it, it will execute it with the specified arguments (e.g., `--version`).
        If `python` (or `python.exe` on Windows) can not be found, an error message will appear like below:
        
        ```text title="python --version"
        'python' is not recognized as an internal or external command,
        operable program or batch file.
        ```
        
        If successful, the python version will be shown like below.

        ```text title="python --version"
        Python 3.13.13
        ```

        Some python installations use `py` rather than `python` as the main executable. If `python --version` fails, also try `py --version`.

        ```text title="py --version"
        Python 3.13.13
        ```

        Some Windows python installers do not automatically add the python executable to the `PATH` environment variable. If you used an installer which has configuration
            options such as the installer downloaded from the python website, and you are still having problems, you may need to reinstall python and ensure that
            during installation, any options related to adding python to the `PATH` environment variable are enabled.

        To check the contents of the `PATH` environment variable via terminal or command line:

        | OS | Description |
        |---|---|
        | Linux | `echo $PATH` |
        | Windows | `echo %PATH%` |

        On Windows, you can manually edit the `PATH` environment variable using:

        - All Users: `Windows Start Menu > Search > Edit The System Environment Variables > Select Path > Edit... > New > Browse`
        - Current User: `Windows Start Menu > Search > Edit Environment Variables for your Account > Select Path > Edit... > New > Browse`

        The `PATH` environment variable should point to where `python.exe` is installed.
        Different python installers install `python.exe` in different directories, which can make it difficult to find.
        

1. Download the [latest ISE release](https://github.com/manufacturingtechnology/DTP-Isaac-Sim-Service/releases/latest) (`amt.dtp.ise-x.x.x.zip`).

1. Extract the the ISE zip file to the `exts` directory in the Isaac Sim installation folder to create `isaac-sim/exts/amt.dtp.ise/`. After, the file `isaac-sim/exts/amt.dtp.ise/readme.md` should exist.

1. Start the Isaac Sim application.

    | OS | Description |
    |---|---|
    | Linux | run `./isaac-sim/isaac-sim.sh` |
    | Windows | double dlick `isaac-sim/isaac-sim.bat` |

    In general, the Isaac Sim application takes a long time to start compared to other applications.
    In addition, the first time that Isaac Sim is started, it will perform setup tasks which can take several minutes to complete.
    While setup tasks are running, the viewport will appear black, with no gridlines.

    When Isaac Sim and the ISE extension have finished loading, the main window shows the viewport with gridlines (e.g., green, red, and grey lines).

1. Open the Extensions Manager (`Window > Extensions`).

1. In the search bar, clear the default search of `@feature` by clicking the `x` beside it, then search for `AMT DTP Isaac Sim Extension` or `amt.dtp.ise`.

1. Enable the `AMT DTP Isaac Sim Extension` (`amt.dtp.ise`) extension.

    The first time the ISE extension is loaded, the ISE extension will perform setup tasks which can take ~2 minutes to complete:
    it installs its co-processor and the Python packages it needs into a `venv-windows/` or `venv-linux/` folder inside the
    extension, using the Python installed above. This needs an internet connection, and Isaac Sim doesn't respond until
    it finishes. Later starts reuse that folder.

    ??? note "Troubleshooting"

        Warnings that mention `SpaceMouse` can appear when starting the ISE, for example on Linux before the udev rule
            is added (see [SpaceMouse](#spacemouse-optional)). They can be ignored during this step.
        
        If Isaac Sim hangs/freezes for more than ~2 minutes after enabling the ISE, the most likely cause is an issue creating a python virtual environment.
        Use the following steps to troubleshoot this issue.

        1. Close/exit the Isaac Sim application.
        1. Navigate to the `isaac-sim/exts/amt.dtp.ise/coprocessor/` directory.
        1. If a directory named `venv-linux/` or `venv-windows/` exists, delete it.
        1. Install the coprocessor virtual environment.

            | OS | Description |
            |---|---|
            | Linux | run `./install.sh` |
            | Windows | double dlick `install.bat` |

            This creates a `venv-linux/` or `venv-windows/` directory and installs python packages in it.

            If successful, the following message will appear:

            ``` title="./install.sh or install.bat"
            coprocessor installed successfully.
            Press any key to continue . . .
            ```

            If not successful, errors should provide troubleshooting guidance/information.
        
        1. Run the coprocessor to ensure it works correctly.

            | OS | Description |
            |---|---|
            | Linux | run `./run.sh` |
            | Windows | double dlick `run.bat` |

            If successful, the following message will appear:

            ``` title="./run.sh or run.bat"
            INFO:     Uvicorn running on http://0.0.0.0:8013 (Press CTRL+C to quit)
            ```

            If not successful, errors should provide troubleshooting guidance/information.

        1. Close/exit the coprocessor window.

        1. Start Isaac Sim and enable the ISE Extension.


1. Enable `Autoload` for the `AMT DTP Isaac Sim Extension` so it automatically loads every time Isaac Sim starts.

???+ tip

    When the ISE is running in Isaac Sim, this documentation website, for the version of the ISE that is running, is available offline here: [http://localhost:8012/](http://localhost:8012/)


# WebRTC Client (Optional)

The Isaac Sim WebRTC Streaming Client allows you to remotely interact with Isaac Sim similar to a "remote desktop" session.

Unlike the Isaac Sim desktop application, the WebRTC client can connect to an instance of Isaac Sim running on a remote server.

``` mermaid
flowchart LR
    subgraph Client[Client Machine]
        WC[WebRTC Client]
    end

    subgraph Server[Server Machine]
        subgraph IS[Isaac Sim]
            S["Application"]
            WS["WebRTC Server"] <--> S
        end
    end

    WC <--> WS
```

However, the WebRTC client can also connect to Isaac Sim running on the same machine.

``` mermaid
flowchart TD
    subgraph Server[Machine]
        WC[WebRTC Client]

        subgraph IS[Isaac Sim]
            S["Application"]
            WS["WebRTC Server"] <--> S
        end
    end

    WC <--> WS
```

To use the WebRTC Client, follow these steps:

1. In Isaac Sim, Open the Extensions Manager (`Window > Extensions`).

1. In the search bar, clear the default search of `@feature` by clicking the `x` beside it, then search for `Livestream Webrtc Backend` or `omni.kit.livestream.webrtc`.

1. Enable the `Livestream Webrtc Backend` extension.

1. **Optional**: Enable `Autoload` for the `Livestream Webrtc Backend` so it automatically loads every time Isaac Sim starts.

1. Download, install, and start the [Isaac Sim WebRTC Streaming Client](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/download.html).

1. Start the WebRTC client and configure the connection:

    | Connection | Settings |
    |---|---|
    | Remote | Server: The IP address of the remote server (`x.x.x.x`) <br> Resolution:  `1280 x 720 (HD)` |
    | Local | Server: `localhost` or `127.0.0.1` <br> Resolution:  `1280 x 720 (HD)` |

1. Click `Connect`.

Note that the WebRTC client does not display any error messages if it is unable to connect to a remote Isaac Sim instance; it simply displays a blank window.    

When the WebRTC client successfully connects to Isaac Sim, the Isaac Sim user interface appears in the WebRTC client window.


# SpaceMouse (Optional)

If you have a [3Dconnexion](https://3dconnexion.com/) SpaceMouse, or another 6-DOF controller that presents itself as a
standard USB HID "multi-axis controller", the ISE uses it to fly the viewport camera. Nothing extra needs to be
installed: plug it in, and the ISE finds it at startup or within a couple of seconds of plugging it in.

| Input | Camera |
|---|---|
| Push, pull, or slide the cap | Move forward/back, up/down, left/right |
| Tilt the cap forward or back, twist it | Look down or up, turn left or right |
| Left button | Frame the current selection |
| Right button | Reset the view |

The SpaceMouse only moves the camera while Isaac Sim is the active application, so you can use it in another
program side by side without Isaac Sim's view moving too. It isn't used at all when Isaac Sim runs without a window
(headless or streaming-only).

- **Windows:** works with or without 3Dconnexion's software (3DxWare) installed.
- **Linux:** reading the device needs permission. Add a udev rule once, then unplug and replug the device:

    ```bash
    echo 'KERNEL=="hidraw*", ATTRS{idVendor}=="256f", MODE="0660", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/99-spacemouse.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger
    ```

    `256f` is 3Dconnexion's USB vendor ID; some older models use Logitech's (`046d`).

To check what the ISE sees, run the diagnostics from the [Python library](#amt-dtp-isaac-sim-python-library-ispl):
`python -m amt.dtp.ise.lib.devices.spacemouse --list` lists devices with their driver, and `--dump 20` prints the
device's layout and live values for 20 seconds while you move the cap and press the buttons.

???+ info "Tested devices"

    | Device | OS | 3Dconnexion software | Result |
    |---|---|---|---|
    | SpaceMouse Compact | Windows 11 | installed and running | works |
    | SpaceMouse Compact | Windows 11 | not installed | works |

    Other models are expected to work; Linux has not been tested on hardware yet. If you test another device or OS,
    please report the output of `--dump 20`.

??? note "Previous method: the SRL SpaceMouse extension"

    Until the built-in support has been verified, the ISE can still use the external
    [SpaceMouse Omniverse Kit Extension](https://github.com/NVlabs/spacemouse-extension) instead: run
    `extension/install-spacemouse.bat` (Windows) or `extension/install-spacemouse.sh` (Linux), and set
    `SPACEMOUSE_BACKEND = "srl"` in the ISE's `service/spacemouse.py`. This method will be removed.

# Using a SpaceMouse in your own application

The Python library reads a SpaceMouse without Isaac Sim and without any other dependency, so your own programs can
use one too (for example, to drive a remote Isaac Sim instance or a robot):

```python
from amt.dtp.ise.lib.devices.spacemouse import SpaceMouse

with SpaceMouse() as mouse:           # the first device found; reconnects if it's unplugged
    state = mouse.state()             # the latest values; never blocks
    print(state.translation, state.rotation, state.buttons)
```

- `translation` (X, Y, Z) and `rotation` (Rx, Ry, Rz) are in the device's own axes, each scaled to `-1..1`; which
  physical motion is positive is the device's convention, so map them to your application's axes.
- `buttons` lists the pressed buttons (1, 2, ...); `connected` and `device` say which device is in use.
- `SpaceMouse(on_change=callback)` calls `callback(state)` whenever a value changes (on the library's reader thread).
- `SpaceMouseState` is a pydantic model, so a state can be sent over a network as JSON.
- `shape_axis(value, deadzone, expo)` applies a deadzone and a softer response near the center to one axis.

# AMT DTP Isaac Sim Python Library (ISPL)

A python library is provided for interacting with the ISE.

The python will eventually be made available through [PyPi](https://pypi.org/).

The ISE python library is build alongside the ISE and the versions are locked together to ensure compatibility.
Use the same version of the ISE python library as the ISE version that you are using.

```bash
pip install amt-dtp-ise-lib
```
