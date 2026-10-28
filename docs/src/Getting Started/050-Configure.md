---
title: 'Configure'
---

# Configuration Schema

The configuration schema is defined in the `amt-dtp-ise-lib` python library:

```python title="amt.dtp.ise.lib.models.py"
--8<-- "lib/src/amt/dtp/ise/lib/models.py:ApplicationConfiguration"
```

??? "Default Configuration"

    ```python title="amt.dtp.ise.lib.models.py"
    --8<-- "lib/src/amt/dtp/ise/lib/models.py:ApplicationConfigurationDefault"
    ```

An example configuration adjusting the particle diameter and maximum element length of deformable workpieces:
```json title="config.json"
{
    "particle_diameter": 0.0508,
    "maximum_face_length": 0.0508
}
```

# Global Configuration File

When the `AMT DTP Isaac Sim Extension (ISE)` is loaded, the the ISE configuration is loaded from the following locations by default:

| OS | Path |
|---|---|
| Linux | `~/.config/amt.dtp.ise/config.json` |
| Windows | `%APPDATA%\amt.dtp.ise\config.json` |

If this file and parent directories do not exist, they will be automatically created.

The configuration file path can be overridden via the `AMT_DTP_ISE_CONFIG_PATH` environment variable.

By default, the container deployment overrides the configuration file path to be inside of a volume: `AMT_DTP_ISE_CONFIG_PATH=/files/config.json`

# Environment Configuration File

When an Isaac Sim stage or ISE Environment (e.g., `my/path/environment.usda`) is loaded, the ISE will look for a configuration file
with the same file name with a `.json` file extension (e.g., `my/path/environment.json`). If the ISE finds that file, it will
attempt to load it as a ISE configuration file, and layer it on top of the global configuration file, overriding values in the
global configuration file. When an Isaac Sim stage or ISE Environment is closed, the ISE will revert to the global configuration.
