import importlib.util
import inspect
import numpy as np
import os
import sys
import types
from typing import Dict, Tuple, List, Union, Type, Optional, Callable
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from typing import List, ClassVar
from dataclasses import dataclass

import omni
import omni.usd
import pxr
import pxr.Gf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdPhysics
import pxr.Sdf

from ..service.subscription import SubscriptionService, SubscriptionSet, PerformanceMonitor
from ..lib.logging import _logger
from ..scripting import BehaviorScript, CONFIG_CUSTOM_DATA_KEY


def _is_abstract_class(cls: type) -> bool:
    """True if cls should be treated as abstract. inspect.isabstract only catches classes
    whose metaclass is ABCMeta (it reads __abstractmethods__). BehaviorScript's metaclass is
    plain type, so a subclass using @abstractmethod without also inheriting ABC never gets
    __abstractmethods__ populated and isabstract misses it — so we also scan for members
    still carrying the @abstractmethod marker (__isabstractmethod__). A concrete subclass
    that overrides those members replaces the marker, so it is not flagged."""
    if inspect.isabstract(cls):
        return True
    for attr in dir(cls):
        try:
            value = inspect.getattr_static(cls, attr)
        except AttributeError:
            continue
        if getattr(value, "__isabstractmethod__", False):
            return True
    return False


class BehaviorModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    model_name: ClassVar[str] = "Behavior"
    custom_data_key: ClassVar[str] = "amt:dtp:behavior:data"
    config_custom_data_key: ClassVar[str] = CONFIG_CUSTOM_DATA_KEY

    script_path: Path = Field(
        title="Script Path",
        description="The path to the script file.",
        json_schema_extra={
            #"must_exist": True, 
            "extensions": [".py"],
        },
    )


class BehaviorScriptRunner:

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim

    @property
    def behavior_script_type(self) -> Type[BehaviorScript]:
        return self._behavior_script_type

    @property
    def behavior_script(self) -> Optional[BehaviorScript]:
        return self._behavior_script
    
    @property
    def update_performance_monitor(self) -> PerformanceMonitor:
        return self._update_monitor
    
    @property
    def physics_performance_monitor(self) -> PerformanceMonitor:
        return self._physics_monitor
    
    #@property
    #def subscription_set(self) -> SubscriptionSet:
    #    return self._subscription_set

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, behavior_script_type: Type[BehaviorScript]) -> None:
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        self._prim = prim

        if not (isinstance(behavior_script_type, type) and issubclass(behavior_script_type, BehaviorScript)):
            raise TypeError("behavior_script_type must be a Type[BehaviorScript]")
        self._behavior_script_type: Type[BehaviorScript] = behavior_script_type

        self._behavior_script: Optional[BehaviorScript] = None
        #self._subscription_set: Optional[SubscriptionSet] = None

        self._update_monitor = PerformanceMonitor(f"{type(self).__qualname__}({self.prim.GetPath()})")
        self._physics_monitor = PerformanceMonitor(f"{type(self).__qualname__}({self.prim.GetPath()})")

        self._is_playing = False
        

    def initialize(self) -> bool:
        try:
            # The Omni BehaviorScript constructor calls on_init()
            behavior_script = self._behavior_script_type(self.prim)
        except Exception as ex:
            _logger.error("Failed to initialize behavior script", exception=ex)
            return False
        self._behavior_script = behavior_script

        self._is_playing = False
        return True
    
    def play(self) -> None:
        # Behavior scripts are constructed anew on every play (EntityService._on_play rebuilds the
        # behavior cache), and already loaded their config then; read it again here, strictly, so an
        # invalid or missing config stops play with the reason.
        self._behavior_script._config = self._behavior_script._load_config(self._behavior_script.prim, strict=True)

        try:
            self._behavior_script.on_play()
        except Exception as ex:
            _logger.debug(f"Failed to play behavior script: {self.behavior_script_type.__name__}", exception=ex)
            # Make a best effort to silently stop and clean up.
            try:
                self._behavior_script.on_stop()
            except Exception as ex2:
                _logger.debug(f"Failed to stop behavior script after failing to play behavior script: {self.behavior_script_type.__name__}", exception=ex2)
            raise ex
        self._is_playing = True

    def stop(self) -> None:
        if self._is_playing:
            # Only stop if successfully played
            #   This will likely throw exceptions if variables weren't initialized.
            try:
                self._behavior_script.on_stop()
            except Exception as ex:
                _logger.debug(f"Failed to stop behavior script: {self.behavior_script_type.__name__}", exception=ex)
                #raise ex
        self._behavior_script._config = None
        self._is_playing = False


    def pause(self) -> None:
        try:
            self.behavior_script.on_pause()
        except Exception as ex:
            _logger.debug(f"Failed to pause behavior script: {self.behavior_script_type.__name__}", exception=ex)
            raise ex

    def update(self, current_time: float, delta_time: float) -> None:
        if not isinstance(current_time, float):
            raise TypeError("current_time must be a float")
        if not isinstance(delta_time, float):
            raise TypeError("delta_time must be a float")
        if not self._is_playing:
            return
        self._update_monitor.start()
        try:
            self._behavior_script.on_update(current_time, delta_time)
        except Exception as ex:
            _logger.debug(f"Failed to update behavior script: {self.behavior_script_type.__name__}", exception=ex)
            raise ex
        self._update_monitor.stop()

    def physics(self, current_time: float, delta_time: float) -> None:
        if not isinstance(current_time, float):
            raise TypeError("current_time must be a float")
        if not isinstance(delta_time, float):
            raise TypeError("delta_time must be a float")
        if not self._is_playing:
            return
        self._physics_monitor.start()
        try:
            self._behavior_script.on_physics(current_time, delta_time)
        except Exception as ex:
            _logger.debug(f"Failed to physics behavior script: {self.behavior_script_type.__name__}", exception=ex)
            raise ex
        self._physics_monitor.stop()

    def destroy(self) -> None:
        if self._behavior_script is None:
            return

        if self._is_playing:
            try:
                self._behavior_script.on_stop()
            except Exception as ex:
                _logger.error(f"Failed to stop behavior script {self.behavior_script_type.__name__}", exception=ex)
        try:
            self._behavior_script.on_destroy()
        except Exception as ex:
            _logger.error(f"Failed to destroy behavior script {self.behavior_script_type.__name__}", exception=ex)
        self._behavior_script = None


