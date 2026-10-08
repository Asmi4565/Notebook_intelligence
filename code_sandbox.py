"""
Code Sandbox module for Notebook Intelligence System (NIS).
Provides sandboxed execution for Python code extracted from handwritten notes (FR-15, FR-16, FR-17, NFR-06).

Security Constraints:
1. Hard execution timeout (default 5 seconds, maximum 5 seconds).
2. Isolated temporary working directory (ephemeral, destroyed after execution).
3. Network access blocked (intercepts socket and network constructors).
4. Filesystem write restrictions (blocks writes outside the isolated sandbox directory).
"""

from dataclasses import dataclass
import os
import subprocess
import sys
import tempfile
import time
from typing import Optional
from pydantic import BaseModel, Field

MAX_TIMEOUT_SECONDS = 5


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    timed_out: bool = False
    error_message: Optional[str] = None


class RunCodeRequest(BaseModel):
    code: str = Field(..., description="Python source code to execute")
    language: str = Field(default="python", description="Target programming language")
    timeout_seconds: int = Field(default=5, ge=1, le=MAX_TIMEOUT_SECONDS, description="Execution timeout limit")


class RunCodeResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    timed_out: bool
    error_message: Optional[str] = None


SECURITY_PRELUDE_TEMPLATE = """# Security Prelude - Sandbox Isolation
import sys
import os
import builtins

# 1. Enforce isolated sandbox directory boundary
_SANDBOX_DIR = os.path.abspath(r"{sandbox_dir}")

_real_open = builtins.open
def _secure_open(file, mode='r', *args, **kwargs):
    if any(m in mode for m in ('w', 'a', '+', 'x')):
        try:
            target_path = os.path.abspath(str(file))
            if not target_path.startswith(_SANDBOX_DIR):
                raise PermissionError(f"Filesystem write blocked outside sandbox: {{file}}")
        except Exception as e:
            if isinstance(e, PermissionError):
                raise
            raise PermissionError(f"Filesystem write blocked: {{e}}")
    return _real_open(file, mode, *args, **kwargs)

builtins.open = _secure_open

# 2. Block network socket creation
import socket
def _blocked_socket(*args, **kwargs):
    raise PermissionError("Network access is blocked in sandbox")

socket.socket = _blocked_socket
socket.create_connection = _blocked_socket
if hasattr(socket, "getaddrinfo"):
    socket.getaddrinfo = _blocked_socket

# 3. Clean environment
del _real_open
# End of Security Prelude

"""


def execute_python_code(code: str, timeout_seconds: int = 5) -> SandboxResult:
    """
    Executes Python code inside an isolated ephemeral temporary directory with
    network blocking, filesystem write restrictions, and strict execution timeout.
    """
    if not code.strip():
        return SandboxResult(stdout="", stderr="", exit_code=0, duration_ms=0.0)

    # Cap timeout to maximum allowed boundary
    effective_timeout = min(max(1, timeout_seconds), MAX_TIMEOUT_SECONDS)

    # Create isolated ephemeral working directory
    with tempfile.TemporaryDirectory(prefix="nis_sandbox_") as sandbox_dir:
        prelude = SECURITY_PRELUDE_TEMPLATE.format(sandbox_dir=sandbox_dir)
        full_script = prelude + code

        script_path = os.path.join(sandbox_dir, "submission.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(full_script)

        try:
            start_time = time.time()

            # Run in isolated mode (-I: no user site, no PYTHONPATH, isolated environment)
            proc = subprocess.run(
                [sys.executable, "-I", "submission.py"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=effective_timeout
            )
            duration_ms = (time.time() - start_time) * 1000

            # Clean output
            stdout_str = proc.stdout or ""
            stderr_str = proc.stderr or ""

            return SandboxResult(
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=proc.returncode,
                duration_ms=round(duration_ms, 2),
                timed_out=False
            )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                stdout="",
                stderr=f"Execution timed out after {effective_timeout} seconds (resource limit exceeded).",
                exit_code=-1,
                duration_ms=round(effective_timeout * 1000.0, 2),
                timed_out=True,
                error_message=f"Sandbox timeout exceeded ({effective_timeout}s)."
            )
        except Exception as ex:
            return SandboxResult(
                stdout="",
                stderr=str(ex),
                exit_code=-1,
                duration_ms=0.0,
                error_message=str(ex)
            )
