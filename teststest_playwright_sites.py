"""V0.3 真实直播网站检测测试。"""

from __future__ import annotations

import time

from obs_match_recorder.browser import BrowserController
from obs_match_recorder.site_adapter import GenericSiteAdapter


URL_A = "https://live.bilibili.com/251054248"
URL_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"


def print_result(name: str, result) -> None:
    print(f"\n{name}")
    print("-" * 60)
    print("状态：", result.status)
    print("评分：", result.score)
    print("页面正常：", result.page_loaded)
    print("找到播放器：", result.player_found)
    print("发现直播文字：", result.live_text_found)
    print("检测到错误：", result.error_detected)
    print("Video 存在：", result.video_exists)
    print("Video 播放中：", result.video_playing)
    print("currentTime：", result.current_time)
    print("视频分辨率：", f"{result.video_width} x {result.video_height}")
    print("详细信息：", result.detail)


def main() -> None:
    browser = BrowserController()

    try:
        print("正在启动浏览器……")
        browser.start()

        print("\n正在打开网站 A……")
        page_a = browser.open_page(URL_A)
        print("A 打开成功：", page_a.title())
        print("A 地址：", page_a.url)

        print("\n正在打开网站 B……")
        page_b = browser.open_page(URL_B)
        print("B 打开成功：", page_b.title())
        print("B 地址：", page_b.url)

        adapter_a = GenericSiteAdapter(page_a, "网站 A")
        adapter_b = GenericSiteAdapter(page_b, "网站 B")

        print("\n" + "=" * 60)
        print("开始进行 3 次直播检测")
        print("=" * 60)

        for number in range(1, 4):
            print(f"\n\n========== 第 {number} 次检测 ==========")

            result_a = adapter_a.check()
            result_b = adapter_b.check()

            print_result("【网站 A】", result_a)
            print_result("【网站 B】", result_b)

            if number < 3:
                print("\n等待 5 秒后进行下一次检测……")
                time.sleep(5)

        print("\n" + "=" * 60)
        print("检测完成")
        print("=" * 60)

        input("\n按 Enter 关闭浏览器……")

    except Exception as error:
        print("\n测试发生错误：")
        print(error)
        input("\n按 Enter 关闭……")

    finally:
        browser.close()


if __name__ == "__main__":
    main()