---
title: 'Environments'
---

Similar to the Isaac Sim concept of a `Stage`, the `AMT DTP Isaac Sim Extension (ISE)` concept of an `Environment` is a digital factory containing assets such as machine tools and robots.

# Structure

Environments have a strictly defined structure:

``` title="Environment Structure"
id/
├──entrypoint
└──configuration
```

In this structure:

- `id/`: A directory which includes all environment files. `id` is intended to be a unique identifier (i.e., name) for the environment.
- `id/entrypoint`: The main USD file which is opened by Isaac Sim when the environment is loaded. This file must be named `main` with any USD extension: `main.usd`, `main.usda`, `main.usdc`, or `main.usdz`. Only one of them may exist.
- `id/configuration`: An optional configuration file for the environment. If included, this file must be named `main.json`. Configuration options are covered later in the `Configure` section.

``` title="Cup Environment Example"
cup/
└──main.usd
```

Environments are intended to be transferrable between ISE deployments, and the `.zip` archive file format is used as the medium for exchange.
When archived in `.zip` format, the root directory must not be included, and the zip file name must be the same as the environment `id`.

``` title="Zipped Cup Environment Example"
cup.zip
└──main.usd
```

Additional USD files can be included as long as the required structure is maintained.

``` title="Complex Environment Example"
complex-environment/
├──main.usd
├──factory/
│  ├──cell1/
│  │  ├──machine1.usd
│  │  ├──machine1.py
│  │  ├──machine2.usd
│  │  └──machine2.py
│  └──cell2/
│     ├──machine3.usd
│     ├──machine3.py
│     ├──machine4.usd
│     └──machine4.py
└──main.json
```


# Rules

1. In all USD files, references to other USD files must use relative paths (e.g., `./factory/cell1/machine1.usd`), not absolute paths (e.g., `C:/Testbeds/complex-environment/factory/cell1/machine1.usd`). This ensures that environments are transferrable across ISE/ISS deployments.
1. Relative paths must use forward slashes (`./cell1/machine1.py`, not `.\cell1\machine1.py`) and must stay inside the environment directory (no `../` that leaves it). This applies to USD asset paths and to behavior script paths (`script_path`).
1. No two USD files in the same directory may share a name with different extensions (e.g., `machine1.usd` and `machine1.usda` together are not allowed). For the entrypoint this means exactly one `main.*` file.

## Validation

Environments are validated when they are archived (`archive`), extracted (`extract`), and uploaded (`environment_upload`, or the `/environment/upload` route). Validation **fails** if the rules above are broken, and the error lists every problem: the file (relative to the environment directory), the line, the offending path, and a suggested fix, for example:

```
 - factory/cell1/machine1.usda:12: error SCRIPT-PATH-BACKSLASH: path uses backslashes [..\_global\spawner.py] (amt:dtp:behavior:data); fix: Use forward slashes: ../_global/spawner.py
```

Fix the listed files and upload again. Some findings are reported but do not fail validation, such as a missing texture or an outdated absolute path left in a `delete` list (for example `delete payload = [@C:/old/machine1.usd@]`), which USD ignores when loading. To see every finding, run the checker on the directory:

```bash
python -m amt.dtp.ise.lib.validation environments/cup/
```

# Tutorial

