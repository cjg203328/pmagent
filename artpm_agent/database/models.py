"""
数据库模型设计 - SQLAlchemy ORM
"""
from sqlalchemy import create_engine, inspect, Column, Integer, String, Float, DateTime, Text, ForeignKey, Boolean, func
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker, selectinload
from datetime import datetime
from typing import List, Dict, Optional
import json
import os
import atexit
import logging
from weakref import WeakSet, finalize

logger = logging.getLogger(__name__)

# --- 资源生命周期治理（技术债 #7）---
# 集中登记 DatabaseManager 创建的 SQLAlchemy Engine，便于统一回收，
# 消除未关闭连接导致的 "unclosed database" ResourceWarning。
_ACTIVE_ENGINES: WeakSet = WeakSet()


def _dispose_engine(engine):
    """Dispose one Engine and remove it from the process registry."""
    _ACTIVE_ENGINES.discard(engine)
    try:
        engine.dispose()
    except Exception:  # noqa: BLE001 - cleanup must remain best effort
        pass


def _dispose_all_engines():
    """解释器退出时回收所有仍存活的 Engine（最后安全网）。"""
    for _engine in list(_ACTIVE_ENGINES):
        try:
            _engine.dispose()
        except Exception:  # noqa: BLE001 - 退出阶段不再抛出
            pass
    _ACTIVE_ENGINES.clear()


atexit.register(_dispose_all_engines)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class TenantScopedMixin:
    """Common database-enforced tenant/workspace ownership columns."""

    tenant_id = Column(String(128), nullable=False, default="local", index=True)
    workspace_id = Column(
        String(128), nullable=False, default="local-default", index=True
    )


class Project(TenantScopedMixin, Base):
    """项目表"""
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_name = Column(String(200), nullable=False, index=True)
    client = Column(String(100), nullable=False, index=True)
    status = Column(String(50), default='待开始', index=True)  # 待开始, 进行中, 已完成, 已取消

    # 金额信息
    quote_amount = Column(Float, nullable=False)
    cost = Column(Float)
    gross_profit = Column(Float)
    net_profit = Column(Float)
    profit_rate = Column(Float)

    # 时间信息
    start_date = Column(DateTime)
    deadline = Column(DateTime, index=True)
    completed_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 联系信息
    contact_person = Column(String(100))
    contact_phone = Column(String(50))
    contact_email = Column(String(100))

    # 备注
    notes = Column(Text)
    risk_level = Column(String(20))  # low, medium, high

    # 关联
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "id": self.id,
            "project_name": self.project_name,
            "client": self.client,
            "status": self.status,
            "quote_amount": self.quote_amount,
            "cost": self.cost,
            "net_profit": self.net_profit,
            "profit_rate": self.profit_rate,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "created_at": self.created_at.isoformat(),
            "contact_person": self.contact_person,
            "risk_level": self.risk_level
        }


class Asset(TenantScopedMixin, Base):
    """资产表"""
    __tablename__ = 'assets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)

    # 资产信息
    asset_name = Column(String(200), nullable=False)
    asset_type = Column(String(50))  # 角色, 场景, 特效, 动画, UI等
    quantity = Column(Integer, default=1)
    unit_price = Column(Float)
    total_price = Column(Float)

    # 制作信息
    status = Column(String(50), default='未开始')  # 未开始, 制作中, 待审核, 已完成
    progress = Column(Integer, default=0)  # 0-100
    complexity = Column(String(20))  # simple, medium, complex

    # 要求
    requirements = Column(Text)
    reference_images = Column(Text)  # JSON array of image URLs

    # 时间
    estimated_hours = Column(Float)
    actual_hours = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    # 关联
    project = relationship("Project", back_populates="assets")
    tasks = relationship("Task", back_populates="asset")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "total_price": self.total_price,
            "status": self.status,
            "progress": self.progress
        }


