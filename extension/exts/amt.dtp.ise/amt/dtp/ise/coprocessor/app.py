from fileinput import fileno
from genericpath import isfile
import subprocess
import sys
import os
import time
import threading
from click import Option
import psutil
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import inspect
import traceback

from ..lib.logging import _logger, LogEntry, LogLevels

# TODO: need to add logging to coprocessor, dumping json via stdout, and in this app,
#   have a thread that continuously reads stdout and logs it, so all the logging is
#   centralized in one place.

# The coprocessor's venv folder per OS (ADR-0029), in both dev and build mode.
_VENV_WINDOWS = "venv-windows"
_VENV_LINUX = "venv-linux"
# The coprocessor's entry module in the source tree (dev mode only).
_SOURCE_SCRIPT = "src/amt/dtp/iscp/coprocessor.py"


class CoProcessorApplication:

    @staticmethod
    def _install_if_needed(installer_path: str, required_paths: list[str]) -> None:
        """Run the coprocessor installer (install.bat / install.sh) if any of `required_paths` is missing.

        The venv is created here, on first start, never at build time (ADR-0030): the extension doesn't ship the
        coprocessor's packages, and a venv records the absolute path of the Python that created it, which a venv built
        elsewhere can't guarantee on this machine. The installer reuses an existing venv, so it also repairs a partial
        install. It runs synchronously and blocks Isaac Sim's start-up (~2 minutes the first time); moving it off the
        main loop is a follow-up.
        """
        if all(os.path.isfile(path) for path in required_paths):
            return
        if not os.path.isfile(installer_path):
            return
        install_subprocess = subprocess.run(
            args=[installer_path, "no_pause"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if install_subprocess.returncode != 0:
            _logger.error(f"coprocessor install return code:\n{install_subprocess.returncode}")
            _logger.error(f"coprocessor install stdout:\n{install_subprocess.stdout.decode()}")
            _logger.error(f"coprocessor install stderr:\n{install_subprocess.stderr.decode()}")
            raise Exception("Could not install coprocessor.")

    def _find_and_terminate_by_port(port: int, host: Optional[str] = None) -> None:
        # Only works on windows because it's a separate process
        # On linux, this kills isaac.
        # host = "0.0.0.0"
        if not isinstance(port, int):
            raise TypeError("port must be an int")
        if host is not None:
            if not isinstance(host, str):
                raise TypeError("host must be an Optional[str]")
        
        if host is not None:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.laddr.ip == host:
                    if conn.pid == 0:
                        return # This is a special case where the process is not running
                    try:
                        proc = psutil.Process(conn.pid)
                        proc.terminate()
                        proc.wait(timeout=5)
                    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                        proc.kill()
        else:
            for conn in psutil.net_connections():
                if conn.laddr.port == port:
                    if conn.pid == 0:
                        return # This is a special case where the process is not running
                    try:
                        psutil.Process(conn.pid).terminate()
                    except psutil.NoSuchProcess:
                        pass

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None
        self._log_threads: list[threading.Thread] = []

    def start(self, port: int = 8013) -> None:
        if self.is_running():
            raise RuntimeError("Process is already running.")
        
        _app_directory = f"{Path(__file__).parent.parent.parent.parent.parent.resolve()}/coprocessor"
        if not os.path.isdir(_app_directory):
            raise FileNotFoundError("_app_directory does not exist")

        # Dev mode: coprocessor/ is a link to the repo's coprocessor/ (source tree), whose venv dev.bat or `make dev`
        #   set up. Build mode: coprocessor/ holds the wheel and the installer, and the venv is created here on first
        #   start (ADR-0030). Both use the same per-OS venv folder (ADR-0029).
        dev_script_path = f"{_app_directory}/{_SOURCE_SCRIPT}"
        dev_module = _SOURCE_SCRIPT.replace("/", ".").removesuffix(".py")
        dev_mode = os.path.isfile(dev_script_path)

        if sys.platform == "win32":
            # If Isaac Sim crashes or the user closes the app, this may leave
            #   rogue processes running.
            # Make sure a rogue instance isn't already running
            CoProcessorApplication._find_and_terminate_by_port(port)

            venv_python_path = f"{_app_directory}/{_VENV_WINDOWS}/Scripts/python.exe"
            console_script_path = f"{_app_directory}/{_VENV_WINDOWS}/Scripts/amt-dtp-iscp.exe"
            installer_path = f"{_app_directory}/install.bat"

            if dev_mode:
                if not os.path.isfile(venv_python_path):
                    raise Exception(f"No coprocessor venv for development: run coprocessor/dev.bat ({_VENV_WINDOWS}).")
                args = [venv_python_path, "-m", dev_module, "--port", f"{port}"]
            else:
                CoProcessorApplication._install_if_needed(installer_path, [venv_python_path, console_script_path])
                if os.path.isfile(console_script_path):
                    args = [console_script_path, "--port", f"{port}"]
                else:
                    raise Exception("No coprocessor available to run.")

            self._process = subprocess.Popen(
                args=args,
                cwd=_app_directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        elif sys.platform == "linux":
            # Tell the process to exit when isaac exits
            import ctypes, signal
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_PDEATHSIG = 1
            def _set_pdeathsig():
                # Runs in the child after fork, before exec
                libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)

            # Environment variables from isaac sim are passed to child processes which
            #   pollute the subprocess/virtual environment, causing them to run in the
            #   isaac sim venv rather than the coprocessor venv, so clean the environment
            #   variables out so it doesn't impact the subprocess.
            env = os.environ.copy()
            for k in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "LD_PRELOAD"):
                env.pop(k, None)

            venv_python_path = f"{_app_directory}/{_VENV_LINUX}/bin/python"
            console_script_path = f"{_app_directory}/{_VENV_LINUX}/bin/amt-dtp-iscp"
            installer_path = f"{_app_directory}/install.sh"

            if dev_mode:
                if not os.path.isfile(venv_python_path):
                    raise Exception(f"No coprocessor venv for development: run `make dev` in coprocessor/ ({_VENV_LINUX}).")
                args = [venv_python_path, "-m", dev_module, "--port", f"{port}"]
            else:
                CoProcessorApplication._install_if_needed(installer_path, [venv_python_path, console_script_path])
                if os.path.isfile(console_script_path):
                    args = [console_script_path, "--port", f"{port}"]
                else:
                    raise Exception("No coprocessor available to run.")

            self._process = subprocess.Popen(
                args=args,
                cwd=_app_directory,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=_set_pdeathsig,
                start_new_session=False,
            )
        else:
            raise Exception("Unsupported Platform")
        
        # Wait for uvicorn to start or fail and exit.
        for i in range(5):
            time.sleep(1.0)
            rc = self._process.returncode
            if self.has_exited():
                stdout = self.read_stdout()
                stderr = self.read_stderr()
                print(stderr)
                raise RuntimeError("Process exited unexpectedly.")
            
        self._port = port

        self._log_threads.clear()
        for label, stream in (("stdout", self._process.stdout), ("stderr", self._process.stderr)):
            t = threading.Thread(target=self._log, args=(stream, label), daemon=True, name=f"AMT DTP Cosim {label}")
            t.start()
            self._log_threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        
        if sys.platform == "win32":
            # For good measure, try to terminate if still running
            if self._port is not None:
                CoProcessorApplication._find_and_terminate_by_port(self._port)

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def has_exited(self) -> bool:
        return self._process is not None and self._process.poll() is not None

    @property
    def exit_code(self) -> Optional[int]:
        if self._process is None:
            return None
        return self._process.poll()

    @property
    def pid(self) -> Optional[int]:
        if self._process is None:
            return None
        return self._process.pid

    def read_stdout(self) -> str:
        if self._process is None or self._process.stdout is None:
            return ""
        return self._process.stdout.read().decode()

    def read_stderr(self) -> str:
        if self._process is None or self._process.stderr is None:
            return ""
        return self._process.stderr.read().decode()
    
    def _log(self, stream, label):
        # Process logs sent from the coprocessor application
        for raw in stream:
            line = raw.decode(errors="replace").rstrip()
            if line:
                timestamp = datetime.now().astimezone(timezone.utc)
            frames = inspect.stack()
            _logger._log(LogEntry(timestamp, LogLevels.Trace, line, frames))

    def __repr__(self) -> str:
        status = "running" if self.is_running() else f"exited({self.exit_code})" if self.has_exited() else "not started"
        return f"BackgroundProcess(command={self._command}, status={status})"