class Behavior:

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        return prim.HasCustomDataKey(BehaviorModel.custom_data_key)

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
        self._prim = prim

        if not self.is_a(prim):
            raise ValueError("prim must be a Behavior")

        self._behavior_runners: List[BehaviorScriptRunner] = []
        self._initialized_script_path: Optional[str] = None

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim
    
    @property
    def runners(self) -> List[BehaviorScriptRunner]:
        return list(self._behavior_runners)
    
    def get_model(self) -> BehaviorModel:
        return BehaviorModel.model_validate_json(self.prim.GetCustomDataByKey(BehaviorModel.custom_data_key))
    
    def set_model(self, behavior_model: BehaviorModel) -> None:
        if not isinstance(behavior_model, BehaviorModel):
            raise TypeError("behavior_model must be a WaypointModel")
        self.prim.SetCustomDataByKey(BehaviorModel.custom_data_key, behavior_model.model_dump_json())
    
    @property
    def initialized_script_path(self) -> Optional[str]:
        """
        The absolute path to the script if initialized, None if not initialized.
        """
        return self._initialized_script_path
    
    def initialize(self) -> None:
        try:
            behavior_script_types = BehaviorScriptLoader.load(self.prim)
        except Exception as ex:
            _logger.debug(f"Error Loading Behavior Script Types", exception=ex)
            raise Exception(f"Error loading behavior script types for {self.prim.GetPath()}: {ex}")
            return # TODO: need to report this to the user as an error rather than hide this.
        
        for behavior_script_type in behavior_script_types:
            behavior_runner = BehaviorScriptRunner(self.prim, behavior_script_type)
            if behavior_runner.initialize():
                self._behavior_runners.append(behavior_runner)

        if not self.prim.HasCustomDataKey(BehaviorModel.custom_data_key):
            raise Exception("prim does not have BehaviorModel custom data")
        behavior_model = BehaviorModel.model_validate_json(self.prim.GetCustomDataByKey(BehaviorModel.custom_data_key))
        
        self._initialized_script_path = BehaviorScriptLoader.get_absolute_script_path(self.prim, behavior_model.script_path)

        # TODO: check for and handle failed starts

    def destroy(self) -> None:
        for behavior_runner in self._behavior_runners:
            behavior_runner.destroy()
        self._behavior_runners.clear()

    def __eq__(self, value):
        if not isinstance(value, Behavior):
            return False
        other: Behavior = value
        return self.prim.__eq__(other.prim)


