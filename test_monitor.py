from obs_match_recorder.browser import BrowserController
from obs_match_recorder.monitor import LiveMonitor


SITE_A = "https://live.bilibili.com/24001542"
SITE_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"


def print_status(status):
    if status is None:
        print("当前没有检测到可用直播网站。")
        return

    result = status.result

    print()
    print(">>> 当前选择：网站", status.name)
    print(">>> 地址：", status.url)
    print(">>> 状态：", result.status)
    print(">>> 评分：", result.score)
    print(">>> Video 播放：", result.video_playing)
    print(
        ">>> 分辨率：",
        f"{result.video_width}x{result.video_height}",
    )


def main():
    browser = BrowserController()

    try:
        print("正在启动浏览器……")
        browser.start()

        print("正在打开网站 A……")
        page_a = browser.open_page(SITE_A)
        print("A：", page_a.title())

        print("正在打开网站 B……")
        page_b = browser.open_page(SITE_B)
        print("B：", page_b.title())

        monitor = LiveMonitor(
            page_a=page_a,
            page_b=page_b,
            site_a_url=SITE_A,
            site_b_url=SITE_B,
            interval=5,
        )

        print()
        print("=" * 60)
        print("直播监控开始")
        print("每 5 秒检测一次")
        print("按 Ctrl+C 停止")
        print("=" * 60)

        for i in range(6):
            print()
            print(f"========== 第 {i + 1} 次检测 ==========")

            status_a, status_b = monitor.detect_once()

            print()
            print(
                "网站 A：",
                status_a.result.status,
                "| 评分：",
                status_a.result.score,
                "| 播放：",
                status_a.result.video_playing,
            )

            print(
                "网站 B：",
                status_b.result.status,
                "| 评分：",
                status_b.result.score,
                "| 播放：",
                status_b.result.video_playing,
            )

            selected = monitor.choose_site(
                status_a,
                status_b,
            )

            print_status(selected)

            if i < 5:
                print()
                print("等待 5 秒……")
                import time
                time.sleep(5)

        print()
        print("=" * 60)
        print("监控测试完成")
        print("=" * 60)

        input("按 Enter 关闭浏览器……")

    finally:
        browser.close()


if __name__ == "__main__":
    main()