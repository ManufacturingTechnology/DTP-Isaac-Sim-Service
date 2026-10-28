"""Models of the CoreApi base endpoints, shared by servers and clients (no fastapi needed)."""
from typing import List
from pydantic import BaseModel, Field


class CoreInfo(BaseModel):
    """Identity of a control API."""
    title: str = Field(title="Title")
    description: str = Field(title="Description")
    version: str = Field(title="Version", description="The version set by the API's author.")
    layers: List[str] = Field(
        title="Layers",
        description="The API layers this API is built from, base first (e.g. CoreApi, SegmentApi). Each "
                    "layer adds its own endpoints.",
    )


class AssetInfo(BaseModel):
    """A file the API serves (e.g. a URDF robot description)."""
    name: str = Field(title="Name", description="The file name; download it from /api/core/assets/download/{name}.")
    size: int = Field(title="Size", description="The file size in bytes.")
    media_type: str = Field(title="Media Type")
    sha256: str = Field(title="SHA-256", description="The SHA-256 hash of the file contents, as hex.")