class ModuleLoader:
    _package_prefix = "amt_dtp_"

    @staticmethod
    def get_loaded() -> Dict[str, types.ModuleType]:
        loaded_modules = {}
        for name in list(sys.modules.keys()):
            if ModuleLoader._package_prefix in name:
                loaded_modules[name] = sys.modules[name]
        return loaded_modules
    
    @staticmethod
    def unload(module_name: str) -> bool:
        if not isinstance(module_name, str):
            raise TypeError("module_name must be a str")

        if not module_name.startswith(ModuleLoader._package_prefix):
            return False
        if module_name in sys.modules:
            del sys.modules[module_name]
            return True
        return False
    
    @staticmethod
    def unload_all():
        for module_name in ModuleLoader.get_loaded():
            ModuleLoader.unload(module_name)

    @staticmethod
    def _flatten_path(path: str) -> str:
        # Collapse a filesystem path into a single, valid package-name segment.
        #   Dots must be replaced too, otherwise they read as package separators
        #   and break relative-import resolution.
        return (path.replace("/", "_").replace("-", "_").replace("\\", "_")
                    .replace(":", "_").replace(".", "_"))

    @staticmethod
    def _make_namespace_package(name: str, dir_path: str) -> types.ModuleType:
        # Create a namespace package whose submodules are searched for in dir_path,
        #   so that imports of its children (relative or absolute) resolve.
        pkg = types.ModuleType(name)
        pkg.__path__ = [dir_path]
        pkg.__package__ = name
        spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        spec.submodule_search_locations = [dir_path]
        pkg.__spec__ = spec
        sys.modules[name] = pkg
        return pkg

    @staticmethod
    def load(module_path: str, module_package: str, module_name: str) -> Type[types.ModuleType]:
        if not isinstance(module_path, str):
            raise TypeError("absolute_script_path must be a str")
        if not os.path.exists(module_path):
            raise FileExistsError("file does not exist at absolute_script_path")
        if not os.path.isfile(module_path):
            raise FileNotFoundError("absolute_script_path is not a file")
        if not isinstance(module_package, str):
            raise TypeError("module_package must be a str")
        if not isinstance(module_name, str):
            raise TypeError("module_name must be a str")
        
        # The directory where the module is located
        module_dir = os.path.dirname(module_path)
        module_name = BehaviorScriptLoader.get_module_name(module_path)

        # Discover the package hierarchy the script lives in by walking up the
        #   directory tree for as long as each directory is a package (contains
        #   an __init__.py). This is what lets relative imports that traverse
        #   PARENT packages resolve (e.g. `from .._global.parts import x` in
        #   assets/mill/mill.py reaching its sibling assets/_global/), not just
        #   single-level relative imports.
        package_dirs: List[str] = []
        search_dir = module_dir
        while os.path.isfile(os.path.join(search_dir, "__init__.py")):
            package_dirs.insert(0, search_dir)
            parent = os.path.dirname(search_dir)
            if parent == search_dir:
                break
            search_dir = parent

        if package_dirs:
            # `source_root` is the directory just above the top-most package; it
            #   is where the on-disk package hierarchy is rooted.
            source_root = os.path.dirname(package_dirs[0])
            # A synthetic, collision-free namespace package anchored at the
            #   source root (two different testbeds may both contain an `assets`
            #   package, so the real names cannot live at the top of sys.modules).
            #   The real directory names hang beneath it, so the dotted module
            #   path mirrors the on-disk layout and relative imports resolve.
            root_ns = ModuleLoader._package_prefix + ModuleLoader._flatten_path(source_root)
            if root_ns not in sys.modules:
                ModuleLoader._make_namespace_package(root_ns, source_root)
            # Import each real package in the chain so their __init__.py modules
            #   execute and they register under their true dotted names.
            package_name = root_ns
            for package_dir in package_dirs:
                package_name = f"{package_name}.{os.path.basename(package_dir)}"
                if package_name not in sys.modules:
                    importlib.import_module(package_name)
        else:
            # Script is not part of a package: expose its own directory as a flat
            #   namespace package so single-level relative imports still work.
            package_name = ModuleLoader._package_prefix + BehaviorScriptLoader.get_package_name(module_path)
            if package_name not in sys.modules:
                ModuleLoader._make_namespace_package(package_name, module_dir)

        full_module_name = f"{package_name}.{module_name}"

        # Load the module from the path
        spec = importlib.util.spec_from_file_location(full_module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load module from {module_path}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = package_name
        module.__file__ = module_path
        module.__last_modified_time__ = os.path.getmtime(module_path)

        # Remove __pycache__ .pyc files for default + optimization levels
        for opt in (None, "1", "2"):
            try:
                pyc = importlib.util.cache_from_source(module_path, optimization=opt)
                if pyc:
                    if os.path.exists(pyc):
                        os.remove(pyc)
            except Exception:
                pass  # best-effort

        # TODO: __pycache__ files do cause an issue hot reloading, where it will use the
        #   .pyc files repeatedly rather than load the newest .py version

        # Cache the module
        sys.modules[full_module_name] = module # TODO: is this needed?

        #importlib.invalidate_caches() # TODO: is this needed?

        # Temporarily add directory to the system path so that
        #   submodules from relative imports can be identified
        #original_path = sys.path.copy() # TODO: is this needed?
        #sys.path.insert(0, module_dir) # TODO: is this needed?
        #sys.path.insert(0, package_dir) # TODO: is this needed?
        #try:
        spec.loader.exec_module(module)
        #    sys.path = original_path # TODO: is this needed? # Set system path back to original
        #except Exception as ex:
        #    sys.path = original_path # TODO: is this needed? # Set system path back to original
        #    raise ex

        # Make sure each module has a last loaded time assigned
        for loaded_module in ModuleLoader.get_loaded().values():
            if hasattr(loaded_module, "__last_modified_time__"):
                continue
            if not hasattr(loaded_module, "__file__"):
                continue
            if loaded_module.__file__ is None:
                continue
            if not os.path.isfile(loaded_module.__file__):
                continue
            loaded_module.__last_modified_time__ = os.path.getmtime(loaded_module.__file__)

        #loaded3 = _ModuleLoader.get_loaded() # TODO: remove
        return module
    
    
    @staticmethod
    def get_modified_modules() -> Optional[Tuple[str, str, str]]:
        modified_modules: List[Tuple[str, str, str]] = []
        for module in ModuleLoader.get_loaded().values():
            if not hasattr(module, "__last_modified_time__"):
                continue
            if not hasattr(module, "__file__"):
                continue
            module_path = module.__file__
            if not os.path.isfile(module.__file__):
                continue
            mod_time = os.path.getmtime(module.__file__)
            if mod_time <= module.__last_modified_time__:
                continue
            module_package = module.__package__
            module_name = str(module.__name__)[len(module_package) + 1:]
            modified_modules.append((module_path, module_package, module_name))

        if len(modified_modules) == 0:
            return None
        
        return modified_modules
        
    @staticmethod
    def reload_modified_modules() -> None: # TODO: remove
        for module in ModuleLoader.get_loaded().values():
            if not hasattr(module, "__last_modified_time__"):
                continue
            if not hasattr(module, "__file__"):
                continue
            module_path = module.__file__
            if not os.path.isfile(module.__file__):
                continue
            if os.path.getmtime(module.__file__) <= module.__last_modified_time__:
                continue
            module_package = module.__package__
            module_name = str(module.__name__)[len(module_package) + 1:]
            ModuleLoader.load(module_path, module_package, module_name)
            _logger.trace(f"Hot Reloaded {module_path}")


class BehaviorScriptLoader:

    @staticmethod
    def get_prim_source_file(prim: pxr.Usd.Prim) -> str:
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim")
        if not prim.IsValid():
            raise ValueError("prim must be valid") # TODO: was return None
        # Not working for current root file
        p: pxr.Usd.Prim = prim
        stage = omni.usd.get_context().get_stage()
        root_prim = stage.GetPseudoRoot()
        while True:
            source_file = BehaviorScriptLoader.get_external_reference_prim_source_file(p)
            if source_file is not None:
                break
            p = p.GetParent()
            if p == root_prim or str(p.GetPath()) == "/":
                # Root of currently open file/stage
                root_layer = stage.GetRootLayer()
                source_file = root_layer.identifier if root_layer else None
                break
            if not p.IsValid():
                break

        return source_file

    @staticmethod
    def get_external_reference_prim_source_file(prim: pxr.Usd.Prim) -> Optional[str]:
        """
        Get the path to the source file of a prim which is an external reference.

        :returns: The path to the source file, or None if the prim is not an external reference.
        """
        # Only gets source file if prim is an external reference
        #   If clicking on a child of another file, doesn't list external reference
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim")
        if not prim.IsValid():
            raise ValueError("prim must be valid") # TODO: was return None

        # Get the prim stack (all composing specs)
        stack = prim.GetPrimStack()
        payloads = []

        for prim_spec in stack:
            # Get applied payloads from the spec's payloadList
            payload_list = prim_spec.payloadList.GetAppliedItems()
            for payload in payload_list:
                if payload.assetPath:
                    # Resolve relative path if needed (using the spec's layer)
                    resolved_path = pxr.Sdf.ComputeAssetPathRelativeToLayer(prim_spec.layer, payload.assetPath)
                    payloads.append(resolved_path)
                # elif payload.primPath:
                #     # Fallback for internal paths
                #     payloads.append(str(payload.primPath))

        if len(payloads) == 0:
            return None

        return payloads[-1]

    @staticmethod
    def get_absolute_script_path(prim: pxr.Usd.Prim | pxr.Sdf.Path | str, script_path: str | Path) -> Optional[str]:
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        if isinstance(script_path, Path):
            script_path = str(script_path)
        if not isinstance(script_path, str):
            raise TypeError("script_path must be a string")

        # Check if absolute and/or valid
        if os.path.isfile(script_path):
            return script_path

        # Get the script path relative to the prim file path
        prim_file_path = BehaviorScriptLoader.get_prim_source_file(prim)
        prim_dir_path = str(Path(prim_file_path).parent)
        absolute_script_path = str(Path(os.path.join(prim_dir_path, script_path)).resolve())
        if not os.path.isfile(absolute_script_path):
            return None
        
        return absolute_script_path
    
    @staticmethod
    def get_package_name(absolute_script_path: str) -> str:
        if not isinstance(absolute_script_path, str):
            raise TypeError("absolute_script_path must be a str")
        # parent directory
        return str(Path(absolute_script_path).parent).replace("/","_").replace("-","_").replace("\\","_").replace(":","_")
    
    @staticmethod
    def get_module_name(absolute_script_path: str) -> str:
        if not isinstance(absolute_script_path, str):
            raise TypeError("absolute_script_path must be a str")
        # file name without extension
        return str(Path(absolute_script_path).stem).replace("-","_")
    
    @staticmethod
    def load(prim: pxr.Usd.Prim) -> List[Type[BehaviorScript]]:
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        if not prim.HasCustomDataKey(BehaviorModel.custom_data_key):
            raise Exception("prim does not have BehaviorModel custom data")
        behavior_model_json = prim.GetCustomDataByKey(BehaviorModel.custom_data_key)
        behavior_model = BehaviorModel.model_validate_json(behavior_model_json)
        
        absolute_script_path = BehaviorScriptLoader.get_absolute_script_path(prim, behavior_model.script_path)
        if absolute_script_path is None:
            raise FileExistsError(f"file does not exist at script path '{behavior_model.script_path}'")
        
        package_name = BehaviorScriptLoader.get_package_name(absolute_script_path)
        module_name = BehaviorScriptLoader.get_module_name(absolute_script_path)
        
        module = ModuleLoader.load(absolute_script_path, package_name, module_name)

        behavior_script_types: List[Type[BehaviorScript]] = []
        for name, obj in module.__dict__.items():
            if inspect.isclass(obj) and issubclass(obj, BehaviorScript) and obj != BehaviorScript and not _is_abstract_class(obj):
                behavior_script_types.append(obj)
                
        return behavior_script_types

    @staticmethod
    def _get_types_from_native(prim: pxr.Usd.Prim) -> List[Type[BehaviorScript]]:
        """
        From the native "Python Scripting > Scripts" attribute which normally specifies
        BehaviorScripts when not using this extension, return the BehaviorScript types
        defined in the python scripts defined in the prim.
        """
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim")
        
        prim_file_path = BehaviorScriptLoader.get_external_reference_prim_source_file(prim)
        parent_dir_path = str(Path(prim_file_path).parent)
        if not parent_dir_path.endswith("/"):
            parent_dir_path = f"{parent_dir_path}/"
        dir_name = parent_dir_path.replace("/", "_").replace("-", "__dash__")
        
        
        #script_attr = prim.GetAttribute("pythonScripting:scriptAsset")
        script_attr = prim.GetAttribute("omni:scripting:scripts")
        if not script_attr:
            return []
        
        script_paths = script_attr.Get()
        if not script_paths:
            return []

        behavior_script_types: List[Type[BehaviorScript]] = []
        for script_ap in script_paths:
            script_asset_path: pxr.Sdf.AssetPath = script_ap
            script_path = str(script_asset_path.path).removeprefix("./")
            #script_path = script_path.replace("-", "__dash__")
            script_name = Path(script_path).stem.replace("/","_")
            module_name = f"{dir_name}.{script_name}"
            #module_name = module_name.replace("-", "__dash__")

            # When debugging, every time an extension reloads, it re-loads the extension
            #   module, which appears to break the link between class references. For
            #   example, if a class defined in the extension is referenced by a
            #   behavior script, and the extension is reloaded, by reloading the
            #   extension module, the class definition is now a new definition, and the
            #   BehaviorScript references the old not the new definition, so this
            #   function will not recognize and load the new definition. So,
            #   reload the module every time for debugging purposes.
            #if module_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(module_name, script_asset_path.resolvedPath)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load module from {script_asset_path.resolvedPath}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[module_name] = module

            if module_name not in sys.modules:

                return []
            
            module = sys.modules[module_name]

            for name, obj in module.__dict__.items():
                # if inspect.isclass(obj) and issubclass(obj, Actor) and obj != Actor:
                #   actor_types.append(obj)

                if inspect.isclass(obj):
                    if issubclass(obj, BehaviorScript):
                        if obj != BehaviorScript and not _is_abstract_class(obj):
                            behavior_script_types.append(obj)

        return behavior_script_types


@dataclass
class BehaviorCacheData:
    all: List[Behavior] = Field(default_factory=list)
    by_path: Dict[str, Behavior] = Field(default_factory=dict)


class BehaviorCache:

    def __init__(self):
        self._data: Optional[BehaviorCacheData] = BehaviorCacheData(all=[], by_path={})

    @property
    def data(self) -> BehaviorCacheData:
        return self._data

    def build(self) -> None:
        if len(self._data.all) > 0:
            _logger.warning("Warning, behaviors initialized without being empty") # TODO: log
    
        self.clear()
        self._data = self._build_cache_from_stage()
        self.refresh() # Initializes modules

    def clear(self) -> None:
        if self._data is not None:
            for behavior in self._data.all:
                behavior.destroy()
        self._data.all.clear()
        self._data.by_path.clear()

    def get_all(self) -> List[Behavior]:
        return list(self._data.all)

    def get(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> Optional[Behavior]:
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")

        return self.data.by_path.get(str(prim.GetPath()), None)

    def refresh(self) -> None:       
        # Want to keep existing behavior instances because they 
        #   hold references to potentially active BehaviorRunners.
        updated_behaviors = self._build_cache_from_stage().all

        # Remove and destroy obsolete behaviors
        obsolete_behaviors: List[Behavior] = []
        for behavior in self._data.all:
            if behavior not in updated_behaviors:
                obsolete_behaviors.append(behavior)
        for behavior in obsolete_behaviors:
            self._data.all.remove(behavior)
            del self._data.by_path[str(behavior.prim.GetPath())]
            behavior.destroy()

        exception_content = ""

        # Append and initialize new behaviors
        new_behaviors: List[Behavior] = []
        for behavior in updated_behaviors:
            if not behavior in self._data.all:
                new_behaviors.append(behavior)
        for behavior in new_behaviors:
            try:
                behavior.initialize()
            except Exception as ex:
                exception_content += f"Error initializing behavior: {ex}\n"
                continue
            self._data.all.append(behavior)
            self._data.by_path[str(behavior.prim.GetPath())] = behavior

        # Destroy and reinitialize changed behavior scripts
        for behavior in self._data.all:
            behavior_model = BehaviorModel.model_validate_json(behavior.prim.GetCustomDataByKey(BehaviorModel.custom_data_key))
            if behavior.initialized_script_path != behavior_model.script_path:
                behavior.destroy()
                try:
                    behavior.initialize()
                except Exception as ex:
                    exception_content += f"Error reinitializing behavior: {ex}\n"
                    continue
            #self._behaviors.append(behavior)

        if exception_content != "":
            raise Exception("Error refreshing behaviors:\n" + exception_content)

    @staticmethod
    def _build_cache_from_stage() -> BehaviorCacheData:
        all: List[Behavior] = []
        by_path: Dict[str, Behavior] = {}

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        all_prims_recursive: List[pxr.Usd.Prim] = list(stage.Traverse())
        for prim in all_prims_recursive:
            if not Behavior.is_a(prim):
                continue
            behavior = Behavior(prim)

            if behavior not in all:
                all.append(behavior)

            if str(prim.GetPath()) in by_path:
                raise Exception(f"prim {prim.GetPath()} already exists in by_path")
            by_path[str(prim.GetPath())] = behavior

        return BehaviorCacheData(
            all=all,
            by_path=by_path,
        )
        