class TeamMember(TenantScopedMixin, Base):
    """团队成员表"""
    __tablename__ = 'team_members'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    role = Column(String(50))  # 建模师, 贴图师, 动画师, PM等

    # 技能
    skills = Column(Text)  # JSON array
    skill_level = Column(String(20))  # junior, intermediate, senior

    # 联系方式
    phone = Column(String(50))
    email = Column(String(100))
    wechat = Column(String(100))

    # 工作信息
    employment_type = Column(String(20))  # full-time, part-time, freelance
    hourly_rate = Column(Float)
    is_active = Column(Boolean, default=True)

    # 统计
    total_projects = Column(Integer, default=0)
    total_hours = Column(Float, default=0)
    avg_quality_score = Column(Float)

    created_at = Column(DateTime, default=datetime.now)

    # 关联
    tasks = relationship("Task", back_populates="assignee")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "skills": json.loads(self.skills) if self.skills else [],
            "skill_level": self.skill_level,
            "is_active": self.is_active
        }


class Task(TenantScopedMixin, Base):
    """任务表"""
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey('assets.id'), index=True)
    assignee_id = Column(Integer, ForeignKey('team_members.id'), index=True)

    # 任务信息
    task_name = Column(String(200), nullable=False)
    task_type = Column(String(50))  # modeling, texturing, rigging, animation等
    description = Column(Text)

    # 状态
    status = Column(String(50), default='未开始', index=True)  # 未开始, 进行中, 待审核, 已完成, 已取消
    priority = Column(String(20), default='medium')  # low, medium, high, urgent
    progress = Column(Integer, default=0)

    # 时间
    estimated_hours = Column(Float)
    actual_hours = Column(Float)
    start_date = Column(DateTime)
    due_date = Column(DateTime, index=True)
    completed_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    # 质量
    quality_score = Column(Float)  # 1-5
    revision_count = Column(Integer, default=0)

    # 关联
    project = relationship("Project", back_populates="tasks")
    asset = relationship("Asset", back_populates="tasks")
    assignee = relationship("TeamMember", back_populates="tasks", lazy="joined")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "task_name": self.task_name,
            "status": self.status,
            "progress": self.progress,
            "assignee": self.assignee.name if self.assignee else None,
            "due_date": self.due_date.isoformat() if self.due_date else None
        }


class Document(TenantScopedMixin, Base):
    """文档表"""
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)

    # 文档信息
    document_type = Column(String(50), nullable=False)  # 报价单, 合同, 验收单等
    file_name = Column(String(200), nullable=False)
    file_path = Column(String(500))
    file_type = Column(String(20))  # excel, pdf, image等
    file_size = Column(Integer)

    # 解析结果
    parsed_data = Column(Text)  # JSON
    confidence_score = Column(Float)

    # 时间
    upload_date = Column(DateTime, default=datetime.now, nullable=False)
    parsed_date = Column(DateTime)

    # 关联
    project = relationship("Project", back_populates="documents")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "document_type": self.document_type,
            "file_name": self.file_name,
            "upload_date": self.upload_date.isoformat(),
            "parsed_data": json.loads(self.parsed_data) if self.parsed_data else None
        }


class KnowledgeBase(TenantScopedMixin, Base):
    """知识库表 - 用于RAG"""
    __tablename__ = 'knowledge_base'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 知识内容
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(50), index=True)  # pricing, timeline, requirements等
    source = Column(String(200))

    # 向量化
    embedding = Column(Text)  # JSON array

    # 元数据
    tags = Column(Text)  # JSON array
    client = Column(String(100), index=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "category": self.category,
            "source": self.source
        }


