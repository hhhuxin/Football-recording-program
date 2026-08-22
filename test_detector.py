from obs_match_recorder.browser import BrowserController
from obs_match_recorder.detector import LiveDetector


SITE_A = "https://live.bilibili.com/24001542"
SITE_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"


def print_result(name, result):
    print()
    print(f"【{name}】")
    print("-" * 60)
    print("状态：", result.status)
    print("评分：", result.score)
    print("页面正常：", result.page_ok)
    print("找到播放器：", result.player_found)
    print("发现直播文字：", result.live_text_found)
    print("检测到错误：", result.error_found)
    print("Video 存在：", result.video_exists)
    print("Video 播放中：", result.video_playing)
    print("currentTime：", result.current_time)
    print("视频分辨率：", result.video_width, "x", result.video_height)
    print("详细信息：", result.details)


def main():
    browser = BrowserController()

    try:
        browser.start()

        print("正在打开网站 A...")
        page_a = browser.open_page(SITE_A)
        print("A 打开成功：", page_a.title())

        print("正在打开网站 B...")
        page_b = browser.open_page(SITE_B)
        print("B 打开成功：", page_b.title())

        print()
        print("=" * 60)
        print("开始检测")
        print("=" * 60)

        detector_a = LiveDetector(page_a)
        detector_b = LiveDetector(page_b)

        for i in range(3):
            print()
            print(f"========== 第 {i + 1} 次检测 ==========")

            result_a = detector_a.detect()
            result_b = detector_b.detect()

            print_result("网站 A", result_a)
            print_result("网站 B", result_b)

            if i < 2:
                print()
                print("等待 5 秒后进行下一次检测……")
                import time
                time.sleep(5)

        print()
        print("=" * 60)
        print("检测完成")
        print("=" * 60)

        input("按 Enter 关闭浏览器...")

    finally:
        browser.close()


if __name__ == "__main__":
    main()