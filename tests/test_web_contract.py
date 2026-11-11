from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ChatPanelSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text()
        cls.js = (ROOT / "web" / "app.js").read_text()
        cls.css = (ROOT / "web" / "app.css").read_text()
        cls.server = (ROOT / "web" / "server.py").read_text()

    def test_chat_log_announces_streamed_updates_politely(self):
        self.assertIn('id="chatLog"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-relevant="additions text"', self.html)

    def test_empty_chat_is_an_invitation_state_instead_of_a_message_placeholder(self):
        self.assertIn('id="chatEmpty"', self.html)
        self.assertIn('class="chat-empty"', self.html)
        self.assertIn('Start with a mark', self.html)

    def test_thinking_state_has_pulse_markup_and_streaming_caret_styles(self):
        self.assertIn('id="chatThinking"', self.html)
        self.assertIn('class="thinking-dots"', self.html)
        self.assertIn('.chat-thinking', self.css)
        self.assertIn('.stream-caret', self.css)

    def test_busy_send_control_posts_stop_and_keeps_reader_for_acknowledgement(self):
        self.assertIn('function chatStop', self.js)
        self.assertIn("api('/api/session/stop'", self.js)
        self.assertNotIn('if (chatBusy) {\n    chatController.abort()', self.js)
        self.assertIn("setChatBusy(true)", self.js)
        self.assertIn("setChatBusy(false)", self.js)
        self.assertIn("'Stop response'", self.js)

    def test_server_exposes_stop_route_and_cancels_on_stream_disconnect(self):
        self.assertIn('path == "/api/session/stop"', self.server)
        self.assertIn('self.app.chat.cancel(bid)', self.server)
        self.assertIn('on_disconnect', self.server)

    def test_stopped_is_a_designed_terminal_chat_state(self):
        self.assertIn("evt.type === 'stopped'", self.js)
        self.assertIn("className = 'chat-stopped'", self.js)
        self.assertIn('.chat-stopped', self.css)

    def test_error_card_exposes_reason_and_retry_without_inner_html(self):
        self.assertIn('className = \'chat-error\'', self.js)
        self.assertIn("retry.textContent = 'Retry'", self.js)
        self.assertIn('reason.textContent =', self.js)
        self.assertNotIn("chatMessage('error'", self.js)

    def test_consecutive_tool_calls_are_grouped_in_one_cluster(self):
        self.assertIn("className = 'chat-tool-group'", self.js)
        self.assertIn('activeToolGroup', self.js)
        self.assertIn('.chat-tool-group', self.css)

    def test_messages_display_timestamps_and_long_code_can_scroll(self):
        self.assertIn("className = 'chat-time'", self.js)
        self.assertIn("document.createElement('time')", self.js)
        self.assertIn('.chat-msg pre', self.css)
        self.assertIn('overflow-x: auto', self.css)

    def test_scroll_follows_only_when_reader_was_already_near_bottom(self):
        self.assertIn('isNearBottom(chatLog)', self.js)
        self.assertIn('function preserveChatScroll', self.js)
        self.assertIn('if (follow) chatScroll()', self.js)

    def test_keyboard_contract_closes_chat_and_keeps_ctrl_j_toggle(self):
        self.assertIn("e.key === 'Escape'", self.js)
        self.assertIn('chatTogglePanel(false)', self.js)
        self.assertIn("e.key === 'j' && (e.ctrlKey || e.metaKey)", self.js)

    def test_focus_visible_and_six_line_textarea_rules_exist(self):
        self.assertIn(':focus-visible', self.css)
        self.assertIn('max-height: 132px', self.css)
        self.assertIn('overflow-y: auto', self.css)

    def test_chat_holds_as_column_at_1240_and_overlay_at_380(self):
        self.assertIn('flex: 0 0 340px', self.css)
        self.assertIn('@media (max-width: 600px)', self.css)
        self.assertIn('inset: 0', self.css)
        self.assertIn('min-width: 0', self.css)
        self.assertIn('width: 100%', self.css)

    def test_polling_stops_while_hidden_and_restarts_once_with_an_immediate_load(self):
        self.assertIn("document.addEventListener('visibilitychange'", self.js)
        self.assertIn('if (document.hidden) stopPolling()', self.js)
        self.assertIn("window.addEventListener('focus', startPolling)", self.js)
        self.assertIn('if (pollTimer !== null) return', self.js)
        self.assertIn('load()', self.js)

    def test_zoom_readout_tracks_loaded_wheel_and_committed_camera_state(self):
        self.assertIn('id="zoom"', self.html)
        self.assertIn('function updateZoom', self.js)
        self.assertIn('updateZoom(doc.camera)', self.js)
        self.assertIn('onCamera: camera =>', self.js)
        self.assertIn("Math.round(z * 100) + '%'", self.js)

    def test_minimap_and_directional_activity_control_are_semantic(self):
        self.assertIn('id="minimap"', self.html)
        self.assertIn('aria-label="Board map', self.html)
        self.assertIn('id="activityJump"', self.html)
        self.assertIn('<button id="activityJump"', self.html)
        self.assertIn('directionToBounds', self.js)
        self.assertIn('board.setCamera(', self.js)
        self.assertIn('"/minimap.mjs"', self.server)

    def test_empty_board_invitation_replaces_persistent_instruction_pill(self):
        self.assertIn('id="emptyBoard"', self.html)
        self.assertIn('id="emptyChat"', self.html)
        self.assertIn('id="emptyShortcuts"', self.html)
        self.assertNotIn('draw. the name at top left switches boards', self.html)
        self.assertIn("emptyBoard.hidden = n > 0", self.js)
        self.assertIn("minimap.hidden = n === 0", self.js)
        self.assertRegex(self.css, r'\.empty-actions kbd\s*\{[^}]*border:')

    def test_renderer_owns_distinct_tool_cursors_and_camera_mutation(self):
        engine = (ROOT / "renderer" / "charrette.mjs").read_text()
        self.assertIn('const TOOL_CURSORS', engine)
        self.assertIn("text: 'text'", engine)
        self.assertIn("hand: 'grab'", engine)
        self.assertIn('setCamera(camera, notify = true)', engine)
        self.assertIn('opts.onCamera', engine)

    def test_map_empty_and_activity_controls_keep_real_hit_targets(self):
        self.assertIn('.minimap', self.css)
        self.assertIn('.activity-jump', self.css)
        self.assertIn('.empty-board', self.css)
        self.assertRegex(self.css, r'\.activity-jump\s*\{[^}]*min-height:\s*28px')
        self.assertIn('.activity-jump:focus-visible', self.css)
        self.assertRegex(self.css, r'\.swatch\s*\{[^}]*width:\s*28px;\s*height:\s*28px')
        self.assertRegex(self.css, r'\.ghost, \.tiny\s*\{[^}]*min-width:\s*28px;\s*min-height:\s*28px')
        self.assertIn('.palette, #fit { display: none; }', self.css)

    def test_server_serves_dependency_free_chat_module(self):
        self.assertIn('"/chat-ui.mjs"', self.server)
        self.assertIn('"text/javascript; charset=utf-8"', self.server)


if __name__ == "__main__":
    unittest.main()
