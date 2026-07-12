"""
Task Allocator - 智能任务分配器 (完整版)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Dict, List, Any
from datetime import datetime, timedelta
import math

try:
    from utils.logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class SmartTaskAllocator:
    """智能任务分配器"""

    def __init__(self, memory_manager=None):
        """
        初始化任务分配器

        Args:
            memory_manager: 记忆管理器,用于获取团队成员和历史数据
        """
        self.memory = memory_manager
        logger.info("SmartTaskAllocator初始化完成")

    def allocate(self, tasks: List[Dict[str, Any]], constraints: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        智能分配任务

        Args:
            tasks: 任务列表
            constraints: 约束条件

        Returns:
            分配结果
        """
        logger.info(f"开始分配 {len(tasks)} 个任务")

        if not tasks:
            logger.warning("任务列表为空")
            return {
                "success": False,
                "error": "任务列表为空"
            }

        # 获取约束条件
        constraints = constraints or {}
        try:
            max_load_per_person = float(constraints.get("max_load_per_person", 20))
        except (TypeError, ValueError):
            return {"success": False, "error": "max_load_per_person 必须是数字"}
        if max_load_per_person <= 0:
            return {"success": False, "error": "max_load_per_person 必须大于0"}
        consider_skills = constraints.get("consider_skills", True)
        consider_history = constraints.get("consider_history", True)
        project_id = constraints.get("project_id")

        # 获取团队成员
        team_members = self._get_team_members()
        if not team_members:
            logger.warning("没有找到可用的团队成员")
            return {
                "success": False,
                "error": "没有可用的团队成员"
            }

        logger.info(f"可用团队成员: {len(team_members)}人")

        # 计算每个成员的当前负载
        member_loads = self._calculate_current_loads(team_members)

        # 分配任务
        allocations = []
        for task in tasks:
            if isinstance(task, str):
                task = {"name": task, "type": "通用", "estimated_hours": 8}
            if not isinstance(task, dict):
                logger.warning(f"忽略无效任务: {task!r}")
                continue

            try:
                estimated_hours = float(task.get("estimated_hours", 8))
            except (TypeError, ValueError):
                logger.warning(f"任务 {task.get('name')} 的工时无效")
                continue
            if estimated_hours <= 0:
                logger.warning(f"任务 {task.get('name')} 的工时必须大于0")
                continue

            # 找到最佳匹配成员
            best_member = self._find_best_match(
                task,
                team_members,
                member_loads,
                estimated_hours=estimated_hours,
                max_load=max_load_per_person,
                project_id=project_id,
                consider_skills=consider_skills,
                consider_history=consider_history
            )

            if not best_member:
                logger.warning(f"任务 {task.get('name')} 无法分配")
                continue

            # 计算预估时间
            start_date = datetime.now()
            completion_date = start_date + timedelta(days=math.ceil(estimated_hours / 8))

            allocation = {
                "task_name": task.get("name"),
                "task_type": task.get("type", "通用"),
                "description": task.get("description", ""),
                "assigned_to": best_member["name"],
                "staff_id": best_member["id"],
                "match_score": best_member["match_score"],
                "match_reason": best_member["match_reason"],
                "estimated_hours": estimated_hours,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "estimated_completion": completion_date.strftime("%Y-%m-%d"),
                "priority": task.get("priority", "medium")
            }

            allocations.append(allocation)

            # 更新成员负载
            member_loads[best_member["id"]] = member_loads.get(best_member["id"], 0) + estimated_hours

            logger.debug(f"任务 {task.get('name')} 分配给 {best_member['name']} (匹配分数: {best_member['match_score']:.2f})")

        # 统计信息
        total_hours = sum(a["estimated_hours"] for a in allocations)
        avg_match_score = sum(a["match_score"] for a in allocations) / len(allocations) if allocations else 0

        result = {
            "success": True,
            "allocations": allocations,
            "summary": {
                "total_tasks": len(tasks),
                "allocated_tasks": len(allocations),
                "unallocated_tasks": len(tasks) - len(allocations),
                "total_hours": total_hours,
                "avg_match_score": avg_match_score
            },
            "total_tasks": len(tasks),
            "unassigned": len(tasks) - len(allocations),
            "team_load": {
                member["name"]: member_loads.get(member["id"], 0)
                for member in team_members
            }
        }

        logger.info(f"任务分配完成: {len(allocations)}/{len(tasks)} 个任务已分配")
        return result

    def _get_team_members(self) -> List[Dict]:
        """获取团队成员"""
        if self.memory:
            try:
                if hasattr(self.memory, "get_all_staff"):
                    members = self.memory.get_all_staff()
                elif hasattr(self.memory, "list_members"):
                    members = [member.to_dict() for member in self.memory.list_members()]
                else:
                    members = []
                if members:
                    logger.debug(f"从数据库获取到 {len(members)} 个成员")
                return members
            except Exception as e:
                logger.warning(f"从数据库获取成员失败: {e}")
                return []

        # 默认虚拟团队
        default_team = [
            {
                "id": "staff_001",
                "name": "张三",
                "role": "建模师",
                "skills": ["建模", "雕刻", "拓扑"],
                "skill_level": "senior",
                "hourly_rate": 150,
                "is_active": True
            },
            {
                "id": "staff_002",
                "name": "李四",
                "role": "贴图师",
                "skills": ["贴图", "材质", "UV"],
                "skill_level": "intermediate",
                "hourly_rate": 120,
                "is_active": True
            },
            {
                "id": "staff_003",
                "name": "王五",
                "role": "动画师",
                "skills": ["动画", "绑定", "K帧"],
                "skill_level": "senior",
                "hourly_rate": 160,
                "is_active": True
            },
            {
                "id": "staff_004",
                "name": "赵六",
                "role": "特效师",
                "skills": ["特效", "粒子", "流体"],
                "skill_level": "intermediate",
                "hourly_rate": 130,
                "is_active": True
            },
            {
                "id": "staff_005",
                "name": "刘七",
                "role": "全能",
                "skills": ["建模", "贴图", "动画", "特效"],
                "skill_level": "intermediate",
                "hourly_rate": 140,
                "is_active": True
            }
        ]

        logger.debug(f"使用默认团队: {len(default_team)} 个成员")
        return default_team

    def _calculate_current_loads(self, team_members: List[Dict]) -> Dict[str, float]:
        """计算成员当前负载"""
        loads = {}

        if self.memory:
            try:
                for member in team_members:
                    if hasattr(self.memory, "get_member_tasks"):
                        tasks = self.memory.get_member_tasks(member["id"])
                        active_tasks = [
                            task for task in tasks
                            if getattr(task, "status", "") not in {"已完成", "已取消", "completed", "cancelled"}
                        ]
                        loads[member["id"]] = sum(
                            float(getattr(task, "estimated_hours", 0) or 0)
                            for task in active_tasks
                        )
                    else:
                        loads[member["id"]] = 0
            except Exception as e:
                logger.warning(f"计算成员负载失败: {e}")

        return loads

    def _find_best_match(
        self,
        task: Dict,
        team_members: List[Dict],
        member_loads: Dict[str, float],
        estimated_hours: float,
        max_load: float,
        project_id: Any = None,
        consider_skills: bool = True,
        consider_history: bool = True
    ) -> Dict:
        """
        找到最佳匹配的成员

        Args:
            task: 任务信息
            team_members: 团队成员列表
            member_loads: 成员负载
            consider_skills: 是否考虑技能匹配
            consider_history: 是否考虑历史合作

        Returns:
            最佳匹配的成员(带match_score)
        """
        task_type = task.get("type", "").lower()
        task_keywords = task.get("keywords", [])

        scored_members = []

        for member in team_members:
            if not member.get("is_active", True):
                continue

            score = 0
            reasons = []

            # 1. 技能匹配 (40分)
            if consider_skills and task_type:
                raw_skills = member.get("skills", [])
                if isinstance(raw_skills, str):
                    raw_skills = [item.strip() for item in raw_skills.split(",")]
                member_skills = [str(s).lower() for s in raw_skills]
                if task_type in member_skills:
                    score += 40
                    reasons.append(f"技能匹配({task_type})")
                elif any(kw in member_skills for kw in task_keywords):
                    score += 30
                    reasons.append("关键词匹配")
                elif member.get("role", "").lower() == "全能":
                    score += 20
                    reasons.append("全能型成员")

            # 2. 负载均衡 (30分)
            current_load = member_loads.get(member["id"], 0)
            if current_load + estimated_hours > max_load:
                continue
            load_ratio = current_load / max_load if max_load > 0 else 0
            load_score = max(0, 30 * (1 - load_ratio))
            score += load_score
            if load_ratio < 0.5:
                reasons.append("负载较低")
            elif load_ratio < 0.8:
                reasons.append("负载适中")

            # 3. 经验等级 (20分)
            skill_level = member.get("skill_level", member.get("level", "intermediate"))
            if skill_level == "senior":
                score += 20
                reasons.append("资深经验")
            elif skill_level == "intermediate":
                score += 15
            elif skill_level == "junior":
                score += 10

            # 4. 历史合作 (10分) - 如果有数据库
            if consider_history and self.memory and project_id:
                worked_together = False
                try:
                    if hasattr(self.memory, "has_worked_together"):
                        worked_together = self.memory.has_worked_together(project_id, member["id"])
                    elif hasattr(self.memory, "get_member_tasks"):
                        worked_together = any(
                            str(getattr(task, "project_id", "")) == str(project_id)
                            for task in self.memory.get_member_tasks(member["id"])
                        )
                except Exception as error:
                    logger.debug(f"历史合作查询失败: {error}")
                if worked_together:
                    score += 10
                    reasons.append("已有项目协作经验")

            scored_members.append({
                **member,
                "match_score": score / 100,  # 归一化到0-1
                "match_reason": ", ".join(reasons) if reasons else "基础匹配"
            })

        # 按分数排序
        scored_members.sort(key=lambda x: x["match_score"], reverse=True)

        return scored_members[0] if scored_members else None


