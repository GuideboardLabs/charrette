"""Image sourcing for the board.

Three ways an image gets onto the board, because a user may have none, one, or
all of them:

  search   -> Wikimedia Commons (no key, direct file URLs, deterministic)
  generate -> the local pipeline if configured (OpenAI-shaped /v1/images/...),
              else Hermes's own image_generate tool via ctx.dispatch_tool
  edit     -> the local pipeline's /v1/images/edits with ref_images, i.e.
              "turn the thing I just drew into a photo"

Config resolves in this order: env, then <data>/config.json.
  CHARRETTE_IMAGE_ENDPOINT   e.g. http://127.0.0.1:8191
  CHARRETTE_IMAGE_KEY        bearer token
  CHARRETTE_IMAGE_MODEL      optional label only
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import safety

UA = "charrette-hermes-plugin/0.1 (+https://github.com/GuideboardLabs/charrette)"
COMMONS = "https://commons.wikimedia.org/w/api.php"


class ImageError(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def config(root: Path) -> dict:
    cfg = {"endpoint": safety.secret("CHARRETTE_IMAGE_ENDPOINT"),
           "key": safety.secret("CHARRETTE_IMAGE_KEY"),
           "model": os.environ.get("CHARRETTE_IMAGE_MODEL", ""),
           "steps": os.environ.get("CHARRETTE_IMAGE_STEPS", "")}
    try:
        disk = json.loads((root / "config.json").read_text(encoding="utf-8"))
        for k, v in (disk.get("image") or {}).items():
            if not cfg.get(k):
                cfg[k] = v
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def default_steps(root: Path) -> int | None:
    """Demo-speed knob. A sketch needs 2-4 steps; the gateway's own default is 6."""
    raw = config(root).get("steps")
    try:
        return max(1, min(20, int(raw))) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search(query: str, dest: Path, limit: int = 5) -> dict:
    """Find a real image for `query` and store it. Returns {src, source_url, title}."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(limit, 10))),
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "iiurlwidth": "1024",
    }
    url = COMMONS + "?" + urllib.parse.urlencode(params)
    try:
        raw = json.loads(_http(url, timeout=20).decode("utf-8"))
    except Exception as exc:  # network
        raise ImageError("E_SEARCH", f"search failed: {exc}") from exc
    pages = list(((raw.get("query") or {}).get("pages") or {}).values())
    pages.sort(key=lambda p: p.get("index", 99))
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        candidate = info.get("thumburl") or info.get("url")
        mime = info.get("mime") or "image/jpeg"
        if not candidate or not mime.startswith("image/"):
            continue
        try:
            blob = _http(candidate, timeout=45)
        except Exception:
            continue
        if len(blob) < 2048:
            continue
        return _save(blob, dest, page.get("title") or query, candidate, mime)
    raise ImageError("E_SEARCH", f"no usable image for {query!r}")


def _save(blob: bytes, dest: Path, title: str, source_url: str, mime: str = "image/png") -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".png")
    name = hashlib.sha256(blob).hexdigest()[:16] + ext
    (dest / name).write_bytes(blob)
    return {"src": name, "source_url": source_url, "title": title[:80], "bytes": len(blob)}


def _b64_to_src(resp: dict, dest: Path, title: str, source: str) -> dict:
    data = (resp or {}).get("data") or []
    if not data or not data[0].get("b64_json"):
        raise ImageError("E_IMAGE", "pipeline returned no image data")
    blob = base64.b64decode(data[0]["b64_json"])
    out = _save(blob, dest, title, source, "image/png")
    out["seconds"] = resp.get("generation_seconds")
    out["size"] = resp.get("size")
    return out


def _local_call(cfg: dict, path: str, body: dict, timeout: int = 1800) -> dict:
    endpoint = (cfg.get("endpoint") or "").rstrip("/")
    if not endpoint:
        raise ImageError("E_NO_PIPELINE", "no image endpoint configured (CHARRETTE_IMAGE_ENDPOINT)")
    headers = {"Content-Type": "application/json"}
    if cfg.get("key"):
        headers["Authorization"] = "Bearer " + cfg["key"]
    req = urllib.request.Request(
        endpoint + path, data=json.dumps(body).encode("utf-8"), headers={"User-Agent": UA, **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise ImageError("E_PIPELINE", f"{path} -> {exc.code}: {detail}") from exc
    except Exception as exc:
        raise ImageError("E_PIPELINE", f"{path} unreachable: {exc}") from exc


def generate(prompt: str, dest: Path, root: Path, size: str | None = None,
             dispatch=None, steps: int | None = None) -> dict:
    cfg = config(root)
    if cfg.get("endpoint"):
        body = {"prompt": prompt, "n": 1}
        if size:
            body["size"] = size
        if steps:
            body["steps"] = int(steps)
        resp = _local_call(cfg, "/v1/images/generations", body)
        return _b64_to_src(resp, dest, prompt, cfg["endpoint"])
    if dispatch is not None:
        try:
            res = dispatch("image_generate", {"prompt": prompt})
        except Exception as exc:
            raise ImageError("E_IMAGE", f"image_generate failed: {exc}") from exc
        blob, src = _from_tool_result(res, root)
        return _save(blob, dest, prompt, src, "image/png")
    raise ImageError("E_NO_PIPELINE", "no image pipeline: set CHARRETTE_IMAGE_ENDPOINT or enable image_generate")


def edit(prompt: str, ref_png: bytes, dest: Path, root: Path, size: str | None = None,
         steps: int | None = None, dispatch=None) -> dict:
    """img2img: use the human's own drawing as the reference.

    Two backends, in order: a local pipeline that speaks /v1/images/edits with
    ref_images, then Hermes's own image_generate with a reference image, which
    is what any user with a FAL-style image model already has.
    """
    cfg = config(root)
    if not cfg.get("endpoint"):
        if dispatch is not None:
            data_url = "data:image/png;base64," + base64.b64encode(ref_png).decode("ascii")
            try:
                res = dispatch("image_generate", {"prompt": prompt, "image_url": data_url})
            except Exception as exc:
                raise ImageError("E_NO_EDIT", f"image_generate refused the reference: {exc}") from exc
            blob, src = _from_tool_result(res, root)
            return _save(blob, dest, prompt, src, "image/png")
        raise ImageError("E_NO_EDIT", "editing needs a local image pipeline (CHARRETTE_IMAGE_ENDPOINT) "
                                      "or an image model that accepts reference images")
    body = {"prompt": prompt, "ref_images": ["data:image/png;base64," + base64.b64encode(ref_png).decode("ascii")]}
    if size:
        body["size"] = size
    if steps:
        body["steps"] = int(steps)
    resp = _local_call(cfg, "/v1/images/edits", body)
    return _b64_to_src(resp, dest, prompt, cfg["endpoint"])


def _from_tool_result(res, root: Path) -> tuple[bytes, str]:
    """Hermes tool results vary; accept a path, a file:// URL, an http URL, or JSON."""
    text = res if isinstance(res, str) else json.dumps(res)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    candidates = []
    if isinstance(parsed, dict):
        for key in ("path", "file", "image_path", "url", "image_url", "image", "image_path"):
            if isinstance(parsed.get(key), str):
                candidates.append(parsed[key])
        for key in ("images", "outputs", "data"):
            val = parsed.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        candidates.append(item)
                    elif isinstance(item, dict):
                        for k2 in ("path", "url", "image", "b64_json"):
                            if isinstance(item.get(k2), str):
                                candidates.append(item[k2])
    candidates += [w.strip("\"'[](),") for w in text.split() if w.startswith(("http", "/", "file:"))]
    for cand in candidates:
        if cand.startswith("data:image/") and ";base64," in cand:
            return base64.b64decode(cand.split(",", 1)[1]), "inline"
        if cand.startswith("http"):
            return safety.fetch(cand, timeout=60, ua=UA), cand
        # a path lifted out of a tool result is not a path the user chose: it is refused here
        # and has to go through board_image source=path, which checks the import directory
        if cand.startswith(("file:", "/", "~")):
            raise ImageError("E_ARGS", "image_generate returned a local path; put the file in the "
                                       "import directory and use source=path")
    raise ImageError("E_IMAGE", "image_generate returned no usable path or URL")


def fetch_url(url: str, dest: Path) -> dict:
    """An image from a URL the model supplied. Private, loopback and link-local hosts are refused."""
    if not url.lower().startswith(("http://", "https://")):
        raise ImageError("E_ARGS", "url must be http(s)")
    try:
        blob = safety.fetch(url, timeout=45, ua=UA)
    except ValueError as exc:
        raise ImageError("E_URL", str(exc)) from None
    mime = safety.sniff_mime(blob) or "image/png"
    return _save(blob, dest, Path(urllib.parse.urlsplit(url).path).name or "image", url, mime)


def read_path(path: str, dest: Path) -> dict:
    """An image the user already has. Only the import directory, and only if it decodes."""
    try:
        blob, mime = safety.read_import(path, dest.parent.parent)
    except ValueError as exc:
        raise ImageError("E_ARGS", str(exc)) from None
    return _save(blob, dest, Path(path).name, str(Path(path).expanduser()), mime)
