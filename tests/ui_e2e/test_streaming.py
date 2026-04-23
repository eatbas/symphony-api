from __future__ import annotations

from playwright.sync_api import Page, expect


class TestStreamingRequest:
    def test_claude_streaming_request(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "claude")
        console_page.select_option("#model", "opus")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "hello from playwright")
        console_page.click("#send-button")

        console_page.wait_for_function(
            "document.getElementById('request-meta').textContent.includes('completed')",
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[completed]" in text or "[terminal]" in text
        assert "claude:hello from playwright" in text

        session_el = console_page.locator("#session-ref")
        expect(session_el).not_to_have_text("none")

        count = int(console_page.locator("#event-count").inner_text())
        assert count >= 3

    def test_gemini_streaming_request(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "gemini")
        console_page.select_option("#model", "gemini-3-flash-preview")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "hello gemini")
        console_page.click("#send-button")

        console_page.wait_for_function(
            "document.getElementById('request-meta').textContent.includes('completed')",
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[terminal]" in text
        assert "gemini:hello gemini" in text

    def test_codex_streaming_request(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "codex")
        console_page.select_option("#model", "gpt-5.4")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "hello codex")
        console_page.click("#send-button")

        console_page.wait_for_function(
            "document.getElementById('request-meta').textContent.includes('completed')",
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[terminal]" in text
        assert "codex:hello codex" in text

    def test_codex_secondary_streaming_request(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "codex")
        console_page.select_option("#model", "gpt-5.2")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "hello codex mini")
        console_page.click("#send-button")

        console_page.wait_for_function(
            "document.getElementById('request-meta').textContent.includes('completed')",
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[terminal]" in text
        assert "codex:hello codex mini" in text

    def test_kimi_streaming_request(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "kimi")
        console_page.select_option("#model", "kimi-code/kimi-for-coding")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "hello kimi")
        console_page.click("#send-button")

        console_page.wait_for_function(
            "document.getElementById('request-meta').textContent.includes('completed')",
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[terminal]" in text
        assert "kimi:hello kimi" in text
