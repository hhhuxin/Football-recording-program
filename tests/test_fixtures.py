from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from obs_match_recorder.fixtures import Fixture, BEIJING, parse_next_fixture
from obs_match_recorder.main import OBSMatchRecorderApp
from obs_match_recorder.config import AppConfig


def event(date, home="Atlético Madrid", away="FC Barcelona", confirmed=True):
    stamp = int(datetime.fromisoformat(date).timestamp() * 1000)
    kickoff = f'data-kickoff="{stamp}"' if confirmed else ''
    return f'''<li class="fixture-result-list__fixture" data-fixture-id="1">
    <div data-fixture-date="{stamp}" {kickoff}></div>
    <span class="fixture-result-list__name visually-hidden">La Liga</span>
    <div class="fixture-info__name fixture-info__name--home">{home}</div>
    <div class="fixture-info__name fixture-info__name--away">{away}</div></li>'''


def test_selects_earliest_future_fixture_and_converts_across_midnight():
    document = event('2026-11-22T13:00:00+00:00') + event('2026-11-08T20:00:00+00:00') + event('2026-11-01T20:00:00+00:00')
    fixture = parse_next_fixture(document, datetime(2026, 11, 8, tzinfo=timezone.utc))
    assert fixture.kickoff.strftime('%Y-%m-%d %H:%M') == '2026-11-09 04:00'
    assert fixture.name == '西甲 马德里竞技 VS 巴塞罗那'


def test_does_not_skip_nearest_fixture_with_unconfirmed_time():
    with pytest.raises(ValueError, match='尚未确定'):
        parse_next_fixture(event('2026-11-09T00:00:00+00:00', confirmed=False) + event('2026-11-22T13:00:00+00:00'), datetime(2026, 11, 8, tzinfo=timezone.utc))


def test_empty_or_unrelated_schedule_is_rejected():
    with pytest.raises(ValueError, match='未返回'):
        parse_next_fixture(event('2026-11-09T00:00:00+00:00', away='Villarreal'), datetime(2026, 11, 8, tzinfo=timezone.utc))


def test_auto_fill_preserves_urls_and_other_saved_settings(monkeypatch):
    from obs_match_recorder import main
    saved = AppConfig(site_a_url='https://example.com/a', site_b_url='https://example.com/b', obs_password='secret')
    store = Mock()
    monkeypatch.setattr(main, 'load_config', lambda: saved)
    monkeypatch.setattr(main, 'save_config', store)
    app = object.__new__(OBSMatchRecorderApp)
    app.fixture_loading = False
    app.task_active = False
    app.fixture_button = Mock()
    app.log = Mock()
    app.variables = {key: Mock() for key in ('match_name', 'match_start_time', 'site_a_url', 'site_b_url')}
    fixture = Fixture('西甲 马德里竞技 VS 巴塞罗那', datetime(2026, 11, 9, 4, tzinfo=BEIJING))
    app._run_in_background = lambda work, success, **kwargs: success(fixture)
    app.refresh_barcelona_fixture()
    result = store.call_args.args[0]
    assert (result.site_a_url, result.site_b_url, result.obs_password) == (saved.site_a_url, saved.site_b_url, saved.obs_password)
    assert result.match_start_time == '2026-11-09 04:00'
    app.variables['site_a_url'].set.assert_not_called()
    app.variables['site_b_url'].set.assert_not_called()
