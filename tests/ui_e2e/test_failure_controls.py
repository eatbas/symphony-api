from __future__ import annotations

import re

from playwright.sync_api import Page, expect


class TestFailedRequest:
    def test_failed_request_shows_error(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "claude")
        console_page.select_option("#model", "opus")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "fail")
        console_page.click("#send-button")

        console_page.wait_for_function(
            """
            (() => {
                const meta = document.getElementById('request-meta').textContent;
                return meta.includes('completed') || meta.includes('fail') || meta.includes('error');
            })()
            """,
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[failed]" in text

    def test_musician_stays_ready_after_cli_failure(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "claude")
        console_page.select_option("#model", "opus")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "fail")
        console_page.click("#send-button")

        console_page.wait_for_function(
            """
            (() => {
                const meta = document.getElementById('request-meta').textContent;
                return meta.includes('completed') || meta.includes('fail') || meta.includes('error');
            })()
            """,
            timeout=15_000,
        )

        console_page.click("#refresh-button")
        console_page.wait_for_timeout(1000)

        claude_group = console_page.locator(
            ".musician-group",
            has=console_page.locator(".musician-group-header", has_text=re.compile(r"^claude\b", re.IGNORECASE)),
        )
        opus_chip = claude_group.locator(".musician-chip", has_text="opus")
        expect(opus_chip).to_have_count(1)
        expect(opus_chip).to_contain_text("ready")

    def test_recovery_after_failure(self, console_page: Page, tmp_path):
        console_page.select_option("#provider", "codex")
        console_page.select_option("#model", "gpt-5.4")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "fail")
        console_page.click("#send-button")

        console_page.wait_for_function(
            """
            (() => {
                const meta = document.getElementById('request-meta').textContent;
                return meta.includes('completed') || meta.includes('fail') || meta.includes('error');
            })()
            """,
            timeout=15_000,
        )

        console_page.select_option("#provider", "codex")
        console_page.select_option("#model", "gpt-5.4")
        console_page.fill("#workspace_path", str(tmp_path.resolve()))
        console_page.fill("#prompt", "recover")
        console_page.click("#send-button")

        console_page.wait_for_function(
            "document.getElementById('request-meta').textContent.includes('completed')",
            timeout=15_000,
        )

        text = console_page.locator("#console").inner_text()
        assert "[completed]" in text
        assert "codex:recover" in text


class TestRefreshWorkerState:
    def test_refresh_button_works(self, console_page: Page):
        console_page.click("#refresh-button")
        console_page.wait_for_timeout(500)
        expect(console_page.locator("#request-meta")).to_have_text("State refreshed.")

    def test_musicians_remain_after_refresh(self, console_page: Page):
        console_page.click("#refresh-button")
        console_page.wait_for_timeout(500)
        expect(console_page.locator(".musician-chip")).to_have_count(11)


class TestModeToggle:
    def test_session_ref_disabled_in_new_mode(self, console_page: Page):
        console_page.select_option("#mode", "new")
        console_page.wait_for_timeout(100)
        expect(console_page.locator("#provider_session_ref")).to_be_disabled()

    def test_session_ref_enabled_in_resume_mode(self, console_page: Page):
        console_page.select_option("#mode", "resume")
        console_page.wait_for_timeout(100)
        expect(console_page.locator("#provider_session_ref")).to_be_enabled()