class Delivery(TenantScopedMixin, Base):
    """交付记录表 - 产品交付阶段的每次交付包（草稿/已交付/已验收/已驳回）"""
    __tablename__ = 'deliveries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)
    delivery_no = Column(String(50), nullable=False, unique=True)
    title = Column(String(200))
    status = Column(String(30), default='草稿')
    items_json = Column(Text)            # JSON: [{asset_id, asset_name, version, status}]
    delivered_by = Column(String(100))
    delivered_at = Column(DateTime)
    accepted_at = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class AssetVersion(TenantScopedMixin, Base):
    """资产版本表 - 单个资产的交付版本历史（待审核/已通过/已驳回）"""
    __tablename__ = 'asset_versions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(Integer, ForeignKey('assets.id'), nullable=False, index=True)
    version = Column(String(20), nullable=False)
    status = Column(String(30), default='待审核')
    note = Column(Text)
    file_ref = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_url: str | None = None):
        if db_url is None:
            from artpm_agent.config import resolve_state_path

            db_path = resolve_state_path("artpm.db", "DB_PATH")
            db_url = f"sqlite:///{db_path.as_posix()}"
        engine_options = {"pool_pre_ping": True} if db_url.startswith("postgresql") else {}
        self.engine = create_engine(db_url, echo=False, **engine_options)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        from artpm_agent.database.tenant_session import install_tenant_session_hooks

        install_tenant_session_hooks(self.SessionLocal)
        _ACTIVE_ENGINES.add(self.engine)
        self._engine_finalizer = finalize(self, _dispose_engine, self.engine)

        try:
            # Reject structurally incompatible legacy tables before any stamp
            # or migration can modify them. Only the newly introduced tenant
            # ownership columns may be absent at this stage.
            self._validate_existing_schema(allow_missing_tenant_scope=True)
            # Alembic 是生产 schema 的唯一来源。SQLite 本地环境保留显式
            # fallback 以兼容最小离线安装；PostgreSQL 不能绕过 tenant/RLS 迁移。
            try:
                from artpm_agent.database.migrate import ensure_schema

                ensure_schema(self.engine)
            except Exception as exc:
                if self.engine.dialect.name == "postgresql":
                    logger.error(
                        "PostgreSQL schema migration failed; refusing create_all fallback",
                        exc_info=True,
                    )
                    raise RuntimeError(
                        "PostgreSQL schema migration failed; database startup is blocked"
                    ) from exc
                fallback_enabled = os.getenv(
                    "ARTPM_SQLITE_FALLBACK", "true"
                ).strip().lower() in {"1", "true", "yes", "on"}
                if not fallback_enabled:
                    raise
                logger.warning(
                    "Alembic 迁移不可用，回退至 Base.metadata.create_all: %s", exc
                )
                Base.metadata.create_all(self.engine)
            # Existing databases need migrations to add tenant columns before
            # model compatibility can be evaluated.
            self._validate_existing_schema()
        except BaseException:
            self.close()
            raise

    # ===== 资源回收（技术债 #7）=====
    def close(self):
        """释放 Engine 与连接池，并从全局登记表中移除。

        幂等：重复调用安全。测试或长生命周期场景结束后应调用，
        避免未关闭的 SQLite 连接触发 ResourceWarning。
        """
        finalizer = getattr(self, "_engine_finalizer", None)
        if finalizer is not None and finalizer.alive:
            finalizer()
        else:
            _dispose_engine(self.engine)

    def dispose(self):
        """SQLAlchemy 命名习惯别名，等同于 close()。"""
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def get_session(self, tenant_context=None):
        """获取数据库会话"""
        session = self.SessionLocal()
        if tenant_context is not None:
            session.info["tenant_context"] = tenant_context
        return session

    def _validate_existing_schema(self, *, allow_missing_tenant_scope=False):
        """Reject incompatible existing tables without modifying the database."""
        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            required_columns = {column.name for column in table.columns}
            missing = required_columns - existing_columns
            if allow_missing_tenant_scope:
                missing -= {"tenant_id", "workspace_id"}
            if missing:
                raise RuntimeError(
                    f"Database table '{table.name}' is incompatible; missing columns: "
                    f"{', '.join(sorted(missing))}. Back up and migrate the database."
                )

    # ===== Project CRUD =====

    def create_project(self, project_data: Dict) -> Project:
        """创建项目"""
        session = self.get_session()
        try:
            project = Project(**project_data)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project
        finally:
            session.close()

    def get_project(self, project_id: int) -> Optional[Project]:
        """获取项目"""
        session = self.get_session()
        try:
            return session.query(Project).options(selectinload(Project.tasks)).filter(Project.id == project_id).first()
        finally:
            session.close()

    def list_projects(self, status: str = None, client: str = None, limit: int = 50) -> List[Project]:
        """列出项目"""
        session = self.get_session()
        try:
            query = session.query(Project)

            if status:
                query = query.filter(Project.status == status)
            if client:
                query = query.filter(Project.client == client)

            return query.options(selectinload(Project.tasks)).order_by(Project.created_at.desc()).limit(limit).all()
        finally:
            session.close()

    def update_project(self, project_id: int, update_data: Dict) -> Optional[Project]:
        """更新项目"""
        allowed = {column.name for column in Project.__table__.columns} - {"id", "created_at"}
        invalid = set(update_data) - allowed
        if invalid:
            raise ValueError(f"Unknown project fields: {', '.join(sorted(invalid))}")
        session = self.get_session()
        try:
            project = session.query(Project).filter(Project.id == project_id).first()
            if project:
                for key, value in update_data.items():
                    setattr(project, key, value)
                session.commit()
                session.refresh(project)
            return project
        finally:
            session.close()

    # ===== Asset CRUD =====

    def create_asset(self, asset_data: Dict) -> Asset:
        """创建资产"""
        session = self.get_session()
        try:
            asset = Asset(**asset_data)
            session.add(asset)
            session.commit()
            session.refresh(asset)
            return asset
        finally:
            session.close()

    def get_project_assets(self, project_id: int) -> List[Asset]:
        """获取项目的所有资产"""
        session = self.get_session()
        try:
            return session.query(Asset).filter(Asset.project_id == project_id).all()
        finally:
            session.close()

    # ===== Task CRUD =====

    def create_task(self, task_data: Dict) -> Task:
        """创建任务"""
        session = self.get_session()
        try:
            task = Task(**task_data)
            session.add(task)
            session.commit()
            session.refresh(task)
            return task
        finally:
            session.close()

    def get_tasks_by_status(self, status: str) -> List[Task]:
        """按状态获取任务"""
        session = self.get_session()
        try:
            return session.query(Task).filter(Task.status == status).all()
        finally:
            session.close()

    def get_member_tasks(self, member_id: int) -> List[Task]:
        """获取成员的任务"""
        session = self.get_session()
        try:
            return session.query(Task).filter(Task.assignee_id == member_id).all()
        finally:
            session.close()

    def get_task(self, task_id: int) -> Optional[Task]:
        """按ID获取任务"""
        session = self.get_session()
        try:
            return session.query(Task).filter(Task.id == task_id).first()
        finally:
            session.close()

    def get_tasks(self, project_id: Optional[int] = None) -> List[Task]:
        """获取任务列表，可按项目过滤"""
        session = self.get_session()
        try:
            query = session.query(Task)
            if project_id is not None:
                query = query.filter(Task.project_id == project_id)
            return query.all()
        finally:
            session.close()

    def update_task(self, task_id: int, **fields) -> bool:
        """更新任务字段（如质量评分、状态、返工次数）"""
        session = self.get_session()
        try:
            task = session.query(Task).filter(Task.id == task_id).first()
            if task is None:
                return False
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            session.commit()
            return True
        finally:
            session.close()

    # ===== Document persistence (需求评估 intake→落库) =====

    def save_document(self, project_id, document_type, file_name, parsed_data=None,
                      file_path=None, file_type=None, file_size=None,
                      confidence_score=None) -> "Document":
        """持久化一份已解析的业务文档（如报价单），修复 intake→落库断层。"""
        session = self.get_session()
        try:
            doc = Document(
                project_id=project_id,
                document_type=document_type,
                file_name=file_name,
                file_path=file_path,
                file_type=file_type,
                file_size=file_size,
                parsed_data=json.dumps(parsed_data, ensure_ascii=False)
                if isinstance(parsed_data, (dict, list)) else parsed_data,
                confidence_score=confidence_score,
                parsed_date=datetime.now() if parsed_data else None,
            )
            session.add(doc)
            session.commit()
            session.refresh(doc)
            return doc
        finally:
            session.close()

    def get_documents(self, project_id=None, document_type=None) -> List["Document"]:
        """按项目/类型查询文档。"""
        session = self.get_session()
        try:
            q = session.query(Document)
            if project_id is not None:
                q = q.filter(Document.project_id == project_id)
            if document_type:
                q = q.filter(Document.document_type == document_type)
            return q.order_by(Document.upload_date.desc()).all()
        finally:
            session.close()

    # ===== Knowledge base writes (复盘总结经验沉淀) =====

    def add_knowledge(self, title, content, category="lessons", source=None,
                      client=None, tags=None) -> "KnowledgeBase":
        """写入一条知识库记录（如复盘经验教训），供后续 RAG 复用。"""
        session = self.get_session()
        try:
            kb = KnowledgeBase(
                title=title,
                content=content,
                category=category,
                source=source,
                client=client,
                tags=json.dumps(tags, ensure_ascii=False) if isinstance(tags, list) else tags,
            )
            session.add(kb)
            session.commit()
            session.refresh(kb)
            return kb
        finally:
            session.close()

    # ===== Delivery & versions (产品交付) =====

    def create_delivery(self, project_id, delivery_no, title=None, items=None,
                        delivered_by=None, status="草稿") -> "Delivery":
        """创建一次交付记录。"""
        session = self.get_session()
        try:
            d = Delivery(
                project_id=project_id,
                delivery_no=delivery_no,
                title=title,
                items_json=json.dumps(items, ensure_ascii=False) if items else None,
                delivered_by=delivered_by,
                status=status,
            )
            session.add(d)
            session.commit()
            session.refresh(d)
            return d
        finally:
            session.close()

    def get_project_deliveries(self, project_id) -> List["Delivery"]:
        session = self.get_session()
        try:
            return session.query(Delivery).filter(
                Delivery.project_id == project_id
            ).order_by(Delivery.created_at.desc()).all()
        finally:
            session.close()

    def create_asset_version(self, asset_id, version, status="待审核",
                             note=None, file_ref=None) -> "AssetVersion":
        """记录某个资产的一个交付版本。"""
        session = self.get_session()
        try:
            v = AssetVersion(
                asset_id=asset_id, version=version, status=status,
                note=note, file_ref=file_ref,
            )
            session.add(v)
            session.commit()
            session.refresh(v)
            return v
        finally:
            session.close()

    def get_asset_versions(self, asset_id) -> List["AssetVersion"]:
        session = self.get_session()
        try:
            return session.query(AssetVersion).filter(
                AssetVersion.asset_id == asset_id
            ).order_by(AssetVersion.created_at.desc()).all()
        finally:
            session.close()

    # ===== TeamMember CRUD =====

    def create_member(self, member_data: Dict) -> TeamMember:
        """创建团队成员"""
        member_data = dict(member_data)
        if isinstance(member_data.get("skills"), (list, tuple)):
            member_data["skills"] = json.dumps(member_data["skills"], ensure_ascii=False)
        session = self.get_session()
        try:
            member = TeamMember(**member_data)
            session.add(member)
            session.commit()
            session.refresh(member)
            return member
        finally:
            session.close()

    def list_members(self, is_active: bool = True) -> List[TeamMember]:
        """列出团队成员"""
        session = self.get_session()
        try:
            query = session.query(TeamMember)
            if is_active is not None:
                query = query.filter(TeamMember.is_active == is_active)
            return query.all()
        finally:
            session.close()

    # ===== Statistics =====

    def get_project_stats(self) -> Dict:
        """获取项目统计"""
        session = self.get_session()
        try:
            total = session.query(Project).count()
            in_progress = session.query(Project).filter(Project.status == '进行中').count()
            completed = session.query(Project).filter(Project.status == '已完成').count()

            # 计算总收入
            total_revenue = session.query(func.sum(Project.quote_amount)).filter(
                Project.status == '已完成'
            ).scalar() or 0

            # 平均利润率
            avg_profit_rate = session.query(func.avg(Project.profit_rate)).filter(
                Project.profit_rate.isnot(None)
            ).scalar() or 0

            return {
                "total_projects": total,
                "in_progress": in_progress,
                "completed": completed,
                "total_revenue": float(total_revenue),
                "avg_profit_rate": float(avg_profit_rate)
            }
        finally:
            session.close()


