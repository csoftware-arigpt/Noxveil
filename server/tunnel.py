import asyncio
import contextlib
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import structlog


logger = structlog.get_logger()
TUNNEL_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class TunnelManager:
    def __init__(self, local_port: int = 1324, data_dir: str | None = None):
        self.local_port = local_port
        if data_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")

        self.data_dir = data_dir
        self._url_file = os.path.join(self.data_dir, "tunnel_url.txt")
        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task | None = None
        self._output_task: asyncio.Task | None = None
        self._restart_count = 0
        self._is_running = False

        self.process: asyncio.subprocess.Process | None = None
        self.tunnel_url: str | None = None

        os.makedirs(self.data_dir, exist_ok=True)

    async def start(self, max_wait: int = 30) -> str:
        async with self._lock:
            if self.is_running and self.tunnel_url:
                return self.tunnel_url

            logger.info("starting-cloudflare-tunnel", port=self.local_port)
            if not await self._check_cloudflared():
                raise RuntimeError("cloudflared not found. Please install it first.")

            await self._terminate_process()
            self.process = await asyncio.create_subprocess_exec(
                "cloudflared",
                "tunnel",
                "--url",
                f"http://127.0.0.1:{self.local_port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )

            self.tunnel_url = await self._wait_for_url(max_wait=max_wait)
            if not self.tunnel_url:
                await self._terminate_process()
                raise RuntimeError("Failed to get tunnel URL within timeout")

            self._is_running = True
            self._restart_count = 0
            self._save_url()

            if self.process and self.process.stdout and (self._output_task is None or self._output_task.done()):
                self._output_task = asyncio.create_task(self._consume_output(), name="cloudflared-output")

            if self._monitor_task is None or self._monitor_task.done():
                self._monitor_task = asyncio.create_task(self._monitor_tunnel(), name="cloudflared-monitor")

            logger.info("tunnel-started", url=self.tunnel_url)
            return self.tunnel_url

    async def _check_cloudflared(self) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                "cloudflared",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            return process.returncode == 0
        except FileNotFoundError:
            return False

    async def _wait_for_url(self, max_wait: int) -> Optional[str]:
        if not self.process or not self.process.stdout:
            return None

        deadline = datetime.utcnow() + timedelta(seconds=max_wait)
        while datetime.utcnow() < deadline:
            if self.process.returncode is not None:
                logger.error("tunnel-process-died", returncode=self.process.returncode)
                return None

            timeout = min(1.0, (deadline - datetime.utcnow()).total_seconds())
            if timeout <= 0:
                break

            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                continue

            if not line:
                await asyncio.sleep(0.1)
                continue

            decoded = line.decode("utf-8", errors="replace").strip()
            logger.info("cloudflared-output", line=decoded)
            match = TUNNEL_URL_PATTERN.search(decoded)
            if match:
                return match.group(0)

        return None

    async def _consume_output(self):
        if not self.process or not self.process.stdout:
            return

        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    logger.info("cloudflared-output", line=decoded)
        except asyncio.CancelledError:
            raise

    async def _monitor_tunnel(self):
        try:
            while self._is_running:
                await asyncio.sleep(5)
                process = self.process
                if not process:
                    continue
                if process.returncode is None:
                    continue

                self._restart_count += 1
                logger.warning("tunnel-crashed-attempting-restart", restart_count=self._restart_count)
                if self._restart_count > 3:
                    logger.error("too-many-restarts-giving-up")
                    self._is_running = False
                    break

                try:
                    await self.start()
                except Exception as exc:
                    logger.error("tunnel-restart-failed", error=str(exc))
        except asyncio.CancelledError:
            raise

    async def _terminate_process(self):
        if not self.process:
            return

        process = self.process
        self.process = None
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    def _save_url(self):
        if not self.tunnel_url:
            return
        with open(self._url_file, "w", encoding="utf-8") as url_file:
            url_file.write(self.tunnel_url)
        logger.info("tunnel-url-saved", file=self._url_file)

    async def stop(self):
        logger.info("stopping-tunnel")
        self._is_running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        if self._output_task:
            self._output_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._output_task
            self._output_task = None

        await self._terminate_process()
        self.tunnel_url = None

    async def restart(self) -> str:
        logger.info("restarting-tunnel")
        await self.stop()
        await asyncio.sleep(2)
        return await self.start()

    @property
    def is_running(self) -> bool:
        return self._is_running and self.process is not None and self.process.returncode is None and bool(self.tunnel_url)


async def get_tunnel_url(data_dir: str | None = None) -> Optional[str]:
    if data_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")

    url_file = os.path.join(data_dir, "tunnel_url.txt")
    if not os.path.exists(url_file):
        return None

    with open(url_file, "r", encoding="utf-8") as saved_url:
        value = saved_url.read().strip()
    return value or None
