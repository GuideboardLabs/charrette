"""Charrette plugin entrypoint.

Three doors, all pointing at the same board document:

  tools        the agent draws, points and places images
  web surface  the plugin's own server, so anyone who installs it has a board
  desktop pane the native pane beside the chat
"""

from __future__ import annotations

import time
import webbrowser

from .board.store import DEFAULT_BOARD, Store
from .tools import SCHEMAS, make_handlers


def _serve(store: Store, port: int = 0, open_browser: bool = False) -> str:
    from .web.server import ensure_running

    server = ensure_running(store, port=port)
    url = server.url
    if open_browser:
        webbrowser.open(url)
    return url


def _cli_setup(parser):
    sub = parser.add_subparsers(dest="charrette_cmd")
    p_open = sub.add_parser("open", help="start the board server and print its URL")
    p_open.add_argument("--port", type=int, default=0, help="0 picks a free port")
    p_open.add_argument("--open", action="store_true", help="open it in your browser")
    sub.add_parser("status", help="where the board lives")


def _cli_handler(args):
    store = Store()
    store.ensure_board(DEFAULT_BOARD, "board 1")
    if getattr(args, "charrette_cmd", None) == "status":
        print(f"data: {store.root}")
        for board in store.index()["boards"]:
            last = (board.get("last_activity") or {}).get("object_id") or "-"
            print(f"  {board['id']:<4} {board['name'][:28]:<28} rev {board['rev']:<5} "
                  f"{board['objects']} objects, newest {last}")
        return
    url = _serve(store, port=getattr(args, "port", 0), open_browser=getattr(args, "open", False))
    print(url, flush=True)
    print("serving; ctrl-c to stop", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def register(ctx):
    store = Store()
    store.ensure_board(DEFAULT_BOARD, "board 1")

    def dispatch(name, args):
        return ctx.dispatch_tool(name, args)

    try:
        llm = ctx.llm
    except Exception:  # no host LLM route in this context
        llm = None

    handlers = make_handlers(store, dispatch=dispatch, llm=llm)

    for schema in SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="charrette",
            schema=schema,
            handler=handlers[schema["name"]],
        )

    ctx.register_cli_command(
        name="charrette",
        help="open the charrette whiteboard in your browser",
        setup_fn=_cli_setup,
        handler_fn=_cli_handler,
    )

    def board_command(raw_args: str) -> str:
        url = _serve(store)
        # Put the board where the user is looking: the pane beside the chat,
        # for the window that asked. A TUI or a gateway without desktop UI
        # still gets the link below, so this is best-effort by design.
        try:
            ctx.dispatch_tool("desktop_preview", {"url": url, "label": "The board"})
        except Exception:
            pass
        return f"the board is open on the right: {url}"

    ctx.register_command("board", board_command, description="open the shared whiteboard")
