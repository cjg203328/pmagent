"""
Progress Tracker - 智能进度跟踪器 (完整版)
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

try:
    from utils.logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class SmartProgressTracker:
    """智能进度跟踪器"""

    def __init__(self, database_manager=None):
        """
        初始化进度跟踪器

        Args:
            database_manager: 数据库管理器
        """
        self.db = database_manager
        logger.info("SmartProgressTracker初始化完成")

    def check_progress(
        self,
        project_id: Optional[str] = None,
        warning_days_ahead: int = 3,
        include_completed: bool = False
    ) -> Dict[str, Any]:
        """
        检查项目进度

        Args:
            project_id: 项目ID (None表示检查所有项目)
            warning_days_ahead: 提前多少天预警
            include_completed: 是否包含已完成项目

        Returns:
            进度检查结果
        """
        logger.info(f"开始检查进度 - 项目ID: {project_id or '全部'}, 预警天数: {warning_days_ahead}")

        if warning_days_ahead < 0:
            return {"success": False, "error": "warning_days_ahead 不能小于0"}

        try:
            # 获取项目列表
            projects = self._get_projects(project_id, include_completed)

            if not projects:
                logger.warning("没有找到需要检查的项目")
                return {
                    "success": True,
                    "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "warnings": [],
                    "on_track": [],
                    "message": "没有找到需要检查的项目"
                }

            logger.info(f"检查 {len(projects)} 个项目")

            # 分析每个项目
            warnings = []
            on_track = []
            at_risk = []
            unknown = []

            today = datetime.now()

            for proj in projects:
                analysis = self._analyze_project(proj, warning_days_ahead, today)

                if analysis["status"] == "warning":
                    warnings.append(analysis)
                elif analysis["status"] == "at_risk":
                    at_risk.append(analysis)
                elif analysis["status"] == "unknown":
                    unknown.append(analysis)
                else:
                    on_track.append(analysis)

            # 排序: 按紧急程度
            warnings.sort(key=lambda x: x["days_left"])
            at_risk.sort(key=lambda x: x["days_left"])

            result = {
                "success": True,
                "check_time": today.strftime("%Y-%m-%d %H:%M:%S"),
                "warnings": warnings,
                "at_risk": at_risk,
                "on_track": on_track,
                "unknown": unknown,
                "summary": {
                    "total_projects": len(projects),
                    "warning_count": len(warnings),
                    "at_risk_count": len(at_risk),
                    "on_track_count": len(on_track),
                    "unknown_count": len(unknown),
                }
            }

            logger.info(f"进度检查完成 - 预警: {len(warnings)}, 风险: {len(at_risk)}, 正常: {len(on_track)}")
            return result

        except Exception as e:
            logger.error(f"进度检查失败: {str(e)}")
            import traceback
            logger.error(f"堆栈跟踪:\n{traceback.format_exc()}")

            return {
                "success": False,
                "error": str(e)
            }

    def get_task_progress(self, project_id: str) -> Dict[str, Any]:
        """
        获取项目的任务进度

        Args:
            project_id: 项目ID

        Returns:
            任务进度详情
        """
        logger.info(f"获取项目任务进度: {project_id}")

        if not self.db:
            return {
                "success": False,
                "error": "数据库未配置"
            }

        try:
            # 获取项目的所有任务
            from database.models import Task

            session = self.db.get_session()
            try:
                tasks = session.query(Task).filter(Task.project_id == project_id).all()

                if not tasks:
                    return {
                        "success": True,
                        "project_id": project_id,
                        "tasks": [],
                        "message": "项目暂无任务"
                    }

                # 统计各状态任务数
                status_counts = {}
                total_estimated = 0
                total_actual = 0

                task_list = []
                for task in tasks:
                    status = task.status
                    status_counts[status] = status_counts.get(status, 0) + 1

                    total_estimated += task.estimated_hours or 0
                    total_actual += task.actual_hours or 0

                    task_list.append({
                        "task_name": task.task_name,
                        "status": task.status,
                        "progress": task.progress,
                        "assignee": task.assignee.name if task.assignee else None,
                        "due_date": task.due_date.strftime("%Y-%m-%d") if task.due_date else None,
                        "estimated_hours": task.estimated_hours,
                        "actual_hours": task.actual_hours
                    })

                # 计算总体进度
                overall_progress = sum(t.progress or 0 for t in tasks) / len(tasks) if tasks else 0

                return {
                    "success": True,
                    "project_id": project_id,
                    "tasks": task_list,
                    "summary": {
                        "total_tasks": len(tasks),
                        "status_counts": status_counts,
                        "overall_progress": round(overall_progress, 1),
                        "total_estimated_hours": total_estimated,
                        "total_actual_hours": total_actual,
                        "hours_variance": total_actual - total_estimated
                    }
                }

            finally:
                session.close()

        except Exception as e:
            logger.error(f"获取任务进度失败: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    def _get_projects(self, project_id: Optional[str], include_completed: bool) -> List:
        """获取项目列表"""
        if not self.db:
            # 返回演示数据
            logger.warning("数据库未配置,使用演示数据")
            return [
                {
                    "id": "demo_001",
                    "project_name": "腾讯角色项目",
                    "status": "进行中",
                    "deadline": datetime.now() + timedelta(days=2),
                    "progress": 60,
                    "client": "腾讯"
                },
                {
                    "id": "demo_002",
                    "project_name": "网易场景项目",
                    "status": "进行中",
                    "deadline": datetime.now() + timedelta(days=10),
                    "progress": 40,
                    "client": "网易"
                }
            ]

        try:
            if project_id:
                # 获取单个项目
                project = self.db.get_project(project_id)
                return [project] if project else []
            else:
                # 获取所有进行中的项目
                projects = self.db.list_projects(status=None, limit=100)
                if include_completed:
                    return projects
                terminal_statuses = {"已完成", "已取消", "completed", "cancelled"}
                return [project for project in projects if getattr(project, "status", None) not in terminal_statuses]

        except Exception as e:
            logger.error(f"获取项目列表失败: {e}")
            return []

    def _analyze_project(self, proj, warning_days_ahead: int, today: datetime) -> Dict:
        """分析项目状态"""
        def field(name, default=None):
            if isinstance(proj, dict):
                return proj.get(name, default)
            return getattr(proj, name, default)

        # 获取截止日期
        deadline = field("deadline")
        if not deadline:
            return {
                "project_id": field("id"),
                "project_name": field("project_name", field("name", "未知项目")),
                "status": "unknown",
                "severity": "low",
                "days_left": None,
                "deadline": None,
                "progress": field("progress", 0) or 0,
                "message": "未设置截止日期"
            }

        # 计算剩余天数
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline)
        if deadline.tzinfo is not None and today.tzinfo is None:
            deadline = deadline.replace(tzinfo=None)
        days_left = (deadline - today).days

        # 获取进度
        progress = field("progress")
        if progress is None:
            tasks = field("tasks", []) or []
            progress = (
                sum((getattr(task, "progress", 0) or 0) for task in tasks) / len(tasks)
                if tasks else 0
            )

        # 判断状态
        if days_left < 0:
            severity = "critical"
            status = "warning"
            message = f"已逾期 {abs(days_left)} 天"
        elif days_left == 0:
            severity = "high"
            status = "warning"
            message = "今天截止"
        elif days_left <= warning_days_ahead:
            severity = "medium"
            status = "warning"
            message = f"还有 {days_left} 天截止"
        elif progress < 50 and days_left <= 7:
            severity = "medium"
            status = "at_risk"
            message = f"进度较慢 ({progress}%), 还有 {days_left} 天"
        else:
            severity = "low"
            status = "on_track"
            message = f"进度正常 ({progress}%), 还有 {days_left} 天"

        return {
            "project_id": field("id"),
            "project_name": field("project_name", field("name", "未知项目")),
            "status": status,
            "severity": severity,
            "days_left": days_left,
            "deadline": deadline.strftime("%Y-%m-%d") if isinstance(deadline, datetime) else str(deadline),
            "progress": progress,
            "message": message
        }


# 使用示例
if __name__ == "__main__":
    from utils.logger import setup_logging
    setup_logging()

    tracker = SmartProgressTracker()

    # 检查所有项目
    result = tracker.check_progress(warning_days_ahead=3)

    if result["success"]:
        print("\n进度检查结果:")
        print(f"检查时间: {result['check_time']}")

        if result['warnings']:
            print(f"\n⚠️ 预警项目 ({len(result['warnings'])}个):")
            for warning in result['warnings']:
                print(f"  • {warning['project_name']}")
                print(f"    {warning['message']}")
                print(f"    严重程度: {warning['severity']}")

        if result['at_risk']:
            print(f"\n🔔 风险项目 ({len(result['at_risk'])}个):")
            for risk in result['at_risk']:
                print(f"  • {risk['project_name']}")
                print(f"    {risk['message']}")

        if result['on_track']:
            print(f"\n✅ 正常项目 ({len(result['on_track'])}个):")
            for proj in result['on_track'][:3]:  # 只显示前3个
                print(f"  • {proj['project_name']} - {proj['message']}")
    else:
        print(f"检查失败: {result['error']}")
