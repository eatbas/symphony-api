from __future__ import annotations

from playwright.sync_api import Page, expect


class TestPageLoad:
    def test_title(self, console_page: Page):
        expect(console_page).to_have_title("Symphony Console")

    def test_hero_heading_visible(self, console_page: Page):
        heading = console_page.locator("h1")
        expect(heading).to_be_visible()
        expect(heading).to_contain_text("Conduct your AI orchestra from the podium")

    def test_health_status_ok(self, console_page: Page):
        status = console_page.locator("#health-status")
        expect(status).to_have_text("ok")

    def test_musician_count(self, console_page: Page):
        count = console_page.locator("#musician-count")
        expect(count).to_have_text("9")

    def test_all_musicians_shown(self, console_page: Page):
        chips = console_page.locator(".musician-chip")
        expect(chips).to_have_count(9)

    def test_all_musicians_ready(self, console_page: Page):
        chips = console_page.locator(".musician-chip")
        for i in range(chips.count()):
            expect(chips.nth(i)).to_contain_text("ready")


class TestProviderModelDropdowns:
    def test_provider_options_exist(self, console_page: Page):
        options = console_page.locator("#provider option")
        providers = [options.nth(i).get_attribute("value") for i in range(options.count())]
        assert "antigravity" in providers
        assert "claude" in providers
        assert "codex" in providers
        assert "kimi" in providers

    def test_model_dropdown_updates_on_provider_change(self, console_page: Page):
        console_page.select_option("#provider", "claude")
        console_page.wait_for_timeout(200)
        model_options = console_page.locator("#model option")
        models = [model_options.nth(i).get_attribute("value") for i in range(model_options.count())]
        assert "opus" in models
        assert "haiku" in models

    def test_codex_models(self, console_page: Page):
        console_page.select_option("#provider", "codex")
        console_page.wait_for_timeout(200)
        model_options = console_page.locator("#model option")
        models = [model_options.nth(i).get_attribute("value") for i in range(model_options.count())]
        assert "gpt-5.4" in models
        assert "gpt-5.4" in models

    def test_kimi_models(self, console_page: Page):
        console_page.select_option("#provider", "kimi")
        console_page.wait_for_timeout(200)
        model_options = console_page.locator("#model option")
        models = [model_options.nth(i).get_attribute("value") for i in range(model_options.count())]
        assert "kimi-code/kimi-for-coding" in models
