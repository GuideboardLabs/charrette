"""The boundaries, in one place.

Three of them, and they are the only three the plugin crosses:

  a name from a client that becomes a file  (valid_src, image_path)
  a URL from a model that becomes a request (safe_url, fetch)
  a path from a model that becomes a read   (read_import)

Each is a predicate plus the one helper that uses it, so the call sites cannot drift
apart. Everything here refuses rather than repairs: a bad name is an error, not a
sanitised name, because a silent fix teaches the caller that anything works.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# what `_save` produces, and the only shape the store, the web server, the dashboard, the
# export and the replay page will accept as an image name
SRC_RE = re.compile(r"^[0-9a-f]{16}\.(png|jpg|webp)$")

MAX_REDIRECTS = 5


def valid_src(src: object) -> bool:
    """A stored image name. Not a path, not a URL, not a traversal: sixteen hex and an extension."""
    return isinstance(src, str) and bool(SRC_RE.match(src))


def image_path(root, src: object) -> Path | None:
    """The file for a stored image name, or None. Nothing outside <root>/images is reachable."""
    if not valid_src(src):
        return None
    base = (Path(root) / "images")
    path = base / str(src)
    try:
        # belt and braces: the name is already constrained, so this can only fail if the
        # directory itself was replaced by a symlink out of the tree
        if path.resolve().parent != base.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    return path


# ---------------------------------------------------------------- network


def _host_is_public(host: str, port: int) -> bool:
    """Every address a name resolves to must be globally routable. One private answer blocks it."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        addr = str(info[4][0])
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def safe_url(url: str) -> bool:
    """Is this URL fetchable without reaching the box's own network?

    Hermes publishes `tools.url_safety.is_safe_url`, which is the authority when the plugin
    runs inside the host. The fallback exists because the test suite runs outside it, and a
    boundary that is only checked in production is not checked.
    """
    try:
        from tools.url_safety import is_safe_url  # type: ignore

        return bool(is_safe_url(url))
    except Exception:
        pass
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return _host_is_public(parts.hostname, port)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by hand, so each hop is a decision rather than a courtesy."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 60,
          ua: str = "charrette/0.3") -> bytes:
    """GET the URL only if every hop is public. Raises ValueError on a blocked target."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if not safe_url(current):
            raise ValueError(f"refused: {urllib.parse.urlsplit(current).hostname or current} is not a public host")
        req = urllib.request.Request(current, data=data, headers={"User-Agent": ua, **(headers or {})})
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                nxt = exc.headers.get("Location") or ""
                current = urllib.parse.urljoin(current, nxt)
                data = None if exc.code in (301, 302, 303) else data
                continue
            raise
    raise ValueError("refused: too many redirects")


# ---------------------------------------------------------------- local files


def hermes_home() -> Path:
    """The active profile's home. `hermes_constants` is the published way to ask."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home()).expanduser()
    except Exception:
        return Path("~/.hermes").expanduser()


def secret(name: str) -> str:
    """A per-profile secret. `agent.secret_scope` is the published store; env is the fallback."""
    try:
        from agent.secret_scope import get_secret  # type: ignore

        got = get_secret(name)
        if got:
            return got
    except Exception:
        pass
    return os.environ.get(name, "")


def _under(path: Path, parent: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def import_dir(root) -> Path:
    """Where a human drops a file they want on the board. Nothing else is readable."""
    env = os.environ.get("CHARRETTE_IMPORT_DIR")
    return Path(env).expanduser() if env else Path(root) / "imports"


def read_import(path: str, root, max_bytes: int = 25 * 1024 * 1024) -> tuple[bytes, str]:
    """Read a local image, refusing anything outside the import directory.

    Returns (bytes, mime). The file must decode as an image; a text file renamed .png is not one.
    Dot-directories and the Hermes home are refused even when the import directory is set to
    something unwise, because "the user set it" is not a reason to read their credentials.
    """
    raw = str(path or "").strip()
    if not raw or raw.startswith("file://"):
        raw = raw[len("file://"):] if raw.startswith("file://") else raw
    if not raw:
        raise ValueError("no path given")
    p = Path(raw).expanduser()
    try:
        resolved = p.resolve(strict=True)
    except OSError:
        raise ValueError(f"no such file: {raw}") from None
    base = import_dir(root).resolve()
    try:
        rel = resolved.relative_to(base)
    except ValueError:
        raise ValueError(f"refused: images must sit in {base}") from None
    # only the part the user controls: an ancestor called .hermes is where the app lives, not a
    # place anyone is smuggling a file through
    if any(part.startswith(".") for part in rel.parts):
        raise ValueError("refused: dot-directories are not importable")
    # The Hermes home is never an import location, with one exception: the plugin's own data
    # directory lives inside it, and that is where imports point by default. Anything else under
    # the home (a credentials file, a secret directory) is unreachable by configuration.
    home = hermes_home()
    data = Path(root).expanduser()
    if _under(base, home) and not _under(base, data):
        raise ValueError("refused: the Hermes home is not an image directory")
    if not resolved.is_file():
        raise ValueError(f"not a file: {raw}")
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(f"refused: {size} bytes is larger than {max_bytes}")
    blob = resolved.read_bytes()
    mime = sniff_mime(blob)
    if not mime:
        raise ValueError("refused: does not decode as a PNG, JPEG or WebP image")
    return blob, mime


def sniff_mime(blob: bytes) -> str:
    """Format from the bytes, not from the name. Returns '' when it is not an image we store."""
    if not blob:
        return ""
    try:
        from PIL import Image  # the renderer already depends on Pillow

        import io

        with Image.open(io.BytesIO(blob)) as im:
            im.verify()
        with Image.open(io.BytesIO(blob)) as im:
            fmt = (im.format or "").upper()
    except Exception:
        return ""
    return {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(fmt, "")
