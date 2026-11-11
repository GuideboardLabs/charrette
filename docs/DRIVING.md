# Driving the board from a chat

The page follows the board, not the other way round. Two channels let a conversation
outside the page act on what the reader is looking at.

## 1. The director channel

The page polls `GET /api/control?since=<seq>` every 700 ms and performs whatever the
queue holds. Anything that can reach the server with the token can push a cue:

```
POST /api/control      {"cmd": "board",  "board": "b6"}          open (or switch to) a board
                       {"cmd": "replay", "ids": ["obj_1", ...], "speed": 2}
                       {"cmd": "replay", "ids": [...], "focus": false}   play without moving the camera
                       {"cmd": "focus",  "ids": [...]}          frame objects, no playback
                       {"cmd": "stop"}                          stop playback, hide the bar
```

A freshly opened page adopts the queue head and does not replay old cues. The queue is
last-40 and lives in `~/.hermes/charrette/control.json`; the page ignores a cue it has
already performed, because it tracks `seq`.

## 2. Ambient ink

When a document arrives carrying agent objects the page has not seen, it draws them in
with the hand instead of popping them in, provided the reader is idle, the tab is visible
and no replay is running. The camera does not move for ambient ink: it appears where it
was drawn, in view or not.

An object is "agent" when its `actor` is `agent`; the human's own strokes never animate.

## 3. The board follows the server

Every fourth director tick (about 2.8 s) the page asks `/api/boards` which board is active
and switches if it differs. So creating a board over HTTP, from a script or a chat turn,
is enough for the open page to go there, even if no cue was sent.
