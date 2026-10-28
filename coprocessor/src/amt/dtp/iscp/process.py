import dataclasses
import time
from http.client import responses
from queue import Queue
from typing import List, Dict, Type
from threading import Thread

from fastapi import APIRouter
#import models
from trimesh import Trimesh
import numpy as np
import uuid
import pydantic
from abc import ABC, abstractmethod

from . import __version__
from .geometry import *
from .lib.geometry import FaceRefiner
from .lib.models import FaceGeometry
from .lib.coprocessorclient import *




class BooleanProcessData(ABC):

    def __init__(
            self,
            workpiece: Trimesh,
            tools: List[Trimesh],
    ):
        if not isinstance(workpiece, Trimesh):
            raise ValueError("workpiece must be a Trimesh")
        self.workpiece = workpiece

        if not isinstance(tools, list) or not all(isinstance(t, Trimesh) for t in tools):
            raise TypeError("tools must be a List[Trimesh]")
        self.tools = tools

        self._input_sequence = 0
        self._output_sequence = 0

        self._queue: Queue[ProcessStepRequest] = Queue()
        self._thread_exit_flag = False
        self._thread = Thread(target=self._run_wrapper)
        self._thread.start()

    @property
    def is_ready_to_finish(self) -> bool:
        return self._input_sequence == self._output_sequence

    def exit(self):
        self._thread_exit_flag = True

    def update(self, update_request: ProcessStepRequest):
        self._input_sequence += 1
        self._queue.put(update_request)

    def _run_wrapper(self):
        """
        A wrapper around the thread _run function that catches exceptions.
        """
        try:
            self._run()
        except Exception as ex:
            print(f"Exception in thread _run: {ex}")

    def _run(self):
        """
        A function run on the dedicated _thread.
        """
        while not self._thread_exit_flag:
            # Check for step requests
            if self._queue.empty():
                # No step requests available
                time.sleep(0.001)
                continue

            #
            step_request = self._queue.get_nowait()
            try:
                self._update(step_request)
            except Exception as ex:
                print(f"Exception in _update: {ex}")
            self._output_sequence += 1

    def _update(self, request: ProcessStepRequest) -> None:
        workpiece = self.workpiece.copy()
        transform_matrix = np.array(request.workpiece.transform_matrix, dtype=np.float64)
        inverse_transform_matrix = np.linalg.inv(np.asarray(transform_matrix, dtype=np.float64))
        workpiece.apply_transform(transform_matrix)
        # workpiece.export(f"{request.workpiece_id}_workpiece.stl")

        for i in range(len(self.tools)):
            tool = self.tools[i].copy()
            transform_matrix = np.array(request.tools[i].transform_matrix, dtype=np.float64)
            tool.apply_transform(transform_matrix)
            # tool.export(f"{request.workpiece_id}_tool_{i:03d}.stl")

            # workpiece = workpiece.difference(tool, engine="manifold", check_volume=True)
            workpiece = self._modify(workpiece, tool)
            if workpiece.is_empty:
                raise Exception("empty mesh")

            workpiece = repair_mesh(workpiece, "Difference")

            if not workpiece.is_watertight:
                raise Exception("not watertight")
            if len(workpiece.vertices) == 0:
                raise Exception("empty vertices")
            if len(workpiece.faces) == 0:
                raise Exception("empty faces")
            if len(workpiece.vertex_normals) == 0:
                raise Exception("empty vertex_normals")

        workpiece.apply_transform(inverse_transform_matrix)
        # workpiece.export(f"{request.workpiece_id}_update.stl")
        self.workpiece = workpiece

    @abstractmethod
    def _modify(self, workpiece: Trimesh, tool: Trimesh) -> Trimesh:
        pass


class AdditiveProcessData(BooleanProcessData):

    def _modify(self, workpiece: Trimesh, tool: Trimesh) -> Trimesh:
        return workpiece.union(tool, engine="manifold", check_volume=True)

class SubtractiveProcessData(BooleanProcessData):

    def _modify(self, workpiece: Trimesh, tool: Trimesh) -> Trimesh:
        return workpiece.difference(tool, engine="manifold", check_volume=True)

_process_data: Dict[str, BooleanProcessData] = {}
#_additive_process_data: Dict[str, AdditiveProcessData] = {}
#_subtractive_process_data: Dict[str, SubtractiveProcessData] = {}

def process_start(request: ProcessStartRequest, process_type: Type[BooleanProcessData]) -> ProcessIdResponse:
    if not isinstance(request, ProcessStartRequest):
        raise TypeError("request must be a ProcessStartRequest")
    if not (isinstance(process_type, type) and issubclass(process_type, ProcessStartRequest)):
        raise TypeError("process_type must be a subclass of ProcessStartRequest")
    
    # Combine objects into a single workpiece
    w_meshes = [repair_mesh(geometry_to_tmesh(m)) for m in request.workpiece]
    workpiece: Trimesh = combine_tmeshes(w_meshes)

    tools: List[Trimesh] = []
    for i in range(len(request.tools)):
        # Combine objects into a single tool
        t_meshes = [repair_mesh(geometry_to_tmesh(t)) for t in request.tools[i]]
        tool = combine_tmeshes(t_meshes)
        tools.append(tool)

    # Generate a unique ID for the process
    workpiece_id = str(uuid.uuid4())

    # Add the process data to the global holder
    global _process_data
    _process_data[workpiece_id] = process_type(
        workpiece=workpiece,
        tools=tools,
    )

    return ProcessIdResponse(id=workpiece_id)


