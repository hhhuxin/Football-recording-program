from types import SimpleNamespace

from obs_match_recorder.source_selector import SourceSelector


def make_status(name, url, status, playing, score):
    result = SimpleNamespace(
        status=status,
        video_playing=playing,
        score=score,
    )

    return SimpleNamespace(
        name=name,
        url=url,
        result=result,
    )


def show(title, selection):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)

    print("当前选择：", selection.selected_name)
    print("当前地址：", selection.selected_url)
    print("是否发生切换：", selection.changed)


def main():
    selector = SourceSelector()

    site_a = make_status(
        "A",
        "https://live.bilibili.com/24001542",
        "正在播放",
        True,
        10,
    )

    site_b = make_status(
        "B",
        "https://www.fszb336.com/broadcast/details?room_id=910427680",
        "正在播放",
        True,
        10,
    )

    # 第一次：A/B 都正常
    result = selector.select(site_a, site_b)
    show("测试 1：A/B 都正常", result)

    # 第二次：A 断播，B 正常
    site_a.result.status = "未确认直播"
    site_a.result.video_playing = False
    site_a.result.score = 0

    result = selector.select(site_a, site_b)
    show("测试 2：A 断播，B 正常", result)

    # 第三次：A 恢复，B 也正常
    site_a.result.status = "正在播放"
    site_a.result.video_playing = True
    site_a.result.score = 10

    result = selector.select(site_a, site_b)
    show("测试 3：A 恢复，B 仍正常", result)

    # 第四次：B 断播，A 正常
    site_b.result.status = "未确认直播"
    site_b.result.video_playing = False
    site_b.result.score = 0

    result = selector.select(site_a, site_b)
    show("测试 4：B 断播，A 正常", result)


if __name__ == "__main__":
    main()