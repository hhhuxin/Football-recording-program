"""从巴萨官网读取男足一线队下一场比赛。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
import re
from urllib.request import Request, urlopen

SCHEDULE_URL = "https://www.fcbarcelona.com/en/football/first-team/schedule"
BEIJING = timezone(timedelta(hours=8))
NAMES = {
    "FC Barcelona": "巴塞罗那", "Atlético Madrid": "马德里竞技",
    "Villarreal": "比利亚雷亚尔", "Real Madrid": "皇家马德里",
    "Athletic Club": "毕尔巴鄂竞技", "Real Betis": "皇家贝蒂斯",
    "Sevilla": "塞维利亚", "Valencia": "瓦伦西亚",
    "Espanyol": "西班牙人", "Real Sociedad": "皇家社会",
}
COMPETITIONS = {"La Liga": "西甲", "UEFA Champions League": "欧冠",
                "Champions League": "欧冠", "Copa del Rey": "国王杯"}


@dataclass(frozen=True)
class Fixture:
    name: str
    kickoff: datetime


def parse_next_fixture(document: str, now: datetime | None = None) -> Fixture:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    candidates = []
    for block in re.findall(
        r'<li\b[^>]*class="fixture-result-list__fixture"[^>]*>(.*?)</li>',
        document, re.S,
    ):
        date = re.search(r'data-fixture-date="(\d+)"', block)
        kickoff = re.search(r'data-kickoff="(\d+)"', block)
        teams = re.findall(r'class="fixture-info__name fixture-info__name--(?:home|away)"[^>]*>(.*?)</div>', block, re.S)
        if not date or len(teams) != 2:
            continue
        start = datetime.fromtimestamp(int((kickoff or date)[1]) / 1000, timezone.utc)
        if start <= now:
            continue
        teams = [unescape(re.sub(r'<[^>]+>', '', t)).strip() for t in teams]
        if "FC Barcelona" not in teams:
            continue
        competition = re.search(r'class="fixture-result-list__name visually-hidden"[^>]*>(.*?)</span>', block, re.S)
        label = unescape(competition[1]).strip() if competition else ""
        name = " VS ".join(NAMES.get(t, t) for t in teams)
        if label:
            name = f"{COMPETITIONS.get(label, label)} {name}"
        candidates.append((start, kickoff is not None, name))
    if not candidates:
        raise ValueError("巴萨官网未返回可用的未来赛程，原配置已保留。")
    start, confirmed, name = min(candidates, key=lambda item: item[0])
    if not confirmed:
        raise ValueError(f"下一场比赛（{name}）开球时间尚未确定，原配置已保留。")
    return Fixture(name, start.astimezone(BEIJING))


def fetch_next_fixture() -> Fixture:
    request = Request(SCHEDULE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        document = response.read().decode("utf-8")
    return parse_next_fixture(document)
