import time

from obs_match_recorder.obs_controller import OBSConnection, OBSController


def main():
    connection = OBSConnection(
        host="localhost",
        port=4455,
        password="123456",
    )

    obs = OBSController(connection)

    print("=" * 60)
    print("OBS WebSocket 测试")
    print("=" * 60)

    print()
    print("1. 正在连接 OBS...")

    version = obs.test_connection()

    print("连接成功！")
    print("OBS 版本：", version)

    print()
    print("2. 请求 OBS 开始录制...")

    # 不再修改 OBS 的录制路径。
    # 直接使用 OBS 当前已经设置好的录制路径。
    obs.start_recording()

    print("开始录制指令发送成功！")

    print()
    print("3. 录制 10 秒...")
    time.sleep(10)

    print()
    print("4. 请求 OBS 停止录制...")

    stopped = obs.stop_recording()

    if stopped:
        print("OBS 录制已停止！")
    else:
        print("OBS 当前没有正在录制。")

    print()
    print("=" * 60)
    print("OBS 测试完成")
    print("=" * 60)

    input("按 Enter 退出...")


if __name__ == "__main__":
    main()