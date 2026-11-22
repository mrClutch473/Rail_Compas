"""Open a page in a real browser and dump everything it receives: responses, websocket frames,
embedded state, final HTML and a screenshot. Used to figure out where a source keeps its data.

    uv run python -m scraper.recon.probe "https://www.tutu.ru/poezda/..." --source tutu
"""

import argparse
import asyncio
import base64
import json
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from playwright.async_api import Request, Response, WebSocket, async_playwright

from scraper.recon.proxy import mask, parse_proxy, proxies_from_env

ROOT = Path(__file__).resolve().parents[2]

BODY_RESOURCE_TYPES = {"document", "xhr", "fetch", "eventsource", "other"}
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

BLOCK_MARKERS = [
    "smartcaptcha",
    "captcha",
    "antibot challenge",
    "похожи на автоматические",
    "access denied",
    "too many requests",
]

STATE_SCRIPTS = {
    "next_data": "() => document.getElementById('__NEXT_DATA__')?.textContent ?? null",
    "nuxt_data": "() => document.getElementById('__NUXT_DATA__')?.textContent ?? null",
    "nuxt": """() => {
        try { return window.__NUXT__ ? JSON.stringify(window.__NUXT__) : null }
        catch (e) { return null }
    }""",
}


def slugify(value: str, limit: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")[:limit] or "root"


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pretty_body(body: bytes) -> tuple[bytes, str]:
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return body, ""
    return json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8"), "json"


EXTENSIONS = {"json": ".json", "html": ".html", "javascript": ".js", "css": ".css", "xml": ".xml"}


def guess_extension(content_type: str, detected: str) -> str:
    if detected:
        return EXTENSIONS[detected]
    for key, ext in EXTENSIONS.items():
        if key in content_type:
            return ext
    return ".txt" if content_type.startswith("text/") else ".bin"


class Recorder:
    def __init__(self, out_dir: Path, capture_scripts: bool):
        self.out_dir = out_dir
        self.capture_scripts = capture_scripts
        self.responses_dir = out_dir / "responses"
        self.ws_dir = out_dir / "ws"
        self.responses_dir.mkdir(parents=True)
        self.ws_dir.mkdir()

        self.started = time.monotonic()
        self.last_activity = self.started
        self.requests: list[dict] = []
        self.sockets: list[dict] = []
        self.console: list[str] = []
        self._tasks: set[asyncio.Task] = set()

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 3)

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout: float = 10) -> None:
        # long-polling and streaming responses never finish, so don't wait for them forever
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=timeout)
        for task in pending:
            task.cancel()

    def on_response(self, response: Response) -> None:
        self.touch()
        self.spawn(self._save_response(response))

    async def _save_response(self, response: Response) -> None:
        request = response.request
        content_type = (await response.all_headers()).get("content-type", "")
        entry = {
            "n": len(self.requests) + 1,
            "t": self.elapsed(),
            "method": request.method,
            "url": response.url,
            "status": response.status,
            "type": request.resource_type,
            "content_type": content_type,
        }
        if request.method != "GET" and request.post_data:
            entry["post_data"] = request.post_data
        self.requests.append(entry)

        wanted = request.resource_type in BODY_RESOURCE_TYPES or "json" in content_type
        if request.resource_type == "script" and self.capture_scripts:
            wanted = True
        if not wanted or 300 <= response.status < 400:
            return

        try:
            body = await response.body()
        except Exception as exc:  # body is gone after navigation or for some redirects
            entry["body_error"] = str(exc).splitlines()[0]
            return

        body, detected = pretty_body(body)
        parts = urlsplit(response.url)
        name = f"{entry['n']:04d}_{slugify(parts.hostname or '', 30)}_{slugify(parts.path)}"
        path = self.responses_dir / (name + guess_extension(content_type, detected))
        path.write_bytes(body)
        entry["size"] = len(body)
        entry["body"] = str(path.relative_to(self.out_dir))

    def on_request_failed(self, request: Request) -> None:
        self.requests.append(
            {
                "n": len(self.requests) + 1,
                "t": self.elapsed(),
                "method": request.method,
                "url": request.url,
                "type": request.resource_type,
                "failure": request.failure,
            }
        )

    def on_websocket(self, ws: WebSocket) -> None:
        self.touch()
        n = len(self.sockets) + 1
        host = slugify(urlsplit(ws.url).hostname or "ws", 40)
        path = self.ws_dir / f"{n:02d}_{host}.jsonl"
        info = {
            "n": n,
            "url": ws.url,
            "opened": self.elapsed(),
            "file": str(path.relative_to(self.out_dir)),
            "sent": 0,
            "received": 0,
        }
        self.sockets.append(info)
        handle = path.open("a", encoding="utf-8")

        def frame(direction: str, payload: str | bytes) -> None:
            self.touch()
            info[direction] += 1
            record = {"t": self.elapsed(), "dir": direction, "size": len(payload)}
            if isinstance(payload, bytes):
                record["b64"] = base64.b64encode(payload).decode()
            else:
                record["text"] = payload
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

        def closed(_ws) -> None:
            info["closed"] = self.elapsed()
            handle.close()

        ws.on("framesent", lambda payload: frame("sent", payload))
        ws.on("framereceived", lambda payload: frame("received", payload))
        ws.on("close", closed)
        ws.on("socketerror", lambda error: info.setdefault("errors", []).append(error))

    def on_console(self, message) -> None:
        self.console.append(f"[{self.elapsed():8.3f}] {message.type}: {message.text}")


