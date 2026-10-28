import os
from pathlib import Path
from typing import Any, List, Optional, Callable, Dict, Type, get_args
from pydantic import BaseModel, ValidationError
#from pydantic_yaml import YamlModel
import json
import isaacsim
import omni
import omni.usd
import pxr
import pxr.Sdf
import pxr.Usd
import uvicorn
import asyncio
import threading
import time
import logging

from ..lib.apiclient import ApplicationConfiguration
from .core import Service
from .subscription import SubscriptionService, SubscriptionSet
from ..lib.logging import _logger
#from .logging import _logger


from omni.services.core.routers import ServiceAPIRouter
from pydantic import BaseModel
import omni.services.core.main
from fastapi import FastAPI
from omni.services.core import main as services_main


def _deep_merge(base: BaseModel, override: BaseModel) -> BaseModel:
    """
    Combine multiple configuration layers by starting with a base then adding overrides.
    """
    if not isinstance(base, BaseModel):
        raise TypeError("base must be a BaseModel")
    if not isinstance(override, BaseModel):
        raise TypeError("override must be a BaseModel")

    assert type(base) is type(override), "base and override must be the same type"

    merged = {}
    for field_name in base.__class__.model_fields:
        base_val = getattr(base, field_name)

        if field_name not in override.model_fields_set:
            merged[field_name] = base_val
            continue

        override_val = getattr(override, field_name)
        merged[field_name] = __merge_values(base_val, override_val)

    return type(base)(**merged)

def __merge_values(base: Any, override : Any):
    """
    Used by and coupled with _deep_merge to recursively merge configuration layers.
    """
    if isinstance(base, BaseModel) and isinstance(override, BaseModel):
        return _deep_merge(base, override)

    if isinstance(base, dict) and isinstance(override, dict):
        result = base.copy()
        for key, value in override.items():
            result[key] = __merge_values(base[key], value) if key in base else value
        return result

    if isinstance(base, list) and isinstance(override, list):
        return override  # or base + override to concatenate

    return override


def _dump_pretty_config(model: Type[BaseModel], default: Dict[str, Any], depth: int = 0) -> str:
    """
    Dump a config to json with comments containing descriptions of configuration options.
    """
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError("model must be a Type[BaseModel]")
    if not isinstance(default, dict) and all(isinstance(k, str) for k in default.keys()):
        raise TypeError("default must be a Dict[str, Any]")
    if not isinstance(depth, int):
        raise TypeError("depth must be an int")
    if not depth >= 0:
        raise ValueError("depth must be >= 0")
    padding = "  " * (depth + 1)

    content = ""
    for name, field in model.model_fields.items():
        content += "\n"

        header_line = ""
        if field.annotation is not None:
            field_type = str(field.annotation).lstrip("typing.")
            header_line += f" {field_type}"
        if field.description is not None:
            if field.annotation is not None:
                header_line += ":"
            header_line += f" {field.description}"
        if field.is_required():
            header_line += f" (Required)"
        if header_line != "":
            content += f"{padding}//{header_line}\n"

        basemodel_type = None
        if field.annotation is not None:
            if issubclass(field.annotation, BaseModel):
                basemodel_type = field.annotation
            typing_type = None
            try:
                typing_type = get_args(field.annotation)[0]
            except Exception as ex:
                pass
            if typing_type is not None and issubclass(typing_type, BaseModel):
                basemodel_type = typing_type
        if basemodel_type is None:
            default_value = json.dumps(default[name])
            content += f"{padding}\"{name}\": {default_value},\n"
        else:
            content += f"{padding}\"{name}\": " + "{\n"
            content += _dump_pretty_config(basemodel_type, default[name], depth + 1)
            content += padding + "},\n"
    
    return content.rstrip(",\n") + "\n" # Remove the comma from the last item

def _strip_json_comments(content: str) -> str:
    """
    Strip comments from a json file while preserving whitepace/formatting so error locations can be later identified.
    """
    if not isinstance(content, str):
        raise TypeError("content must be a str")

    buffer = ""
    i = 0
    while True:
        if i >= len(content):
            break

        if content[i] == "/" and content[i+1] == "/":
            i += 2
            buffer += "  "
            while True:
                if content[i] == "\n":
                    buffer += " "
                    i += 1
                    break
                buffer += " "
                i += 1
            continue

        if content[i] == "/" and content[i+1] == "*":
            buffer += "  "
            while True:
                if content[i] == "*" and content[i+1] == "/":
                    buffer += "  "
                    i += 2
                    break
                buffer += " "
                i += 1
            continue

        buffer += content[i]
        i += 1
    
    return buffer


