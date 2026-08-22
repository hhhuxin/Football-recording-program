"""Playwright 库本身可用性验证（集成测试）。

不依赖调试 Chrome，验证环境里 Playwright 能启动浏览器并访问网页。
首次使用需执行：python -m playwright install chromium

运行方式：
    pytest tests/test_playwright.py --run-integration
"""

from __future__ import annotations

import pytest

from playwright.sync_api import sync_playwright


@pytest.mark.integration
def test_playwright_can_launch_and_visit_page() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(
                "https://www.baidu.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            assert page.title(), "页面应能加载出标题"
        finally:
            browser.close()
