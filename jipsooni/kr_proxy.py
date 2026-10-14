"""Optional local forwarder for multi-hop proxies (e.g. tailscale userspace SOCKS -> remote tinyproxy).

Config only (nothing hardcoded): listen URL = JIPSOONI_KR_PROXY or prefs.network.kr_proxy,
upstream chain = JIPSOONI_KR_PROXY_UPSTREAM or prefs.network.kr_proxy_upstream (pproxy -r syntax).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def ensure_forwarder(listen_url: str, upstream: str, log_path: str | None = None) -> str:
    """Returns 'up' | 'started' | 'skipped' | 'failed: ...'."""
    if not listen_url or not upstream:
        return "skipped"
    u = urlparse(listen_url)
    host, port = u.hostname or "127.0.0.1", u.port or 8080
    if _listening(host, port):
        return "up"
    exe = shutil.which("pproxy") or str(Path(sys.executable).with_name("pproxy"))
    if not Path(exe).exists():
        return "failed: pproxy not installed (pip install pproxy)"
    log = open(log_path or os.devnull, "ab")
    subprocess.Popen([exe, "-l", f"http://{host}:{port}", "-r", upstream], stdout=log, stderr=log,
                     start_new_session=True)
    for _ in range(20):
        time.sleep(0.25)
        if _listening(host, port):
            return "started"
    return "failed: forwarder did not start"


def main() -> None:
    from jipsooni.config import Settings
    from jipsooni.prefs import load_prefs
    s = Settings.from_env()
    net = load_prefs(s.prefs_path)["network"]
    listen = s.kr_proxy or net.get("kr_proxy", "")
    upstream = os.getenv("JIPSOONI_KR_PROXY_UPSTREAM") or net.get("kr_proxy_upstream", "")
    print(ensure_forwarder(listen, upstream))


if __name__ == "__main__":
    main()
