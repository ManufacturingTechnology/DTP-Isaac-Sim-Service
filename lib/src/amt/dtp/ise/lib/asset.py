import os
from pathlib import Path

import os
from click import Option
from pydantic import BaseModel, FilePath
from typing import Optional, List
from pathlib import Path
import requests
import zipfile
import tempfile

from .environment import package_errors
from .validation import ENTRY_EXTS, ENTRY_STEM

# Unused twin of environment.py (the asset routes use environment.extract); kept consistent with it.


def get_asset_usd_path(directory_path: Path | str) -> Optional[Path]:
    if isinstance(directory_path, str):
        directory_path = Path(directory_path)

    usd_files = [f for f in directory_path.rglob("*") if f.suffix.lower() in ENTRY_EXTS]
    if len(usd_files) == 0:
        return None

    main_usd_files = [f for f in usd_files if f.stem.lower() == ENTRY_STEM]
    if len(main_usd_files) == 0:
        return None
    
    return main_usd_files[0]

def validate_asset_directory(directory_path: Path | str) -> Optional[List[str]]:
    # Returns a list of errors if there are errors, otherwise None
    if isinstance(directory_path, str):
        directory_path = Path(directory_path)
    if not isinstance(directory_path, Path):
        raise TypeError("directory_path must be a Path | str")
    
    if not os.path.exists(directory_path):
        return [f"directory_path {directory_path} does not exist"]
    if not os.path.isdir(directory_path):
        return [f"directory_path {directory_path} must be a directory"]
    
    errors: List[str] = []
    
    if get_asset_usd_path(directory_path) is None:
        errors.append(f"asset {directory_path} does not contain a usd file")

    errors += package_errors(directory_path)

    return errors if len(errors) > 0 else None

def validate_asset_file(file_path: Path | str) -> Optional[List[str]]:
    # Returns a list of errors if there are errors, otherwise None
    if isinstance(file_path, str):
        file_path = Path(file_path)
    if not isinstance(file_path, Path):
        raise TypeError("file_path must be a Path | str")
    
    if not os.path.exists(file_path):
        return [f"file_path {file_path} does not exist"]
    if not os.path.isfile(file_path):
        return [f"file_path {file_path} must be a file"]
    if not str(file_path).lower().endswith(".zip"):
        return [f"file_path {file_path} must be a zip file"]
    
    errors: List[str] = []
    
    # TODO: validate

    return errors if len(errors) > 0 else None

def compress_asset(directory_path: Path | str) -> Path:
    # Returns path to temp file
    if isinstance(directory_path, str):
        directory_path = Path(directory_path)

    errors = validate_asset_directory(directory_path)
    if errors is not None:
        errors = "\n".join(errors)
        raise Exception(f"directory_path {directory_path} is not a valid asset directory:\n{errors}")
    
    # Zip the directory relative to the directory_path so that the root directory is not included
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        file_path = Path(tmp.name)
        with zipfile.ZipFile(file_path, "w") as zip:
            for file in Path(directory_path).rglob("*"):
                zip.write(file, arcname=file.relative_to(directory_path))

    errors = validate_asset_file(file_path)
    if errors is not None:
        errors = "\n".join(errors)
        raise Exception(f"file_path {file_path} created from {directory_path} is not a valid asset file:\n{errors}")
    
    return file_path

def extract_asset(file_path: Path | str) -> Path:
    # Returns path to temp directory
    if isinstance(file_path, str):
        file_path = Path(file_path)

    errors = validate_asset_file(file_path)
    if errors is not None:
        errors = "\n".join(errors)
        raise Exception(f"file_path {file_path} is not a valid asset file:\n{errors}")
    
    # Extract zip file to temp directory
    tmp_dir = Path(tempfile.mkdtemp()).resolve()
    directory_path = Path(os.path.join(tmp_dir, file_path.stem))
    directory_path.mkdir()
    with zipfile.ZipFile(file_path, 'r') as zip:
        zip.extractall(str(directory_path))

    errors = validate_asset_directory(directory_path)
    if errors is not None:
        errors = "\n".join(errors)
        raise Exception(f"directory_path {directory_path} is not a valid asset directory:\n{errors}")
    
    return directory_path