# 使用示例
if __name__ == "__main__":
    from utils.logger import setup_logging
    setup_logging()

    allocator = SmartTaskAllocator()

    tasks = [
        {"name": "角色建模", "type": "建模", "estimated_hours": 16},
        {"name": "场景建模", "type": "建模", "estimated_hours": 24},
        {"name": "角色贴图", "type": "贴图", "estimated_hours": 12},
        {"name": "角色动画", "type": "动画", "estimated_hours": 20},
        {"name": "特效制作", "type": "特效", "estimated_hours": 16},
    ]

    result = allocator.allocate(tasks)

    if result["success"]:
        print("\n任务分配结果:")
        for allocation in result["allocations"]:
            print(f"\n任务: {allocation['task_name']}")
            print(f"  负责人: {allocation['assigned_to']}")
            print(f"  匹配分数: {allocation['match_score']:.2f}")
            print(f"  匹配原因: {allocation['match_reason']}")
            print(f"  预计工时: {allocation['estimated_hours']}小时")

        print("\n总体情况:")
        print(f"  已分配: {result['summary']['allocated_tasks']}/{result['summary']['total_tasks']}")
        print(f"  总工时: {result['summary']['total_hours']}小时")
        print(f"  平均匹配分数: {result['summary']['avg_match_score']:.2f}")
    else:
        print(f"分配失败: {result['error']}")