1. The cup environment (`Demos/cup-1/` in the [DTP-Isaac-Sim-Service-Alpha-Testbeds](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service-Alpha-Testbeds) repository) is a minimal environment: a single [`main.usd`](https://raw.githubusercontent.com/ManufacturingTechnology/DTP-Isaac-Sim-Service-Alpha-Testbeds/main/Demos/cup-1/main.usd). Download it into a directory named `cup`:

    ```bash
    mkdir -p ~/Downloads/cup
    curl -L -o ~/Downloads/cup/main.usd "https://raw.githubusercontent.com/ManufacturingTechnology/DTP-Isaac-Sim-Service-Alpha-Testbeds/main/Demos/cup-1/main.usd"
    ```

1. Ensure that the following structure exists:

    ``` title="Cup Environment Example"
    cup/
    └──main.usd
    ```

1. In Isaac Sim, open the environment using `File > Open`, then navigating to and selecting `cup/main.usd`, and clicking `Open File`.

1. In Isaac Sim, close the current environment by creating a new stage: `File > New`.

1. In a temporary development directory, create a `environments` directory, move the `cup` environment into it, and create a python virtual environment

    ```bash
    mkdir demo
    cd demo
    cp -r ~/Downloads/cup ./
    mkdir environments
    mv cup/ environments/
    python -m venv venv
    venv/bin/pip install amt.dtp.ise.lib
    ```

1. Create a the following python script demonstrating validation and round trip archiving:

    ```python title="round_trip.py"
    #! pip install amt.dtp.ise.lib
    import time
    import os
    import shutil
    from typing import List, Optional
    from amt.dtp.ise.lib.environment import validate_directory, validate_archive, archive, extract

    # Validate the environment (returns None if valid, otherwise a list of errors)
    errors: Optional[List[str]] = validate_directory("environments/cup/")
    if errors:
        print("Errors:")
        for error in errors:
            print(f" - {error}")
        raise Exception("invalid environment directory")

    # Archive the cup environment directory -> a temporary file such as '/tmp/tmp????.zip'
    #   This will also validate the environment, throwing an exception if it is not valid.
    temp_archive_path = archive("environments/cup/")
    # Move the archive to the environments directory, named after the environment id
    shutil.move(temp_archive_path, "environments/cup.zip")
    if not os.path.isfile("environments/cup.zip"):
        raise Exception("Error: environments/cup.zip is not a file or does not exist")

    # Delete the existing environment directory
    shutil.rmtree("environments/cup/")

    time.sleep(10.0)

    # Validate the archive
    errors = validate_archive("environments/cup.zip")
    if errors:
        print("Errors:")
        for error in errors:
            print(f" - {error}")
        raise Exception("invalid environment archive")

    # Extract the environment to a temporary directory -> /tmp/????/cup/
    #   This will also validate the environment, throwing an exception if it is not valid.
    temp_dir_path = extract("environments/cup.zip")
    # Move the environment directory to the environments directory
    shutil.move(temp_dir_path, "environments/")
    if not os.path.isdir("environments/cup/"):
        raise Exception("Error: environments/cup/ is not a directory or does not exist")

    # Delete the archive
    os.remove("environments/cup.zip")
    ```

1. Run the script

    ```bash
    venv/bin/python round_trip.py
    ```

1. Create the following script demonstrating how to upload and use an environment.

    ```python title="upload.py"
    #! pip install amt.dtp.ise.lib
    import time
    from requests.exceptions import HTTPError
    from amt.dtp.ise.lib.environment import archive
    from amt.dtp.ise.lib.apiclient import ApiClient

    client = ApiClient("http://localhost:8010")
    try:
        client.extension_ping()
        if client.version != client.extension_version():
            raise Exception("client and ISE version to not match.")

        # Archive and upload environment directory
        client.environment_upload("environments/cup/")
        if "cup" not in client.environment_list():
            raise Exception("cup environment was not uploaded.")
        client.environment_remove("cup")
        
        # Upload archived environment
        temp_archive_path = archive("environments/cup/")
        client.environment_upload(temp_archive_path)
        if "cup" not in client.environment_list():
            raise Exception("cup environment was not uploaded.")
        os.remove(temp_archive_path)

        client.environment_open("cup")
        client.timeline_play()
        time.sleep(5.0)
        client.timeline_stop()
        client.environment_close()
        client.environment_clear()
    except HTTPError as e:
        print(f"Error occured communicating with the ISE API: {e}")
        raise e
    except Exception as e:
        print(f"Error occurred: {e}")
        raise e
    ```

1. Run the script

    ```bash
    venv/bin/python upload.py
    ```