# ============================================================================
# 遗留表（Legacy tables）
# ----------------------------------------------------------------------------
# 以下 6 张表历史上由 memory/sqlite_manager.py 以原生 SQL 创建并维护，未被
# SQLAlchemy ORM 建模，导致 Alembic 无法覆盖、产生 schema 漂移（真实库有 14 张表
# 而 ORM 仅 8 张）。此处补齐 ORM 模型并新增迁移 0002，使全量 schema 受 Alembic 管理。
#
# staff / quotes / reminders 当前无业务代码读写，属于历史残留，仅保留以便数据保全；
# 若确认不再需要，可在后续迁移中 DROP。task_assignments / progress_updates /
# operation_logs 仍被 sqlite_manager.py 以原生 SQL 使用。
# ============================================================================

class Staff(TenantScopedMixin, Base):
    """遗留：人员表（历史残留，当前无业务代码读写）。"""
    __tablename__ = "staff"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    level = Column(String)
    skills = Column(Text)
    daily_cost = Column(Float)
    contact_wecom = Column(String)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.now)


class Quote(TenantScopedMixin, Base):
    """遗留：报价单解析结果表（历史残留，当前无业务代码读写）。"""
    __tablename__ = "quotes"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"))
    document_type = Column(String)
    file_path = Column(String)
    file_hash = Column(String)
    parsed_data = Column(Text)
    profit_analysis = Column(Text)
    confidence = Column(Float)
    created_at = Column(DateTime, default=datetime.now)


