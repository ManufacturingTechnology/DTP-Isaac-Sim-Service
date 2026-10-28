# AMT Digital Testbed Platform (DTP) Isaac Sim Extension (ISE)

For installation instructions, see the [Online Documentation](https://iss.amt.onl/).

## Build

To build this extension in a linux environment and create `dist/amt_dtp_ise-<version>.zip`, run in this directory:

	```bash
	make
	```

## Development

1. Download and install [Isaac Sim](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/download.html), ensuring that the Isaac Sim version that you are downloading is compatible with this version of the AMT DTP ISE. This version targets **Isaac Sim 6.0.0**.

1. Download and install [vscode](https://code.visualstudio.com/).

### Windows

First, copy `.vscode/settings.json.windows` to `.vscode/settings.json`.

To enable intellisense/code completion, create a soft symbolic link in this directory named `isaac-sim`, pointing to the Isaac Sim installation directory by running:

	```batch
	mklink /D "isaac-sim" "<Path to Isaac Sim Installation Directory>"
	```

To configure the extension for windows development, double click/run `dev.bat` to create python symbolic links and install python packages. To clean, double click/run `clean.bat`.

If you do not have admin access to create symbolic links on Windows, ask your administrator to enable symbolic links using the `Group Policy Editor (gpedit.exe)` by setting `computer configuration > Windows settings > security settings > local policy > user rights > creating symbolic links` to enabled.

### Linux

These instructions have only been tested on [Debian](https://www.debian.org/).

First, copy `.vscode/settings.json.linux` to `.vscode/settings.json`.

To enable intellisense/code completion, create a soft symbolic link in this directory named `isaac-sim`, pointing to the Isaac Sim installation directory by running:

	```bash
	ln -s "<path to isaac sim installation>" "isaac-sim"
	```

### Both

1. In Isaac Sim, make sure the `Python Debugger` extension is enabled and set to autoload.

1. In vscode, install the `Python` and `Python Debugger` extensions.

1. Drag and drop this directory, `extension`, into [vscode](https://code.visualstudio.com/) to open it.

1. In vscode, open the `Run and Debug` pane and click the `Start Debugging` button.

1. In Isaac Sim, navigate to `Window > Extensions > Options (Hamburger) > Settings > Extension Search Paths` and add the `exts` directory.

1. In Isaac Sim, navigate to `Window > Extensions > Third Party > User` and enable the `AMT Extension`.

1. If using a [3D Mouse](https://3dconnexion.com/), also install the [Spacemouse Extension](https://github.com/NVlabs/spacemouse-extension), enable it, and set it to autoload. The AMT DTP ISE will automatically use the spacemouse extension for pespective camera translation and rotation if it is installed and loaded. This only applies to Isaac Sim when locally installed, not when running it in a docker container or over the WebRTC Streaming Client. On Windows, if you have the 3Dconnexion drivers installed, you may need to disable `Pan/Zoom`, `Rotation`, and set buttons to `3Dconnexion > Disabled`, in `3Dconnexion Settings` for 3D mouse actions to pass through to Isaac Sim.

## License

Apache License 2.0 (`LICENSE.TXT` and `NOTICE` at the repository root; the build copies them into the extension). Third-party
components the extension includes or needs at run time are listed in `THIRD-PARTY-NOTICES.md`.