# def process_subtractive_start(request: ProcessStartRequest) -> GeometryStartResponse:
#     # Combine objects into a single workpiece
#     w_meshes = [repair_mesh(geometry_to_tmesh(m)) for m in request.workpiece]
#     workpiece: Trimesh = combine_tmeshes(w_meshes)

#     tools: List[Trimesh] = []
#     for i in range(len(request.tools)):
#         # Combine objects into a single tool
#         t_meshes = [repair_mesh(geometry_to_tmesh(t)) for t in request.tools[i]]
#         tool = combine_tmeshes(t_meshes)
#         tools.append(tool)

#     # Generate a unique ID for the process
#     workpiece_id = str(uuid.uuid4())

#     # Add the process data to the global holder
#     global _subtractive_process_data
#     _subtractive_process_data[workpiece_id] = SubtractiveProcessData(
#         workpiece=workpiece,
#         tools=tools,
#     )

#     return GeometryStartResponse(workpiece_id=workpiece_id)

# def process_additive_start(request: ProcessStartRequest) -> GeometryStartResponse:
#     # Combine objects into a single workpiece
#     w_meshes = [repair_mesh(geometry_to_tmesh(m)) for m in request.workpiece]
#     workpiece: Trimesh = combine_tmeshes(w_meshes)

#     tools: List[Trimesh] = []
#     for i in range(len(request.tools)):
#         # Combine objects into a single tool
#         t_meshes = [repair_mesh(geometry_to_tmesh(t)) for t in request.tools[i]]
#         tool = combine_tmeshes(t_meshes)
#         tools.append(tool)

#     # Generate a unique ID for the process
#     workpiece_id = str(uuid.uuid4())

#     # Add the process data to the global holder
#     global _additive_process_data
#     _additive_process_data[workpiece_id] = AdditiveProcessData(
#         workpiece=workpiece,
#         tools=tools,
#     )

#     return GeometryStartResponse(workpiece_id=workpiece_id)

def process_step(request: ProcessStepRequest) -> None:
    global _process_data
    try:
        data: BooleanProcessData = _process_data[request.id]
    except Exception as ex:
        print(f"-------------Invalid ID: {request.id}")
        return
    data.update(request)

# def process_subtractive_step(request: ProcessStepRequest) -> None:
#     global _subtractive_process_data
#     try:
#         data = _subtractive_process_data[request.workpiece_id]
#     except Exception as ex:
#         print(f"-------------Invalid ID: {request.workpiece_id}")
#         return
#     data.update(request)

# def process_subtractive_step(request: ProcessStepRequest) -> None:
#     global _additive_process_data
#     try:
#         data = _additive_process_data[request.workpiece_id]
#     except Exception as ex:
#         print(f"-------------Invalid ID: {request.workpiece_id}")
#         return
#     data.update(request)

def process_is_complete(request: ProcessIdRequest) -> ProcessBooleanResponse:
    global _process_data
    try:
        data: BooleanProcessData = _process_data[request.id]
    except Exception as ex:
        print(f"-------------Invalid ID: {request.id}")
        return
    return ProcessBooleanResponse(ready=data.is_ready_to_finish)

# def process_subtractive_is_complete(request: GeometryFinalizeRequest) -> GeometryReadyResponse:
#     global _subtractive_process_data
#     data = _subtractive_process_data[request.workpiece_id]
#     return GeometryReadyResponse(ready=data.is_ready_to_finish)

def process_finalize(request: ProcessIdRequest) -> ProcessFinalizeResponse:
    global _process_data
    try:
        data: BooleanProcessData = _process_data[request.id]
    except Exception as ex:
        print(f"-------------Invalid ID: {request.id}")
        return
    del _process_data[request.id]
    data.exit()

    # Get meshes. If a part is cut into pieces or there are disjoint additive bodies,
    #   there will be multiple meshes.
    meshes = data.workpiece.split() # scipy required for this
    if len(meshes) == 0:
        meshes = [data.workpiece] # Not sure why this happens, but sometimes split produces empty list

    workpieces: List[ObjectGeometry] = []
    for mesh in meshes:
        workpieces.append(
            ObjectGeometry(
                vertices=mesh.vertices.tolist(),
                faces=mesh.faces.tolist(),
                vertex_normals=mesh.vertex_normals.tolist(),
            )
        )
    return ProcessFinalizeResponse(workpieces=workpieces)

# def process_subtractive_finalize(request: GeometryFinalizeRequest) -> GeometryFinalizeResponse:
#     global _subtractive_process_data
#     data = _subtractive_process_data[request.workpiece_id]
#     del _subtractive_process_data[request.workpiece_id]
#     data.exit()

#     mesh = data.workpiece
#     meshes = mesh.split() # scipy required for this
#     if len(meshes) == 0:
#         meshes = [mesh] # Not sure why this happens, but sometimes split produces empty list

#     workpieces: List[ObjectGeometry] = []
#     for mesh in meshes:
#         workpieces.append(ObjectGeometry(
#             vertices=mesh.vertices.tolist(),
#             faces=mesh.faces.tolist(),
#             vertex_normals=mesh.vertex_normals.tolist()
#         ))
#     return GeometryFinalizeResponse(
#         workpieces=workpieces
#     )
