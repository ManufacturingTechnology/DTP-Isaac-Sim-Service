import os
from pathlib import Path

import os
from click import Option
from pydantic import BaseModel, FilePath
from typing import Optional, List
from pathlib import Path
import requests
import shutil
import zipfile
import tempfile

from .validation import ENTRY_EXTS, ENTRY_STEM, Severity, check_package_paths, format_issue


class PackageValidationError(Exception):
    """Raised by :func:`archive` / :func:`extract` when a package or archive is invalid. ``errors``
    holds one line per problem (for path rules: ``file[:line]: error RULE: message [path] (context);
    fix: ...``, with ``file`` relative to the package root)."""

    def __init__(self, message: str, errors: List[str]):
        super().__init__(message + "\n" + "\n".join(f" - {e}" for e in errors))
        self.errors = errors


def get_environment_usd_path(directory_path: Path | str) -> Optional[Path]:
    # The entry layer: a file named main.* with any USD extension (main.usd, .usda, .usdc, .usdz).
    # Note: rglob, so a main.* in a subfolder also counts (see the package-validation spec).
    if isinstance(directory_path, str):
        directory_path = Path(directory_path)
    if not isinstance(directory_path, Path):
        raise TypeError("directory_path must be a Path | str")

    usd_files = [f for f in directory_path.rglob("*") if f.suffix.lower() in ENTRY_EXTS]
    if len(usd_files) == 0:
        return None

    main_usd_files = [f for f in usd_files if f.stem.lower() == ENTRY_STEM]
    if len(main_usd_files) == 0:
        return None

    return main_usd_files[0]

def package_errors(directory_path: Path | str, *, use_pxr: bool = True) -> List[str]:
    # Error-severity findings of the package path rules (amt.dtp.ise.lib.validation), one line each.
    # Warnings and info don't make a package invalid.
    return [format_issue(i, with_fix=True) for i in check_package_paths(directory_path, use_pxr=use_pxr)
            if i.severity == Severity.ERROR]

def validate_directory(directory_path: Path | str, *, use_pxr: bool = True) -> Optional[List[str]]:
    # Returns a list of errors if there are errors, otherwise None.
    # Checks the entry layer (main.usd/.usda/.usdc/.usdz) and every error-severity package rule
    # (absolute or backslash paths, paths leaving the package, duplicate USD name stems, ...).
    if isinstance(directory_path, str):
        directory_path = Path(directory_path)
    if not isinstance(directory_path, Path):
        raise TypeError("directory_path must be a Path | str")

    if not os.path.exists(directory_path):
        return [f"directory_path {directory_path} does not exist"]
    if not os.path.isdir(directory_path):
        return [f"directory_path {directory_path} must be a directory"]

    errors: List[str] = []

    if get_environment_usd_path(directory_path) is None:
        names = ", ".join(ENTRY_STEM + ext for ext in ENTRY_EXTS)
        errors.append(f"environment {directory_path} does not contain an entry USD file ({names})")

    errors += package_errors(directory_path, use_pxr=use_pxr)

    return errors if len(errors) > 0 else None

def validate_archive(archive_path: Path | str) -> Optional[List[str]]:
    # Returns a list of errors if there are errors, otherwise None
    if isinstance(archive_path, str):
        archive_path = Path(archive_path)
    if not isinstance(archive_path, Path):
        raise TypeError("archive_path must be a Path | str")
    
    if not os.path.exists(archive_path):
        return [f"archive_path {archive_path} does not exist"]
    if not os.path.isfile(archive_path):
        return [f"archive_path {archive_path} must be a file"]
    if not str(archive_path).lower().endswith(".zip"):
        return [f"archive_path {archive_path} must be a zip file"]
    
    errors: List[str] = []
    
    # TODO: validate

    return errors if len(errors) > 0 else None

def archive(directory_path: Path | str) -> Path:
    # Returns path to temp file
    if isinstance(directory_path, str):
        directory_path = Path(directory_path)
    if not isinstance(directory_path, Path):
        raise TypeError("directory_path must be a Path | str")

    errors = validate_directory(directory_path)
    if errors is not None:
        raise PackageValidationError(f"directory_path {directory_path} is not a valid environment directory:", errors)

    # Zip the directory relative to the directory_path so that the root directory is not included
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        archive_path = Path(tmp.name)
        with zipfile.ZipFile(archive_path, "w") as zip:
            for file in Path(directory_path).rglob("*"):
                zip.write(file, arcname=file.relative_to(directory_path))

    errors = validate_archive(archive_path)
    if errors is not None:
        raise PackageValidationError(f"archive_path {archive_path} created from {directory_path} is not a valid environment file:", errors)

    return archive_path

def extract(archive_path: Path | str) -> Path:
    # Returns path to temp directory
    if isinstance(archive_path, str):
        archive_path = Path(archive_path)
    if not isinstance(archive_path, Path):
        raise TypeError("archive_path must be a Path | str")

    errors = validate_archive(archive_path)
    if errors is not None:
        raise PackageValidationError(f"archive_path {archive_path} is not a valid environment file:", errors)

    # Extract zip file to temp directory
    tmp_dir = Path(tempfile.mkdtemp()).resolve()
    directory_path = Path(os.path.join(tmp_dir, archive_path.stem))
    directory_path.mkdir()
    with zipfile.ZipFile(archive_path, 'r') as zip:
        zip.extractall(str(directory_path))

    errors = validate_directory(directory_path)
    if errors is not None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise PackageValidationError(f"archive {archive_path.name} is not a valid environment:", errors)

    return directory_path