"""V0.3 直播检测测试。

测试网站使用 Google，不使用百度。
"""

from __future__ import annotations

import sys
import time

from obs_match_recorder.browser import BrowserController
from obs_match_recorder.site_adapter import GenericSiteAdapter


def main() -> None:
    url_a = "https://www.google.com"
    url_b = "https://www.youtube.com"

    browser = BrowserController()

    try:
        print("正在启动浏览器……")
        browser.start()

        print("正在打开测试网站 A：Google")
        page_a = browser.open_page(url_a)

        print("正在打开测试网站 B：YouTube")
        page_b = browser.open_page(url_b)

        adapter_a = GenericSiteAdapter(page_a, "网站 A")
        adapter_b = GenericSiteAdapter(page_b, "网站 B")

        print()
        print("=" * 60)
        print("开始直播状态检测")
        print("=" * 60)

        for i in range(3):
            print()
            print(f"第 {i + 1} 次检测")
            print("-" * 60)

            result_a = adapter_a.check()
            result_b = adapter_b.check()

            print(
                f"A：{result_a.status} | "
                f"评分={result_a.score} | "
                f"currentTime={result_a.current_time:.2f} | "
                f"分辨率={result_a.video_width}x{result_a.video_height}"
            )
            print(f"   {result_a.detail}")

            print(
                f"B：{result_b.status} | "
                f"评分={result_b.score} | "
                f"currentTime={result_b.current_time:.2f} | "
                f"分辨率={result_b.video_width}x{result_b.video_height}"
            )
            print(f"   {result_b.detail}")

            time.sleep(5)

        input("\n测试完成，按 Enter 关闭浏览器……")

    except Exception as error:
        print()
        print("测试失败：")
        print(error)
        input("\n按 Enter 关闭……")

    finally:
        browser.close()


if __name__ == "__main__":
    main()