async def settle(recorder: Recorder, wait: float, quiet: float, max_extra: float) -> None:
    """Wait at least `wait` seconds, then keep waiting while traffic continues (up to max_extra)."""
    deadline = recorder.started + wait
    hard_deadline = deadline + max_extra
    while True:
        now = time.monotonic()
        if now >= hard_deadline:
            return
        if now >= deadline and now - recorder.last_activity >= quiet:
            return
        await asyncio.sleep(0.25)


async def scroll_page(page) -> None:
    for _ in range(12):
        await page.mouse.wheel(0, 900)
        await asyncio.sleep(0.6)


def find_markers(html: str) -> list[str]:
    lowered = html.lower()
    return [marker for marker in BLOCK_MARKERS if marker in lowered]


# Playwright's own Chromium fails to start on some Windows builds (side-by-side assembly error),
# so locally we fall back to the installed Chrome or Edge.
AUTO_CHANNELS = ["chromium", "chrome", "msedge"]


async def launch(pw, args: argparse.Namespace, proxy: dict | None, out_dir: Path):
    channels = AUTO_CHANNELS if args.channel == "auto" else [args.channel]
    errors = []
    for channel in channels:
        if args.fresh:
            profile_dir = Path(tempfile.mkdtemp(prefix="probe-"))
        else:
            profile_dir = ROOT / "data" / "profiles" / channel / args.source
            profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=not args.headed,
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 1366, "height": 768},
                proxy=proxy,
                ignore_https_errors=args.insecure,
                record_har_path=str(out_dir / "page.har"),
                record_har_content="omit",
            )
        except Exception as exc:
            errors.append(f"{channel}: {str(exc).splitlines()[0]}")
            continue
        if errors:
            print("Skipped: " + "; ".join(errors))
        return context, channel, profile_dir
    raise RuntimeError("no browser could be started:\n" + "\n".join(errors))


