from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ToolbarSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text()
        cls.js = (ROOT / "web" / "app.js").read_text()
        cls.css = (ROOT / "web" / "app.css").read_text()
        cls.pane = (ROOT / "desktop" / "pane.js").read_text()
        cls.bundle = (ROOT / "desktop" / "plugin.js").read_text()

    def test_web_tool_buttons_use_inline_current_color_svg_icons(self):
        tool_buttons = re.findall(r'<button class="tool[^>]*>[\s\S]*?</button>', self.html)
        self.assertEqual(len(tool_buttons), 15)
        self.assertTrue(all('<svg' in button for button in tool_buttons))
        self.assertTrue(all('width="18"' in button and 'height="18"' in button for button in tool_buttons))
        self.assertTrue(all('stroke="currentColor"' in button for button in tool_buttons))
        self.assertNotRegex(self.html, r'&#(?:9655|10021|9998|9636|9003|9647|9711|9671|9651|9585|8599|9638);')

    def test_tool_targets_icons_and_strokes_share_one_measured_weight(self):
        self.assertRegex(self.css, r'\.tool\s*\{[^}]*width:\s*30px;\s*height:\s*30px')
        self.assertRegex(self.css, r'\.tool svg\s*\{[^}]*width:\s*18px;\s*height:\s*18px')
        self.assertIn('stroke-width="1.75"', self.html)
        self.assertNotIn('stroke-width="2.5"', self.html)

    def test_every_tool_has_a_named_shortcut_tooltip(self):
        tool_buttons = re.findall(r'<button class="tool[^>]*>', self.html)
        self.assertTrue(all('aria-label=' in button for button in tool_buttons))
        self.assertTrue(all('data-tip=' in button for button in tool_buttons))
        self.assertIn('content: attr(data-tip)', self.css)
        self.assertIn('.tool:hover::after', self.css)
        self.assertIn('.tool:focus-visible::after', self.css)

    def test_tool_groups_hover_active_and_focus_states_are_explicit(self):
        self.assertGreaterEqual(self.html.count('class="sep"'), 3)
        self.assertIn('.tool:hover', self.css)
        self.assertIn('.tool.on', self.css)
        self.assertIn('.tool:focus-visible', self.css)
        self.assertIn('color: var(--teal)', self.css)

    def test_shortcut_sheet_is_an_accessible_dialog_toggled_by_question_mark(self):
        self.assertIn('id="shortcutSheet"', self.html)
        self.assertIn('role="dialog"', self.html)
        self.assertIn('aria-modal="true"', self.html)
        self.assertIn('id="shortcutHelp"', self.html)
        self.assertIn('aria-expanded="false"', self.html)
        self.assertIn("key === '?'", self.js)
        self.assertIn('toggleShortcutSheet()', self.js)
        self.assertIn('closeShortcutSheet()', self.js)

    def test_open_shortcut_sheet_contains_focus_and_blocks_background_bindings(self):
        self.assertIn("shortcutHelp.setAttribute('aria-expanded', 'true')", self.js)
        self.assertIn("shortcutHelp.setAttribute('aria-expanded', 'false')", self.js)
        self.assertIn("if (!shortcutSheet.hidden) return", self.js)
        self.assertIn("if (e.key === 'Tab')", self.js)
        self.assertIn('shortcutClose.focus()', self.js)

    def test_shortcut_sheet_lists_every_existing_and_panel_binding(self):
        required = ['V', 'H', 'P', 'M', 'E', 'R', 'O', 'D', 'G', 'L', 'A', 'B', 'C',
                    'Shift+C', 'T', 'F', 'Ctrl/Cmd+Z', 'Delete/Backspace', '1–5',
                    'Ctrl/Cmd+J', 'Enter', 'Shift+Enter', 'Escape', '?']
        for binding in required:
            with self.subTest(binding=binding):
                self.assertIn(f'<kbd>{binding}</kbd>', self.html)

    def test_drawing_shortcuts_do_not_fire_while_typing(self):
        self.assertIn("closest('input, textarea, [contenteditable=\"true\"]')", self.js)
        self.assertIn('if (typing) return', self.js)
        self.assertIn("key === 'j' && (e.ctrlKey || e.metaKey)", self.js)

    def test_desktop_rail_uses_same_svg_size_weight_and_current_color(self):
        self.assertIn('function toolIcon', self.pane)
        self.assertIn("width: '18'", self.pane)
        self.assertIn("height: '18'", self.pane)
        self.assertIn("stroke: 'currentColor'", self.pane)
        self.assertIn("strokeWidth: '1.75'", self.pane)
        self.assertIn("width: '30px'", self.pane)
        self.assertIn("height: '30px'", self.pane)
        self.assertNotRegex(self.pane, r"['\"](?:▹|✥|✎|▤|⌫|▭|◯|◇|△|↗|▦|▥)['\"]")

    def test_generated_desktop_bundle_contains_svg_rail_and_no_retired_glyphs(self):
        self.assertIn('function toolIcon', self.bundle)
        self.assertIn("stroke: 'currentColor'", self.bundle)
        self.assertNotRegex(self.bundle, r"['\"](?:▹|✥|✎|▤|⌫|▭|◯|◇|△|↗|▦|▥)['\"]")


if __name__ == "__main__":
    unittest.main()
