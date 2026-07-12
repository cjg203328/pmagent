"""
高级数据可视化 - 甘特图、燃尽图、热力图等
"""
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import pandas as pd
import numpy as np
from typing import List, Dict


class AdvancedVisualizer:
    """高级数据可视化工具"""

    def __init__(self):
        self.color_scheme = {
            "primary": "#667eea",
            "secondary": "#764ba2",
            "success": "#22c55e",
            "warning": "#fb923c",
            "danger": "#ef4444",
            "info": "#38bdf8"
        }

    def create_gantt_chart(self, tasks: List[Dict]) -> go.Figure:
        """
        创建甘特图

        Args:
            tasks: 任务列表
                [{
                    "task_name": "任务名称",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-15",
                    "progress": 65,
                    "assignee": "张三",
                    "status": "进行中"
                }]
        """
        if not tasks:
            return self._create_empty_chart("暂无任务数据")

        # 准备数据
        df = pd.DataFrame(tasks)

        # 转换日期
        df['start_date'] = pd.to_datetime(df['start_date'])
        df['end_date'] = pd.to_datetime(df['end_date'])

        # 计算持续时间
        if (df['end_date'] < df['start_date']).any():
            raise ValueError("end_date cannot be earlier than start_date")
        df['duration_ms'] = (
            (df['end_date'] - df['start_date']).dt.total_seconds().clip(lower=86400)
            * 1000
        )

        # 状态颜色映射
        status_colors = {
            "未开始": "#94a3b8",
            "进行中": self.color_scheme["primary"],
            "待审核": self.color_scheme["warning"],
            "已完成": self.color_scheme["success"],
            "已取消": self.color_scheme["danger"]
        }

        # 创建甘特图
        fig = go.Figure()

        for idx, task in df.iterrows():
            # 已完成部分
            progress = max(0, min(100, float(task.get('progress', 0) or 0)))
            completed_duration = task['duration_ms'] * (progress / 100)

            # 完成进度条
            fig.add_trace(go.Bar(
                name=task['task_name'],
                x=[completed_duration],
                y=[task['task_name']],
                orientation='h',
                marker=dict(
                    color=status_colors.get(task.get('status', '进行中')),
                    line=dict(width=0)
                ),
                base=task['start_date'],
                hovertemplate=(
                    f"<b>{task['task_name']}</b><br>" +
                    f"负责人: {task.get('assignee', '未分配')}<br>" +
                    f"进度: {task.get('progress', 0)}%<br>" +
                    f"状态: {task.get('status', '未知')}<br>" +
                    f"开始: {task['start_date'].strftime('%Y-%m-%d')}<br>" +
                    f"结束: {task['end_date'].strftime('%Y-%m-%d')}<br>" +
                    "<extra></extra>"
                ),
                showlegend=False
            ))

            # 未完成部分（浅色）
            remaining_duration = task['duration_ms'] - completed_duration
            if remaining_duration > 0:
                fig.add_trace(go.Bar(
                    x=[remaining_duration],
                    y=[task['task_name']],
                    orientation='h',
                    marker=dict(
                        color=status_colors.get(task.get('status', '进行中')),
                        opacity=0.3,
                        line=dict(width=0)
                    ),
                    base=task['start_date'] + pd.to_timedelta(completed_duration, unit='ms'),
                    hoverinfo='skip',
                    showlegend=False
                ))

        # 添加今天标记线
        today = datetime.now()
        fig.add_vline(
            x=today,
            line_dash="dash",
            line_color=self.color_scheme["danger"],
            annotation_text="今天",
            annotation_position="top"
        )

        # 布局
        fig.update_layout(
            title={
                'text': "📅 项目甘特图",
                'font': {'size': 20, 'color': '#e2e8f0'}
            },
            xaxis=dict(
                title="时间线",
                type='date',
                tickformat='%Y-%m-%d',
                gridcolor='rgba(255,255,255,0.1)'
            ),
            yaxis=dict(
                title="任务",
                autorange="reversed",
                gridcolor='rgba(255,255,255,0.1)'
            ),
            barmode='stack',
            height=max(400, len(tasks) * 40),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#e2e8f0'),
            margin=dict(l=150, r=50, t=80, b=50)
        )

        return fig

    def create_burndown_chart(self, project_data: Dict) -> go.Figure:
        """
        创建燃尽图

        Args:
            project_data: {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "total_hours": 200,
                "daily_progress": [
                    {"date": "2026-07-01", "remaining": 200, "completed": 0},
                    {"date": "2026-07-02", "remaining": 190, "completed": 10},
                    ...
                ]
            }
        """
        if not project_data or "daily_progress" not in project_data:
            return self._create_empty_chart("暂无进度数据")

        df = pd.DataFrame(project_data["daily_progress"])
        df['date'] = pd.to_datetime(df['date'])

        # 计算理想线
        start_date = pd.to_datetime(project_data["start_date"])
        end_date = pd.to_datetime(project_data["end_date"])
        total_hours = project_data["total_hours"]

        date_range = pd.date_range(start=start_date, end=end_date, freq='D')
        total_days = (end_date - start_date).days
        ideal_daily_burn = total_hours / total_days if total_days > 0 else 0

        ideal_line = []
        for i, date in enumerate(date_range):
            ideal_line.append({
                "date": date,
                "ideal_remaining": total_hours - (i * ideal_daily_burn)
            })

        df_ideal = pd.DataFrame(ideal_line)

        # 创建图表
        fig = go.Figure()

        # 理想燃尽线
        fig.add_trace(go.Scatter(
            x=df_ideal['date'],
            y=df_ideal['ideal_remaining'],
            mode='lines',
            name='理想进度',
            line=dict(color=self.color_scheme["info"], dash='dash', width=2),
            hovertemplate='<b>理想进度</b><br>剩余: %{y:.1f}小时<extra></extra>'
        ))

        # 实际燃尽线
        fig.add_trace(go.Scatter(
            x=df['date'],
            y=df['remaining'],
            mode='lines+markers',
            name='实际进度',
            line=dict(color=self.color_scheme["primary"], width=3),
            marker=dict(size=6),
            hovertemplate='<b>实际进度</b><br>剩余: %{y:.1f}小时<extra></extra>'
        ))

        # 完成量区域
        fig.add_trace(go.Scatter(
            x=df['date'],
            y=df['completed'],
            mode='lines',
            name='已完成',
            line=dict(color=self.color_scheme["success"], width=2),
            fill='tozeroy',
            fillcolor='rgba(34, 197, 94, 0.2)',
            hovertemplate='<b>已完成</b><br>%{y:.1f}小时<extra></extra>'
        ))

        # 今天标记
        today = datetime.now()
        if start_date <= today <= end_date:
            fig.add_vline(
                x=today,
                line_dash="dot",
                line_color=self.color_scheme["warning"],
                annotation_text="今天"
            )

        # 布局
        fig.update_layout(
            title={
                'text': "🔥 项目燃尽图",
                'font': {'size': 20, 'color': '#e2e8f0'}
            },
            xaxis=dict(
                title="日期",
                gridcolor='rgba(255,255,255,0.1)'
            ),
            yaxis=dict(
                title="剩余工时（小时）",
                gridcolor='rgba(255,255,255,0.1)'
            ),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#e2e8f0'),
            hovermode='x unified',
            height=500,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )

        return fig

    def create_team_heatmap(self, team_load_data: List[Dict]) -> go.Figure:
        """
        创建团队负载热力图

        Args:
            team_load_data: [
                {
                    "member": "张三",
                    "date": "2026-07-01",
                    "load": 8,  # 工时
                    "capacity": 8
                },
                ...
            ]
        """
        if not team_load_data:
            return self._create_empty_chart("暂无团队数据")

        df = pd.DataFrame(team_load_data)
        df['date'] = pd.to_datetime(df['date'])

        # 计算负载率
        load = pd.to_numeric(df['load'], errors='coerce').fillna(0)
        capacity = pd.to_numeric(df['capacity'], errors='coerce').fillna(0)
        df['load_rate'] = np.where(capacity > 0, load / capacity * 100, np.where(load > 0, 100, 0))

        # 透视表
        pivot = df.pivot(index='member', columns='date', values='load_rate')

        # 创建热力图
        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=[d.strftime('%m-%d') for d in pivot.columns],
            y=pivot.index,
            colorscale=[
                [0, self.color_scheme["success"]],
                [0.7, self.color_scheme["warning"]],
                [1, self.color_scheme["danger"]]
            ],
            text=pivot.values,
            texttemplate='%{text:.0f}%',
            textfont={"size": 10},
            hovertemplate='<b>%{y}</b><br>日期: %{x}<br>负载: %{z:.0f}%<extra></extra>',
            colorbar=dict(
                title="负载率",
                ticksuffix="%"
            )
        ))

        fig.update_layout(
            title={
                'text': "🔥 团队负载热力图",
                'font': {'size': 20, 'color': '#e2e8f0'}
            },
            xaxis=dict(title="日期", side="bottom"),
            yaxis=dict(title="团队成员"),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#e2e8f0'),
            height=max(300, len(pivot) * 40)
        )

        return fig

    def create_profit_funnel(self, profit_breakdown: Dict) -> go.Figure:
        """
        创建利润漏斗图

        Args:
            profit_breakdown: {
                "报价金额": 300000,
                "制作成本": 200000,
                "毛利润": 100000,
                "管理费": 45000,
                "税费": 18000,
                "净利润": 37000
            }
        """
        # 准备数据
        stages = list(profit_breakdown.keys())
        values = list(profit_breakdown.values())

        # 颜色渐变
        colors = [
            self.color_scheme["primary"],
            self.color_scheme["warning"],
            self.color_scheme["info"],
            self.color_scheme["danger"],
            self.color_scheme["danger"],
            self.color_scheme["success"]
        ]

        # 创建漏斗图
        fig = go.Figure(go.Funnel(
            y=stages,
            x=values,
            textposition="inside",
            textinfo="value+percent initial",
            marker=dict(
                color=colors,
                line=dict(width=2, color='rgba(255,255,255,0.3)')
            ),
            hovertemplate='<b>%{y}</b><br>金额: ¥%{x:,.0f}<extra></extra>'
        ))

        fig.update_layout(
            title={
                'text': "💰 利润分解漏斗图",
                'font': {'size': 20, 'color': '#e2e8f0'}
            },
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#e2e8f0', size=12),
            height=500,
            margin=dict(l=150, r=50, t=80, b=50)
        )

        return fig

    def create_project_timeline(self, projects: List[Dict]) -> go.Figure:
        """
        创建项目时间线

        Args:
            projects: [
                {
                    "project_name": "项目A",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-15",
                    "status": "进行中"
                },
                ...
            ]
        """
        if not projects:
            return self._create_empty_chart("暂无项目数据")

        df = pd.DataFrame(projects)
        df['start_date'] = pd.to_datetime(df['start_date'])
        df['end_date'] = pd.to_datetime(df['end_date'])

        # 状态颜色
        status_colors = {
            "未开始": self.color_scheme["info"],
            "进行中": self.color_scheme["primary"],
            "已完成": self.color_scheme["success"],
            "已取消": self.color_scheme["danger"]
        }

        fig = px.timeline(
            df,
            x_start="start_date",
            x_end="end_date",
            y="project_name",
            color="status",
            color_discrete_map=status_colors,
            title="📊 项目时间线"
        )

        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#e2e8f0'),
            height=max(400, len(projects) * 50),
            xaxis_title="时间",
            yaxis_title="项目"
        )

        return fig

    def create_skill_radar(self, member_skills: Dict) -> go.Figure:
        """
        创建技能雷达图

        Args:
            member_skills: {
                "建模": 90,
                "贴图": 85,
                "绑定": 70,
                "动画": 60,
                "优化": 80
            }
        """
        if not member_skills:
            return self._create_empty_chart("暂无技能数据")

        categories = list(member_skills.keys())
        values = list(member_skills.values())

        fig = go.Figure()

        fig.add_trace(go.Scatterpolar(
            r=values,
            theta=categories,
            fill='toself',
            fillcolor='rgba(102, 126, 234, 0.3)',
            line=dict(color=self.color_scheme["primary"], width=2),
            marker=dict(size=8, color=self.color_scheme["primary"]),
            hovertemplate='<b>%{theta}</b><br>熟练度: %{r}%<extra></extra>'
        ))

        fig.update_layout(
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    range=[0, 100],
                    gridcolor='rgba(255,255,255,0.2)'
                ),
                angularaxis=dict(
                    gridcolor='rgba(255,255,255,0.2)'
                ),
                bgcolor='rgba(0,0,0,0)'
            ),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#e2e8f0'),
            title={
                'text': "🎯 技能雷达图",
                'font': {'size': 20, 'color': '#e2e8f0'}
            },
            height=500
        )

        return fig

    def _create_empty_chart(self, message: str) -> go.Figure:
        """创建空图表"""
        fig = go.Figure()

        fig.add_annotation(
            text=message,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=16, color='#94a3b8')
        )

        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            height=400
        )

        return fig


# 使用示例
if __name__ == "__main__":
    viz = AdvancedVisualizer()

    # 1. 甘特图
    tasks = [
        {
            "task_name": "角色建模",
            "start_date": "2026-07-01",
            "end_date": "2026-07-10",
            "progress": 65,
            "assignee": "张三",
            "status": "进行中"
        },
        {
            "task_name": "角色贴图",
            "start_date": "2026-07-08",
            "end_date": "2026-07-15",
            "progress": 30,
            "assignee": "李四",
            "status": "进行中"
        }
    ]

    fig = viz.create_gantt_chart(tasks)
    fig.show()

    # 2. 利润漏斗
    profit = {
        "报价金额": 300000,
        "制作成本": 200000,
        "毛利润": 100000,
        "管理费": 45000,
        "税费": 18000,
        "净利润": 37000
    }

    fig = viz.create_profit_funnel(profit)
    fig.show()
