import sqlite3

from artpm_agent.core import token_monitor as token_monitor_module
from artpm_agent.core.token_monitor import TokenMonitor


class _TrackingConnection(sqlite3.Connection):
    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def test_token_monitor_closes_every_sqlite_connection(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    connections = []

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(token_monitor_module.sqlite3, "connect", tracked_connect)

    monitor = TokenMonitor(str(tmp_path / "tokens.db"))
    monitor.track("openai", "model", 10, 5, 0.01)
    monitor.get_today_stats()
    monitor.get_model_stats()
    monitor.get_feature_stats()
    monitor.get_trend_data()
    monitor.get_hourly_usage()
    monitor.get_usage_since("2026-01-01")

    assert connections
    assert all(connection.was_closed for connection in connections)
