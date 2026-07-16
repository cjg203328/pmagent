"""
Token监控系统 - 实时监控和预算管理
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import Dict, List
from pathlib import Path
import pandas as pd
import logging

from artpm_agent.core.redis_cache import (
    get_redis,
    cache_get_json,
    cache_set_json,
    cache_delete_prefix,
    key,
)

logger = logging.getLogger(__name__)


class TokenMonitor:
    """Token监控器 - 存储和分析Token使用情况"""

    def __init__(self, db_path: str = None):
        if db_path:
            self.db_path = str(Path(db_path).expanduser().resolve())
        else:
            from artpm_agent.config import resolve_data_root
            self.db_path = str(resolve_data_root() / "token_usage.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                feature TEXT,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                cost REAL NOT NULL,
                latency REAL,
                session_id TEXT
            )
            """)

            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON token_usage(timestamp)
            """)

            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_model ON token_usage(model)
            """)

            conn.commit()

    def track(self, provider: str, model: str, input_tokens: int,
              output_tokens: int, cost: float, feature: str = None,
              latency: float = None, session_id: str = None):
        """记录Token使用"""
        if input_tokens < 0 or output_tokens < 0 or cost < 0:
            raise ValueError("token counts and cost cannot be negative")

        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()

            cursor.execute("""
            INSERT INTO token_usage
            (timestamp, provider, model, feature, input_tokens, output_tokens,
             total_tokens, cost, latency, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                provider,
                model,
                feature,
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                cost,
                latency,
                session_id
            ))

            conn.commit()
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Drop Redis-cached token aggregates after a write (best-effort)."""
        try:
            cache_delete_prefix(key("tok"))
        except Exception:  # noqa: BLE001
            pass

    def get_today_stats(self) -> Dict:
        """获取今日统计"""
        r = get_redis()
        if r is not None:
            cached = cache_get_json(key("tok", "today"))
            if cached is not None:
                return cached

        today = datetime.now().date()
        yesterday = today - timedelta(days=1)

        with closing(sqlite3.connect(self.db_path)) as conn:
            # 今日数据
            today_df = pd.read_sql_query(f"""
            SELECT * FROM token_usage
            WHERE DATE(timestamp) = '{today}'
            """, conn)

            # 昨日数据（用于计算变化）
            yesterday_df = pd.read_sql_query(f"""
            SELECT * FROM token_usage
            WHERE DATE(timestamp) = '{yesterday}'
            """, conn)

        today_tokens = today_df['total_tokens'].sum() if len(today_df) > 0 else 0
        today_cost = today_df['cost'].sum() if len(today_df) > 0 else 0
        today_avg_latency = today_df['latency'].mean() if len(today_df) > 0 else 0

        yesterday_tokens = yesterday_df['total_tokens'].sum() if len(yesterday_df) > 0 else 0
        yesterday_cost = yesterday_df['cost'].sum() if len(yesterday_df) > 0 else 0

        result = {
            "tokens": int(today_tokens),
            "cost": float(today_cost),
            "avg_latency": float(today_avg_latency),
            "tokens_change": int(today_tokens - yesterday_tokens),
            "cost_change": float(today_cost - yesterday_cost),
            "daily_limit": 100000,
            "requests": len(today_df)
        }
        if r is not None:
            cache_set_json(key("tok", "today"), result, ttl=15)
        return result

    def get_model_stats(self, days: int = 7) -> List[Dict]:
        """按模型统计"""
        r = get_redis()
        ck = key("tok", "model", str(days))
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return cached

        start_date = (datetime.now() - timedelta(days=days)).date()

        with closing(sqlite3.connect(self.db_path)) as conn:
            df = pd.read_sql_query(f"""
            SELECT
                model,
                provider,
                COUNT(*) as requests,
                SUM(total_tokens) as tokens,
                SUM(cost) as cost,
                AVG(latency) as avg_latency
            FROM token_usage
            WHERE DATE(timestamp) >= '{start_date}'
            GROUP BY model, provider
            ORDER BY tokens DESC
            """, conn)

        result = df.to_dict('records')
        if r is not None:
            cache_set_json(ck, result, ttl=30)
        return result

    def get_feature_stats(self, days: int = 7) -> List[Dict]:
        """按功能统计"""
        r = get_redis()
        ck = key("tok", "feature", str(days))
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return cached

        start_date = (datetime.now() - timedelta(days=days)).date()

        with closing(sqlite3.connect(self.db_path)) as conn:
            df = pd.read_sql_query(f"""
            SELECT
                COALESCE(feature, '未分类') as feature,
                COUNT(*) as requests,
                SUM(total_tokens) as tokens,
                SUM(cost) as cost
            FROM token_usage
            WHERE DATE(timestamp) >= '{start_date}'
            GROUP BY feature
            ORDER BY tokens DESC
            """, conn)

        result = df.to_dict('records')
        if r is not None:
            cache_set_json(ck, result, ttl=30)
        return result

    def get_trend_data(self, days: int = 7) -> pd.DataFrame:
        """获取趋势数据"""
        r = get_redis()
        ck = key("tok", "trend", str(days))
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                # Rebuild the DataFrame from its serialized form.
                if isinstance(cached, dict) and "records" in cached:
                    return pd.DataFrame(
                        cached["records"], columns=cached.get("columns")
                    )
                return cached

        start_date = (datetime.now() - timedelta(days=days)).date()

        with closing(sqlite3.connect(self.db_path)) as conn:
            df = pd.read_sql_query(f"""
            SELECT
                DATE(timestamp) as date,
                model,
                SUM(total_tokens) as tokens,
                SUM(cost) as cost
            FROM token_usage
            WHERE DATE(timestamp) >= '{start_date}'
            GROUP BY DATE(timestamp), model
            ORDER BY date, model
            """, conn)

        result = df
        if r is not None:
            # DataFrames are not JSON-serializable; cache as records + columns.
            cache_set_json(
                ck,
                {"columns": list(df.columns), "records": df.to_dict("records")},
                ttl=30,
            )
        return result

    def get_hourly_usage(self) -> List[Dict]:
        """获取小时级使用情况（今日）"""
        r = get_redis()
        ck = key("tok", "hourly")
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return cached

        today = datetime.now().date()

        with closing(sqlite3.connect(self.db_path)) as conn:
            df = pd.read_sql_query(f"""
            SELECT
                strftime('%H', timestamp) as hour,
                SUM(total_tokens) as tokens,
                SUM(cost) as cost,
                COUNT(*) as requests
            FROM token_usage
            WHERE DATE(timestamp) = '{today}'
            GROUP BY strftime('%H', timestamp)
            ORDER BY hour
            """, conn)

        result = df.to_dict('records')
        if r is not None:
            cache_set_json(ck, result, ttl=30)
        return result

    def get_usage_since(self, start_date) -> Dict:
        """Aggregate token and cost usage since a date (inclusive)."""
        r = get_redis()
        ck = key("tok", "since", str(start_date))
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return cached

        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost), 0), COUNT(*)
                FROM token_usage WHERE DATE(timestamp) >= DATE(?)
                """,
                (str(start_date),),
            ).fetchone()
        finally:
            conn.close()
        result = {"tokens": int(row[0]), "cost": float(row[1]), "requests": int(row[2])}
        if r is not None:
            cache_set_json(ck, result, ttl=15)
        return result


class TokenBudgetManager:
    """Token预算管理器"""

    def __init__(self, config_path: str = "./config/token_budget.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.monitor = TokenMonitor()

    def _load_config(self) -> Dict:
        """加载配置"""
        default_config = {
            "limits": {
                "daily": 100000,
                "weekly": 500000,
                "monthly": 2000000,
                "per_request": 8000
            },
            "alerts": {
                "daily_warning": 0.8,  # 80%时预警
                "daily_critical": 0.95  # 95%时严重预警
            },
            "cost_limits": {
                "daily": 10.0,
                "monthly": 200.0
            }
        }

        try:
            if Path(self.config_path).exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    for section, value in loaded.items():
                        if isinstance(value, dict) and isinstance(default_config.get(section), dict):
                            default_config[section].update(value)
                        else:
                            default_config[section] = value
        except Exception as e:
            print(f"加载配置失败: {e}, 使用默认配置")

        return default_config

    def save_config(self):
        """保存配置"""
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def check_limit(self, requested_tokens: int, period: str = "daily") -> Dict:
        """检查是否超限"""
        if period not in self.config["limits"]:
            raise ValueError(f"Unsupported budget period: {period}")
        if requested_tokens < 0:
            raise ValueError("requested_tokens cannot be negative")
        limit = self.config["limits"][period]
        if limit <= 0:
            raise ValueError(f"{period} token limit must be greater than zero")
        current_usage = self._get_current_usage(period)

        remaining = limit - current_usage["tokens"]
        usage_rate = current_usage["tokens"] / limit

        can_proceed = current_usage["tokens"] + requested_tokens <= limit

        alert_level = None
        if usage_rate >= self.config["alerts"]["daily_critical"]:
            alert_level = "critical"
        elif usage_rate >= self.config["alerts"]["daily_warning"]:
            alert_level = "warning"

        return {
            "can_proceed": can_proceed,
            "limit": limit,
            "current": current_usage["tokens"],
            "remaining": remaining,
            "usage_rate": usage_rate,
            "alert_level": alert_level,
            "requested": requested_tokens,
            "after_request": current_usage["tokens"] + requested_tokens
        }

    def _get_current_usage(self, period: str) -> Dict:
        """获取当前周期的使用量"""
        if period == "daily":
            return self.monitor.get_today_stats()
        elif period == "weekly":
            start = datetime.now().date() - timedelta(days=datetime.now().date().weekday())
            return self.monitor.get_usage_since(start)
        elif period == "monthly":
            start = datetime.now().date().replace(day=1)
            return self.monitor.get_usage_since(start)

        return {"tokens": 0, "cost": 0}

    def get_budget_status(self) -> Dict:
        """获取预算状态"""
        daily_check = self.check_limit(0, "daily")
        today_stats = self.monitor.get_today_stats()

        return {
            "daily": {
                "limit": daily_check["limit"],
                "used": daily_check["current"],
                "remaining": daily_check["remaining"],
                "usage_rate": daily_check["usage_rate"],
                "alert_level": daily_check["alert_level"]
            },
            "cost": {
                "today": today_stats["cost"],
                "limit": self.config["cost_limits"]["daily"],
                "remaining": self.config["cost_limits"]["daily"] - today_stats["cost"]
            },
            "recommendations": self._get_recommendations(daily_check)
        }

    def _get_recommendations(self, check_result: Dict) -> List[str]:
        """获取优化建议"""
        recommendations = []

        if check_result["usage_rate"] > 0.9:
            recommendations.append("⚠️ Token使用量接近上限，建议减少大模型调用")
            recommendations.append("💡 考虑使用缓存减少重复查询")

        if check_result["usage_rate"] > 0.7:
            recommendations.append("💡 可以切换到更经济的模型（如GPT-3.5或DeepSeek）")

        return recommendations

    def set_limit(self, period: str, value: int):
        """设置限额"""
        if period not in self.config["limits"]:
            raise ValueError(f"Unsupported budget period: {period}")
        if value <= 0:
            raise ValueError("limit must be greater than zero")
        self.config["limits"][period] = value
        self.save_config()


# 使用示例
if __name__ == "__main__":
    # 1. 记录使用
    monitor = TokenMonitor()
    monitor.track(
        provider="openai",
        model="gpt-4o",
        input_tokens=1000,
        output_tokens=500,
        cost=0.0175,
        feature="chat",
        latency=1.2
    )

    # 2. 查看统计
    today_stats = monitor.get_today_stats()
    print(f"今日使用: {today_stats}")

    model_stats = monitor.get_model_stats()
    print(f"模型统计: {model_stats}")

    # 3. 预算管理
    budget = TokenBudgetManager()
    status = budget.get_budget_status()
    print(f"预算状态: {status}")

    # 4. 检查限额
    check = budget.check_limit(5000)
    if check["can_proceed"]:
        print("可以继续调用")
    else:
        print(f"已超限! 告警级别: {check['alert_level']}")