class Reminder(TenantScopedMixin, Base):
    """遗留：催办提醒表（历史残留，当前无业务代码读写）。"""
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"))
    reminder_type = Column(String)
    content = Column(Text)
    recipients = Column(Text)
    channel = Column(String)
    sent_status = Column(String, default="pending")
    scheduled_at = Column(DateTime)
    sent_at = Column(DateTime)


class OperationLog(TenantScopedMixin, Base):
    """遗留：操作日志表（仍被 sqlite_manager.py 原生 SQL 使用）。"""
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    operation = Column(String)
    skill_name = Column(String)
    inputs = Column(Text)
    outputs = Column(Text)
    success = Column(Boolean)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class ProgressUpdate(TenantScopedMixin, Base):
    """遗留：进度更新表（仍被 sqlite_manager.py 原生 SQL 使用）。"""
    __tablename__ = "progress_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    progress = Column(Float)
    note = Column(Text)
    updated_by = Column(String)
    updated_at = Column(DateTime, default=datetime.now)


class TaskAssignment(TenantScopedMixin, Base):
    """遗留：任务分派表（仍被 sqlite_manager.py 原生 SQL 使用）。"""
    __tablename__ = "task_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    staff_id = Column(String, ForeignKey("staff.id"), nullable=False)
    workload_ratio = Column(Float, default=1.0)
    assigned_at = Column(DateTime, default=datetime.now)


# 使用示例
if __name__ == "__main__":
    from sqlalchemy import func

    # 初始化数据库
    db = DatabaseManager()

    # 创建项目
    project = db.create_project({
        "project_name": "角色模型制作",
        "client": "腾讯",
        "quote_amount": 30000,
        "cost": 20000,
        "status": "进行中",
        "deadline": datetime(2026, 8, 15)
    })
    print(f"创建项目: {project.id} - {project.project_name}")

    # 创建资产
    asset = db.create_asset({
        "project_id": project.id,
        "asset_name": "主角色模型",
        "asset_type": "角色",
        "quantity": 1,
        "unit_price": 8000,
        "total_price": 8000
    })
    print(f"创建资产: {asset.asset_name}")

    # 查询项目
    projects = db.list_projects(status="进行中")
    print(f"进行中的项目: {len(projects)}个")

    # 统计
    stats = db.get_project_stats()
    print(f"项目统计: {stats}")
