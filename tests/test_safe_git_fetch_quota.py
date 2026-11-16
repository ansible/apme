"""Real production Git snapshot quota tests; CI requires private tmpfs mounts."""

import asyncio
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest

from apme_gateway.scm import safe_fetch


async def _exercise_https_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Exercise real HTTPS snapshots with optional kernel storage limits.

    Args:
        tmp_path: Sources and private mountpoint.
        monkeypatch: Deployment and DNS overrides.
        mode: Git expansion stage or overlapping aggregate storage to stress.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    required = os.environ.get("APME_REQUIRE_GIT_QUOTA_TESTS") == "1"
    privileged_mount = ["sudo", "-n"] if required and os.geteuid() != 0 else []
    if not shutil.which("git") or not shutil.which("openssl"):
        if required:
            pytest.fail("Required Git quota gate requires Git and OpenSSL")
        pytest.skip("HTTPS snapshot tests require Git and OpenSSL")
    config = subprocess.run(["git", "help", "--config"], capture_output=True)
    if config.returncode or b"http.curloptResolve\n" not in config.stdout:
        if required:
            pytest.fail("Required Git quota gate requires Git http.curloptResolve support")
        pytest.skip("HTTPS snapshot tests require Git http.curloptResolve support")
    mount = tmp_path / "quota"
    mount.mkdir()
    if mode == "transport":
        monkeypatch.setattr(safe_fetch, "_open_storage", lambda: os.open(mount, os.O_RDONLY | os.O_DIRECTORY))
    else:
        result = subprocess.run(
            [
                *privileged_mount,
                "mount",
                "-t",
                "tmpfs",
                "-o",
                f"size=4m,nr_inodes={128 if mode == 'loose' else 8192},mode=0700,uid={os.getuid()},gid={os.getgid()}",
                "tmpfs",
                str(mount),
            ],
            capture_output=True,
        )
        if result.returncode:
            if required:
                pytest.fail("Required Git quota gate cannot mount its bounded tmpfs")
            pytest.skip("Private tmpfs mount requires CAP_SYS_ADMIN")
    server = None
    worker = None
    try:
        monkeypatch.setenv("APME_GIT_FETCH_ROOT", str(mount))
        if mode != "transport":
            assert safe_fetch._storage_root() == mount
        source = tmp_path / "repo.git"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "small",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(source), "branch", "small"], check=True, capture_output=True)
        if mode == "loose":
            for n in range(300):
                (source / str(n)).write_text(f"object {n}\n")
        else:
            (source / "large").write_bytes(
                b"x"
                if mode == "transport"
                else (
                    b"x" * ((3 if mode == "shared" else 6) * 1024 * 1024)
                    if mode in {"checkout", "shared"}
                    else os.urandom(6 * 1024 * 1024)
                )
            )
        subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "large",
            ],
            check=True,
            capture_output=True,
        )
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-subj",
                "/CN=quota.example",
                "-addext",
                "subjectAltName=DNS:quota.example",
            ],
            check=True,
            capture_output=True,
        )

        class Handler(BaseHTTPRequestHandler):
            def serve_git(self) -> None:
                parsed = urlsplit(self.path)
                env = dict(
                    os.environ,
                    GIT_PROJECT_ROOT=str(tmp_path),
                    GIT_HTTP_EXPORT_ALL="1",
                    PATH_INFO=parsed.path,
                    QUERY_STRING=parsed.query,
                    REQUEST_METHOD=self.command,
                    CONTENT_TYPE=self.headers.get("Content-Type", ""),
                    HTTP_GIT_PROTOCOL=self.headers.get("Git-Protocol", ""),
                )
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                response = subprocess.run(
                    ["git", "http-backend"], env=env, input=body, capture_output=True, check=True
                ).stdout
                headers, _, data = response.partition(b"\r\n\r\n")
                parsed_headers = [header.partition(b":") for header in headers.split(b"\r\n")]
                status = next(
                    (int(value.split()[0]) for name, _, value in parsed_headers if name.lower() == b"status"), 200
                )
                self.send_response(status)
                for name, _, value in parsed_headers:
                    if name.lower() not in {b"status", b"content-length"}:
                        self.send_header(name.decode(), value.strip().decode())
                # A length-delimited TLS response does not depend on close_notify
                # behavior of the local test server or the client TLS backend.
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = serve_git
            do_POST = serve_git

            def log_message(self, *_args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"https://quota.example:{server.server_port}/repo.git"
        monkeypatch.setenv("GIT_SSL_CAINFO", str(cert))
        monkeypatch.setattr(
            safe_fetch, "_destination", AsyncMock(return_value=(url, f"quota.example:{server.server_port}:127.0.0.1"))
        )
        original_run = safe_fetch._run
        loose_failures = []

        async def run(command: list[str], env: dict[str, str], *, storage_fd: int | None = None) -> None:
            # Exercise both pack retention and unpack-objects under the same quota.
            if "fetch" in command:
                command[1:1] = ["-c", f"fetch.unpackLimit={10000 if mode == 'loose' else 1}"]
            try:
                await original_run(command, env, storage_fd=storage_fd)
            except RuntimeError:
                if mode == "loose" and "fetch" in command:
                    objects = Path(env["TMPDIR"]) / ".git" / "objects"
                    assert any(objects.glob("[0-9a-f][0-9a-f]/*")), "Fetch must expand actual loose objects"
                    loose_failures.append(True)
                raise

        monkeypatch.setattr(safe_fetch, "_run", run)

        async def fetch() -> None:
            async with safe_fetch.git_snapshot(url, "HEAD"):
                pytest.fail("Oversized snapshot must fail")

        # Positive control: the same production transport can fetch a bounded snapshot.
        async with safe_fetch.git_snapshot(url, "HEAD" if mode == "transport" else "small") as checkout:
            assert (checkout / ".git").is_dir()
            if mode == "transport":
                assert (checkout / "large").read_bytes() == b"x"
        assert not list(mount.iterdir())
        if mode == "transport":
            return
        if mode == "shared":
            # Each snapshot fits alone; retaining one must constrain the next.
            async with safe_fetch.git_snapshot(url, "HEAD") as first:
                assert (first / "large").stat().st_size == 3 * 1024 * 1024
                with pytest.raises(RuntimeError, match="Git fetch failed"):
                    async with safe_fetch.git_snapshot(url, "HEAD"):
                        pytest.fail("Two individually valid snapshots must share one budget")
                assert (first / "large").stat().st_size == 3 * 1024 * 1024
            assert not list(mount.iterdir())
            async with safe_fetch.git_snapshot(url, "HEAD") as recovered:
                assert (recovered / "large").stat().st_size == 3 * 1024 * 1024
            assert not list(mount.iterdir())
            return
        results = await asyncio.gather(fetch(), fetch(), return_exceptions=True)
        assert all(isinstance(value, RuntimeError) for value in results), results
        # Checkout writes can report only "unable to write file" without errno.
        assert all(
            "storage quota exhausted" in str(value) or (mode == "checkout" and "stage checkout" in str(value))
            for value in results
        ), results
        if mode == "loose":
            assert len(loose_failures) == 2, "Both fetches must exhaust inodes during loose-object expansion"
        stats = os.statvfs(mount)
        assert stats.f_blocks * stats.f_frsize <= 4 * 1024 * 1024
        assert not list(mount.iterdir())
    finally:
        if server:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        if worker:
            worker.join()
        if mode != "transport":
            subprocess.run([*privileged_mount, "umount", str(mount)], check=True, capture_output=True)


async def test_real_https_snapshot_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete HTTPS fetch and checkout works without mount privilege.

    Args:
        tmp_path: Source and checkout directories.
        monkeypatch: Transport fixture configuration.
    """
    await _exercise_https_snapshot(tmp_path, monkeypatch, "transport")


@pytest.mark.parametrize("mode", ["packed", "checkout", "loose", "shared"])  # type: ignore[untyped-decorator]
async def test_concurrent_snapshots_enforce_kernel_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Kernel limits bound objects, checkout expansion and overlapping snapshots.

    Args:
        tmp_path: Sources and private mountpoint.
        monkeypatch: Deployment and DNS overrides.
        mode: Storage stage or overlapping aggregate budget to stress.
    """
    await _exercise_https_snapshot(tmp_path, monkeypatch, mode)