async def probe(args: argparse.Namespace) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "recon" / args.source / stamp
    out_dir.mkdir(parents=True)

    proxy_url = args.proxy or (
        proxies_from_env()[0] if args.use_env_proxy and proxies_from_env() else None
    )
    proxy = parse_proxy(proxy_url) if proxy_url else None

    recorder = Recorder(out_dir, capture_scripts=args.scripts)
    meta = {
        "url": args.url,
        "source": args.source,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "headless": not args.headed,
        "proxy": mask(proxy_url) if proxy_url else None,
        "blocked_resources": args.block,
    }

    async with async_playwright() as pw:
        context, channel, profile_dir = await launch(pw, args, proxy, out_dir)
        meta["channel"] = channel
        meta["profile"] = "fresh" if args.fresh else str(profile_dir.relative_to(ROOT))
        page = context.pages[0] if context.pages else await context.new_page()

        if args.block:
            await page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in BLOCKED_RESOURCE_TYPES
                    else route.continue_()
                ),
            )

        page.on("response", recorder.on_response)
        page.on("requestfailed", recorder.on_request_failed)
        page.on("websocket", recorder.on_websocket)
        page.on("console", recorder.on_console)

        try:
            response = await page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
            meta["status"] = response.status if response else None
        except Exception as exc:
            meta["goto_error"] = str(exc).splitlines()[0]

        if args.manual:
            print("Interact with the browser window, then press Enter here to dump the page...")
            await asyncio.get_running_loop().run_in_executor(None, input)
        else:
            if args.scroll:
                await scroll_page(page)
            await settle(recorder, args.wait, args.quiet, args.max_extra)

        meta["final_url"] = page.url
        meta["title"] = await page.title()
        html = await page.content()
        (out_dir / "page.html").write_text(html, encoding="utf-8")
        try:
            await page.screenshot(path=str(out_dir / "page.png"), full_page=True, timeout=15_000)
        except Exception as exc:
            meta["screenshot_error"] = str(exc).splitlines()[0]

        state_dir = out_dir / "state"
        state_dir.mkdir()
        meta["state"] = []
        for name, script in STATE_SCRIPTS.items():
            raw = await page.evaluate(script)
            if not raw:
                continue
            body, detected = pretty_body(raw.encode("utf-8"))
            (state_dir / f"{name}{'.json' if detected else '.txt'}").write_bytes(body)
            meta["state"].append(name)

        meta["cookies"] = sorted({c["name"] for c in await context.cookies()})
        meta["block_markers"] = find_markers(html)

        await recorder.drain()
        await context.close()

    if args.fresh:
        shutil.rmtree(profile_dir, ignore_errors=True)

    meta["duration_sec"] = recorder.elapsed()
    write_json(out_dir / "meta.json", meta)
    write_json(out_dir / "requests.json", recorder.requests)
    write_json(out_dir / "sockets.json", recorder.sockets)
    (out_dir / "console.log").write_text("\n".join(recorder.console), encoding="utf-8")

    print_summary(out_dir, meta, recorder)
    return out_dir


def print_summary(out_dir: Path, meta: dict, recorder: Recorder) -> None:
    by_type: dict[str, int] = {}
    for entry in recorder.requests:
        by_type[entry["type"]] = by_type.get(entry["type"], 0) + 1
    saved = [e for e in recorder.requests if "body" in e]
    json_saved = [e for e in saved if e["body"].endswith(".json")]

    print(f"\nDump: {out_dir.relative_to(ROOT)}")
    print(f"Status {meta.get('status')} -> {meta['final_url']}")
    print(f"Title: {meta['title']}")
    if meta.get("goto_error"):
        print(f"Navigation error: {meta['goto_error']}")
    print(
        f"Requests: {len(recorder.requests)} "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    )
    print(f"Saved bodies: {len(saved)} (json: {len(json_saved)})")
    for ws in recorder.sockets:
        print(f"WebSocket #{ws['n']}: {ws['url']}  sent={ws['sent']} received={ws['received']}")
    if meta["state"]:
        print("Embedded state: " + ", ".join(meta["state"]))
    if meta["block_markers"]:
        print("Possible block markers: " + ", ".join(meta["block_markers"]))
    print(f"Took {meta['duration_sec']}s")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Dump everything a page loads, for source recon.")
    parser.add_argument("url")
    parser.add_argument(
        "--source", default="misc", help="subfolder in recon/ and browser profile name"
    )
    parser.add_argument(
        "--wait", type=float, default=15, help="minimum seconds to wait after navigation"
    )
    parser.add_argument(
        "--quiet", type=float, default=3, help="stop after this many seconds without traffic"
    )
    parser.add_argument(
        "--max-extra", type=float, default=20, help="extra seconds allowed while traffic goes on"
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument(
        "--manual", action="store_true", help="headed; dump only after Enter is pressed"
    )
    parser.add_argument("--scroll", action="store_true", help="scroll down to trigger lazy loading")
    parser.add_argument("--block", action="store_true", help="skip images, fonts and media")
    parser.add_argument("--scripts", action="store_true", help="also save JS bundles")
    parser.add_argument(
        "--fresh", action="store_true", help="throwaway profile instead of the persistent one"
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="ignore TLS errors (sites on the Russian national CA, e.g. rzd.ru)",
    )
    parser.add_argument(
        "--channel", default="auto", help="chromium, chrome, msedge or auto (first that starts)"
    )
    parser.add_argument("--proxy", help="scheme://user:pass@host:port")
    parser.add_argument(
        "--use-env-proxy", action="store_true", help="take the first proxy from SCRAPER_PROXIES"
    )
    args = parser.parse_args()
    if args.manual:
        args.headed = True

    asyncio.run(probe(args))


if __name__ == "__main__":
    main()
