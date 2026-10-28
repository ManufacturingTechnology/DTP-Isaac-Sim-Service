from typing import Generic, TypeVar, Type, Optional
from pydantic import BaseModel

import omni.behavior.scripting.core
import omni.usd
import pxr.Sdf
import pxr.Usd

from .lib.logging import _logger, Logger

T = TypeVar("T", bound=BaseModel)

# Prim custom data key holding a behavior script's config (JSON).
CONFIG_CUSTOM_DATA_KEY = "amt:dtp:behavior:config"


class BehaviorScript(omni.behavior.scripting.core.BehaviorScript, Generic[T]):

    @property
    def config(self) -> Optional[T]:
        """The behavior script's config, read from the prim when the script is constructed (so it is
        available in ``__init__`` and ``on_init()``), and read again just before ``on_play()``.

        Every time the simulation plays, behavior scripts are constructed anew from the stage, so the
        config is the one the prim has at that moment; configs can't be edited while playing. None if
        the script has no ``config_type``, or if the prim's config was missing (and the config type has
        required fields) or invalid when the script was constructed; playing then fails with the reason.
        """
        return self._config

    @property
    def config_type(self) -> Optional[Type[T]]:
        return None

    @property
    def logger(self) -> Logger:
        return _logger
        #return LoggingService().logger

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str):
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")

        # Load the config before the Omni BehaviorScript constructor calls on_init(), so on_init() (and
        # a subclass's __init__ after super().__init__()) can use it. Not strict: the config editor
        # constructs scripts to find their config_type, possibly before a valid config exists.
        self._config: Optional[T] = self._load_config(prim, strict=False)

        super().__init__(prim.GetPath())

    def _load_config(self, prim: pxr.Usd.Prim, strict: bool) -> Optional[T]:
        """Read this script's config from ``prim``: its custom data if present, otherwise the config
        type's defaults. If that fails, raise if ``strict``, otherwise return None."""
        config_type = self.config_type
        if config_type is None:
            return None
        if prim.HasCustomDataKey(CONFIG_CUSTOM_DATA_KEY):
            try:
                return config_type.model_validate_json(prim.GetCustomDataByKey(CONFIG_CUSTOM_DATA_KEY))
            except Exception as ex:
                if strict:
                    raise Exception(f"Config '{config_type.__name__}' data for prim '{prim.GetPath()}' is "
                                    f"invalid: {ex}") from ex
                _logger.debug(f"Invalid config '{config_type.__name__}' for prim '{prim.GetPath()}'", exception=ex)
                return None
        try:
            # The prim has no config data; try the config's defaults.
            return config_type()
        except Exception as ex:
            if strict:
                raise Exception(f"Config '{config_type.__name__}' data for prim '{prim.GetPath()}' BehaviorScript "
                                f"does not exist. Create one before playing.") from ex
            return None

    def on_physics(self, current_time: float, delta_time: float) -> None:
        pass
