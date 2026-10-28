import os
import asyncio
import tempfile
from typing import Optional, List, Tuple, Dict, ClassVar
from click import Option
from isodate import parse_date
import numpy as np
import threading
import queue
import time
import pathlib
import math
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ConfigDict
from sympy import EX
import warp as wp
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pxr
import pxr.Gf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdShade
import pxr.UsdPhysics
import pxr.PhysxSchema
import pxr.Sdf
import pxr.Vt
import omni
import omni.kit
import omni.kit.app
import omni.kit.commands
import omni.usd
import omni.physx
import omni.physx.scripts
import omni.physx.scripts.particleUtils
#from omni.physx import get_physx_simulation_interface

from ..lib.logging import _logger



class EntityInstancer:

    def __init__(self, folder_name: str, prim_prefix: str):
        if not isinstance(folder_name, str):
            raise TypeError("folder_name must be a str")
        self._folder_name = folder_name

        if not isinstance(prim_prefix, str):
            raise TypeError("prim_prefix must be a str")
        self._prim_prefix = prim_prefix

        self._instance_id_counter: Optional[int] = None

    def reset(self):
        self._instance_id_counter = None

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise Exception("Could not get stage")

        # If folder exists, remove it
        folder_path = pxr.Sdf.Path(f"/{self._folder_name}")
        folder_prim: pxr.Usd.Prim = stage.GetPrimAtPath(folder_path)
        if folder_prim.IsValid():
            omni.kit.commands.execute("DeletePrims", paths=[folder_path]) # Recursively delete all prims

    def create_if_doesnt_exist(self) -> pxr.Sdf.Path:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise Exception("Could not get stage")
        
        # Create folder if it doesn't exist
        folder_path = pxr.Sdf.Path(f"/{self._folder_name}")
        folder_prim: pxr.Usd.Prim = stage.GetPrimAtPath(folder_path)
        if folder_prim is None or not folder_prim.IsValid():
            pxr.UsdGeom.Scope.Define(stage, folder_path)
        return folder_path

    def get_path(self) -> str:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise Exception("Could not get stage")
        
        if self._instance_id_counter is None:
            self.reset()
            self._instance_id_counter = 0

        folder_path = self.create_if_doesnt_exist()

        self._instance_id_counter += 1
        return f"{folder_path}/{self._prim_prefix}{self._instance_id_counter:012d}"

    def get_existing_prims(self) -> List[pxr.Usd.Prim]:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise Exception("Could not get stage")
        
        folder_path = pxr.Sdf.Path(f"/{self._folder_name}")
        folder_prim: pxr.Usd.Prim = stage.GetPrimAtPath(folder_path)
        if folder_prim is None or not folder_prim.IsValid():
            return # No prims to delete
        
        prims: List[pxr.Usd.Prim] = []
        for child_prim in folder_prim.GetChildren():
            if child_prim.GetName().startswith(f"{self._prim_prefix}"):
                if child_prim.IsA(pxr.UsdGeom.Imageable):
                    #imageable: pxr.UsdGeom.Imageable = pxr.UsdGeom.Imageable.Get(stage, str(self.prim.GetPath()))
                    imageable: pxr.UsdGeom.Imageable = pxr.UsdGeom.Imageable(child_prim)
                    if imageable.GetVisibilityAttr().Get() != pxr.UsdGeom.Tokens.invisible:
                        prims.append(child_prim)
        return prims

    def get_existing_paths(self) -> List[str]:
        return [str(p.GetPath()) for p in self.get_existing_prims()]
    
_instancer = EntityInstancer("Entity", "e")

def get_entity_instancer() -> EntityInstancer:
    return _instancer