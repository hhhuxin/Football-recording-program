import time

from obs_match_recorder.browser import BrowserController
from obs_match_recorder.monitor import LiveMonitor
from obs_match_recorder.obs_controller import OBSConnection, OBSController


SITE_A = "https://live.bilibili.com/24001542"
SITE_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"

OBS_PASSWORD = "123456"
RECORDING_DIRECTORY = r"F:\Football Videos"


def main():
    print("=" * 60)
    print("OBS 比赛录制助手 V0.2 全流程测试")
    print("=" * 60)

    browser = None

    try:
        print("\n1. 启动浏览器...")
        browser = BrowserController()
        browser.start()
        print("浏览器启动成功。")

        print("\n2. 打开网站 A...")
        page_a = browser.open_page(SITE_A)
        print("网站 A：", page_a.title())

        print("\n3. 打开网站 B...")
        page_b = browser.open_page(SITE_B)
        print("网站 B：", page_b.title())

        print("\n4. 检测直播源...")

        monitor = LiveMonitor(
            page_a=page_a,
            page_b=page_b,
            site_a_url=SITE_A,
            site_b_url=SITE_B,
            interval=5,
        )

        status_a, status_b = monitor.detect_once()

        print("\n网站 A：")
        print("状态：", status_a.result.status)
        print("评分：", status_a.result.score)
        print("播放：", status_a.result.video_playing)

        print("\n网站 B：")
        print("状态：", status_b.result.status)
        print("评分：", status_b.result.score)
        print("播放：", status_b.result.video_playing)

        selected = monitor.choose_site(status_a, status_b)

        if selected is None:
            print("\n当前没有可用直播源。")
        else:
            print("\n当前选择：", selected.name)
            print("当前地址：", selected.url)
            print("当前状态：", selected.result.status)
            print("当前评分：", selected.result.score)

        print("\n5. 连接 OBS...")

        obs = OBSController(
            OBSConnection(
                host="localhost",
                port=4455,
                password=OBS_PASSWORD,
            )
        )

        version = obs.test_connection()

        print("OBS 连接成功。")
        print("OBS 版本：", version)

        print("\n6. 开始 OBS 录制...")

        obs.start_recording(RECORDING_DIRECTORY)

        print("OBS 录制已开始。")
        print("录制 15 秒...")

        for i in range(15):
            print(f"\r已录制 {i + 1}/15 秒", end="", flush=True)
            time.sleep(1)

        print()

        print("\n7. 停止 OBS 录制...")

        if obs.stop_recording():
            print("OBS 录制已停止。")
        else:
            print("OBS 当前没有录制。")

        print("\n全流程测试完成。")

    except KeyboardInterrupt:
        print("\n\n测试已手动停止。")

    except Exception as error:
        print("\n\n测试失败：")
        print(error)

    finally:
        if browser is not None:
            print("\n关闭浏览器...")
            browser.close()
            print("浏览器已关闭。")

    input("\n按 Enter 退出...")


if __name__ == "__main__":
    main()