def _config_resolve_paths(config: ApplicationConfiguration, root_directory_path: str) -> ApplicationConfiguration:
    """
    Convert relative paths in a configuration into absolute paths, relative to a specified root_directory_path.
    """
    # Copy so the original isn't modified
    config = config.model_copy(deep=True)

    if config.environments_directory_path is not None:
        env_path = Path(config.environments_directory_path)
        if not env_path.is_absolute():
            # path is relative, so make it absolute and resolve it
            env_path = Path(os.path.join(root_directory_path, str(env_path))).resolve()
        config.environments_directory_path = str(env_path).replace("\\", "/")

    if config.assets_directory_path is not None:
        ast_path = Path(config.assets_directory_path)
        if not ast_path.is_absolute():
            # path is relative, so make it absolute
            ast_path = Path(os.path.join(root_directory_path, str(ast_path))).resolve()
        config.assets_directory_path = str(ast_path).replace("\\", "/")

    return config


class ConfigurationService(Service):
    _singleton: Optional[Service] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True

        self._sub: Optional[SubscriptionSet] = None
        
        self._default_configuration: Optional[ApplicationConfiguration] = None
        self._global_config_path: Optional[str] = None
        self._global_configuration: Optional[ApplicationConfiguration] = None
        self._environment_configuration: Optional[ApplicationConfiguration] = None
        self._session_configuration: Optional[ApplicationConfiguration] = None
        self._resolved_configuration: Optional[ApplicationConfiguration] = None

    def _service_start(self) -> None:
        # Configure subscriptions
        self._sub = SubscriptionService().create_subscription_set("ConfigurationService")

        def _on_stage_opened(*args):
            self._reload_configs()
        self._sub.on_stage_opened = _on_stage_opened

        def _on_stage_closing(*args):
            self._environment_configuration = None
            self._reresolve_configs()
        self._sub.on_stage_closing = _on_stage_closing

        self._sub.enable()

        self._reload_configs()

    def _service_stop(self) -> None:
        if self._sub is not None:
            self._sub.destroy()

        self._global_config_path = None
        self._global_configuration = None

    @property
    def configuration(self) -> ApplicationConfiguration:
        if self._resolved_configuration is None:
            raise Exception("Configuration has not been resolved.")
        if self._session_configuration is not None:
            return _deep_merge(self._resolved_configuration, self._session_configuration)
        # Return a copy so the consumer can't modify it
        return self._resolved_configuration.model_copy(deep=True)
    
    @property
    def default_configuration_layer(self) -> ApplicationConfiguration:
        if self._default_configuration is None:
            return ApplicationConfiguration()
        # Return a copy so the consumer can't modify it
        return self._default_configuration.model_copy(deep=True)

    @property
    def global_configuration_layer(self) -> ApplicationConfiguration:
        if self._global_configuration is None:
            return ApplicationConfiguration()
        # Return a copy so the consumer can't modify it
        return self._global_configuration.model_copy(deep=True)

    @property
    def environment_configuration_layer(self) -> ApplicationConfiguration:
        if self._environment_configuration is None:
            return ApplicationConfiguration()
        # Return a copy so the consumer can't modify it
        return self._environment_configuration.model_copy(deep=True)
    
    @property
    def session_configuration_layer(self) -> ApplicationConfiguration:
        if self._session_configuration is None:
            self._session_configuration = ApplicationConfiguration()
        # Allow session configuration to be modified
        return self._session_configuration

    @property
    def path(self) -> str:
        return self._global_config_path
    
    def _load_config_default(self) -> Optional[ApplicationConfiguration]:
        """
        Load the default configuration and resolve relative paths from the user data directory path.
        """
        if os.name == "nt": # Windows
            user_data_directory_path = f"{Path(os.getenv('APPDATA'))}"
            amt_dtp_ise_config_directory_path = os.path.join(user_data_directory_path, "amt.dtp.ise")
        else: # Linux
            user_data_directory_path = f"{Path.home()}"
            amt_dtp_ise_config_directory_path = os.path.join(user_data_directory_path, ".config", "amt.dtp.ise")

        config = ApplicationConfiguration.get_default_configuration()

        return _config_resolve_paths(config, amt_dtp_ise_config_directory_path)
    
    def _load_config_global(self) -> Optional[ApplicationConfiguration]:
        """
        Load the global configuration and resolve relative paths from the global configuration path.
        """
        # Get the path to the config file via environment variable if it exists, or by default the user data directory.
        self._global_config_path = os.getenv("AMT_DTP_ISE_CONFIG_PATH")
        if self._global_config_path is None:
            if os.name == "nt": # Windows
                user_data_directory_path = f"{Path(os.getenv('APPDATA'))}"
                amt_dtp_ise_config_directory_path = os.path.join(user_data_directory_path, "amt.dtp.ise")
            else: # Linux
                user_data_directory_path = f"{Path.home()}"
                amt_dtp_ise_config_directory_path = os.path.join(user_data_directory_path, ".config", "amt.dtp.ise")

            if not os.path.exists(user_data_directory_path):
                _logger.critical(f"User Data Directory '{user_data_directory_path}' does not exist, can not load configuration.")
                raise FileNotFoundError(f"User Data Directory '{user_data_directory_path}' does not exist, can not load configuration.")

            if not os.path.exists(amt_dtp_ise_config_directory_path):
                Path(amt_dtp_ise_config_directory_path).mkdir(parents=True, exist_ok=True)

            self._global_config_path = os.path.join(amt_dtp_ise_config_directory_path, "config.json")

        if not os.path.exists(self._global_config_path):
            # Config does not exist, so create it.
            default_config = ApplicationConfiguration.get_default_configuration().model_dump()
            content = "/*\n{"
            content += _dump_pretty_config(ApplicationConfiguration, default_config)
            content += "}\n*/\n{\n  \n}\n"
            with open(self._global_config_path, "w") as json_file:
                json_file.write(content)

        if not os.path.isfile(self._global_config_path):
            _logger.critical(f"AMT DTP ISE configuration file at path {self._global_config_path} is not a file.")
            raise FileNotFoundError(f"AMT DTP ISE configuration file at path {self._global_config_path} is not a file.")
        
        # Load the configuration
        try:
            json_data = Path(self._global_config_path).read_text()
        except FileNotFoundError as ex:
            _logger.critical(f"Could not read AMT DTP ISE configuration file at path {self._global_config_path}.")
            raise ex

        json_data = _strip_json_comments(json_data)

        # Validate and load the configuration
        try:
            config = ApplicationConfiguration.model_validate_json(json_data)
        except ValidationError as ex:
            _logger.critical(f"AMT DTP ISE configuration file at path {self._global_config_path} is not valid.", exception=ex)
            raise ex
        except Exception as ex:
            _logger.critical(f"Could not parse AMT DTP ISE configuration file at path {self._global_config_path}.", exception=ex)
            raise ex
        
        return _config_resolve_paths(config, str(Path(self._global_config_path).parent))

    def _load_config_environment(self) -> Optional[ApplicationConfiguration]:
        """
        Load the environment configuration and resolve relative paths from the main (stage) USD path.
        """
        # Load environment configuration
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is None or not isinstance(stage, pxr.Usd.Stage):
            return None
        
        root_layer: pxr.Sdf.Layer = stage.GetRootLayer()
        if root_layer is None or not isinstance(root_layer, pxr.Sdf.Layer):
            return None
        
        usd_path = root_layer.realPath.replace("\\", "/") or None
        if usd_path is None:
            return None

        json_path = Path(usd_path).with_suffix(".json")
        if not os.path.exists(json_path):
            return None
        if not os.path.isfile(json_path):
            return None

        try:
            with open(json_path, 'rb') as file:
                json_data = file.read()
        except Exception as ex:
            _logger.error(f"Error reading environment configuration file {json_path}", exception=ex)
            raise ex

        json_data = _strip_json_comments(json_data)
        
        try:
            config = ApplicationConfiguration.model_validate_json(json_data)
        except Exception as ex:
            _logger.error(f"Error validating environment configuration file {json_path}", exception=ex)
            raise ex
        
        return _config_resolve_paths(config, str(Path(usd_path).parent))

    def _reload_configs(self) -> None:
        """
        Reload all configuration layers and merge all configuration layers into a single resolved configuration.
        """
        self._default_configuration = self._load_config_default()
        self._global_configuration = self._load_config_global()
        self._environment_configuration = self._load_config_environment()
        self._reresolve_configs()

    def _reresolve_configs(self) -> None:
        """
        Merge configuration layers into a single resolved configuration.
        """
        layers: List[ApplicationConfiguration] = []

        if self._default_configuration is not None:
            layers.append(self._default_configuration)

        if self._global_configuration is not None:
            layers.append(self._global_configuration)

        if self._environment_configuration is not None:
            layers.append(self._environment_configuration)

        self._resolved_configuration = layers.pop(0)
        while len(layers) >= 1:
            self._resolved_configuration = _deep_merge(self._resolved_configuration, layers.pop(0))

        Path(self._resolved_configuration.assets_directory_path).mkdir(exist_ok=True)
        Path(self._resolved_configuration.environments_directory_path).mkdir(exist_ok=True)
