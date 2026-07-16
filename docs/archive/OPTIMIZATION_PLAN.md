# ArtPM Agent 全量优化方案

## 📊 当前状态分析

### 项目规模
- **代码行数**: ~10,714 行
- **Python 文件**: 40 个
- **文档文件**: 20+ 个

### 核心问题
1. **代码冗余** ✅ FIXED
   - 8个备份/测试文件 → 已清理
   - 3个MCP客户端版本 → 已统一为mcp_client_real.py

2. **架构混乱** 🔧 IN PROGRESS
   - 同步/异步混用 → 需要创建同步包装器
   - Skills接口不统一 → 需要统一基类

3. **功能不完整** 📋 PLANNED
   - Skills多为stub实现
   - Excel解析器未实现
   - 向量嵌入使用简化版本

4. **缺少工程化** 📋 PLANNED  
   - 无结构化日志
   - 异常处理不统一
   - 缺少单元测试
   - 无健康检查

---

## 🎯 优化策略

### 阶段1: 清理重构 (当前阶段)

#### 1.1 代码清理 ✅
- [x] 删除测试文件 (test_*.py)
- [x] 删除备份文件 (app_backup.py, app_old.py)
- [x] 统一MCP客户端 (保留mcp_client_real.py)

#### 1.2 日志系统 🔧
```python
# artpm_agent/utils/logger.py
import logging
from pathlib import Path

def setup_logging(level=logging.INFO):
    """配置全局日志"""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=level,
        format='[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
        handlers=[
            logging.FileHandler(log_dir / "artpm.log"),
            logging.StreamHandler()
        ]
    )

def get_logger(name: str):
    """获取logger实例"""
    return logging.getLogger(name)
```

#### 1.3 同步LLM包装器 🔧
```python
# artpm_agent/utils/llm_client.py  
def create_llm_client(config: dict):
    """创建带同步包装的LLM客户端"""
    # 保持现有异步实现
    async_client = UniversalLLMClient(...)
    
    # 添加同步包装
    class SyncLLMClient:
        def chat(self, messages, **kwargs):
            import asyncio
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                async_client.chat(messages, **kwargs)
            )
    
    return SyncLLMClient()
```

#### 1.4 统一Skills接口 🔧
```python
# artpm_agent/skills/base_skill.py
class BaseSkill(ABC):
    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.logger = get_logger(self.skill_name)
    
    @abstractmethod
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行技能"""
        pass
    
    def run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """带异常处理和日志的执行包装"""
        try:
            self.logger.info(f"执行 {self.skill_name}")
            result = self.execute(inputs)
            self.logger.info(f"执行成功: {self.skill_name}")
            return result
        except Exception as e:
            self.logger.error(f"执行失败 {self.skill_name}: {e}")
            return {"success": False, "error": str(e)}
```

---

### 阶段2: 功能完善

#### 2.1 Excel解析器实现
```python
# artpm_agent/parsers/excel_parser.py
class ExcelQuoteParser:
    """真实的Excel报价单解析器"""
    
    def parse(self, file_path: str) -> Dict:
        """解析Excel报价单"""
        import pandas as pd
        
        df = pd.read_excel(file_path)
        
        # 智能识别项目名称 (第1-3行中包含"项目"的单元格)
        project_name = self._extract_project_name(df)
        
        # 智能识别客户名称 (包含"客户"、"公司"的行)
        client_name = self._extract_client(df)
        
        # 识别资产表格 (包含"资产"、"名称"、"单价"等列)
        assets = self._extract_assets(df)
        
        # 计算总金额
        total_amount = sum(a['total_price'] for a in assets)
        
        return {
            "success": True,
            "document_type": "报价单",
            "extracted_data": {
                "project_info": {
                    "project_name": project_name,
                    "client_name": client_name,
                },
                "assets": assets,
                "total_amount": total_amount
            }
        }
```

#### 2.2 Task Allocator智能分配
```python
class TaskAllocator(BaseSkill):
    def execute(self, inputs):
        tasks = inputs["tasks"]
        
        # 从数据库获取真实团队成员
        members = self.context["memory"].get_all_staff()
        
        # 按技能和负载智能匹配
        allocations = []
        for task in tasks:
            best_member = self._find_best_match(
                task,
                members,
                consider_load=True,
                consider_skills=True
            )
            allocations.append({
                "task": task,
                "assignee": best_member,
                "match_score": self._calculate_match_score(task, best_member)
            })
        
        return {"success": True, "allocations": allocations}
```

#### 2.3 Progress Tracker真实检查
```python
class ProgressTracker(BaseSkill):
    def execute(self, inputs):
        # 从数据库获取真实项目
        db = self.context["memory"].db
        projects = db.list_projects(status="进行中")
        
        warnings = []
        for proj in projects:
            days_left = (proj.deadline - datetime.now()).days
            if days_left <= inputs["warning_days_ahead"]:
                warnings.append({
                    "project": proj.project_name,
                    "severity": "high" if days_left <=0 else "medium",
                    "message": f"项目{proj.project_name}{'已逾期' if days_left <=0 else f'还有{days_left}天截止'}"
                })
        
        return {"success": True, "warnings": warnings}
```

---

###阶段3: 性能优化

#### 3.1 添加缓存
```python
# artpm_agent/utils/cache.py
from functools import lru_cache
import pickle
from pathlib import Path

class DiskCache:
    def __init__(self, cache_dir=".cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
    
    def get(self, key: str):
        """获取缓存"""
        cache_file = self.cache_dir / f"{key}.pkl"
        if cache_file.exists():
            return pickle.load(open(cache_file, 'rb'))
        return None
    
    def set(self, key: str, value):
        """设置缓存"""
        cache_file = self.cache_dir / f"{key}.pkl"
        pickle.dump(value, open(cache_file, 'wb'))
```

#### 3.2 向量嵌入优化
```python
# 使用真实的Sentence Transformers (可选)
from sentence_transformers import SentenceTransformer

class VectorStore:
    def __init__(self, model_name="paraphrase-multilingual-MiniLM-L12-v2"):
        self.model = SentenceTransformer(model_name)
    
    def get_embedding(self, text: str):
        """获取真实嵌入"""
        return self.model.encode(text).tolist()
```

#### 3.3 数据库查询优化
```python
# 添加索引
class DatabaseManager:
    def _create_indexes(self):
        """创建数据库索引"""
        with self.engine.connect() as conn:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_projects_status 
                ON projects(status);
                
                CREATE INDEX IF NOT EXISTS idx_projects_client 
                ON projects(client);
                
                CREATE INDEX IF NOT EXISTS idx_tasks_status 
                ON tasks(status);
            """)
```

---

### 阶段4: 企业级增强

#### 4.1 企业微信集成
```python
# artpm_agent/integrations/wecom.py
import requests

class WeComClient:
    """企业微信客户端"""
    
    def __init__(self, corp_id, secret):
        self.corp_id = corp_id
        self.secret = secret
        self.access_token = self._get_access_token()
    
    def send_message(self, user_id, message):
        """发送消息"""
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={self.access_token}"
        data = {
            "touser": user_id,
            "msgtype": "text",
            "text": {"content": message}
        }
        return requests.post(url, json=data)
```

#### 4.2 API服务
```python
# artpm_agent/api/main.py
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat(request: ChatRequest):
    """聊天接口"""
    agent = ArtPMAgent()
    response = agent.chat(request.message)
    return {"response": response}

@app.post("/calculate")
def calculate_profit(quote_amount: float, cost: float):
    """利润计算接口"""
    agent = ArtPMAgent()
    result = agent.calculate_quote({
        "total_amount": quote_amount,
        "cost": cost
    })
    return result
```

#### 4.3 健康检查
```python
# artpm_agent/utils/health.py
def check_health() -> Dict:
    """系统健康检查"""
    checks = {
        "database": check_database_connection(),
        "llm": check_llm_availability(),
        "mcp": check_mcp_status(),
        "disk_space": check_disk_space()
    }
    
    all_healthy = all(checks.values())
    
    return {
        "status": "healthy" if all_healthy else "degraded",
        "checks": checks
    }
```

---

## 📝 优化后的项目结构

```
artpm_agent/
├── app.py                      # Streamlit UI (优化后)
├── agent.py                    # Agent核心 (添加日志)
├── config.py                   # 配置管理
│
├── core/                       # 核心模块
│   ├── llm_client.py          # 统一LLM客户端 (同步包装)
│   ├── mcp_client_real.py     # MCP客户端 (唯一版本)
│   ├── rag_system.py          # RAG系统
│   └── token_monitor.py       # Token监控
│
├── skills/                     # Skills模块 (完整实现)
│   ├── base_skill.py          # 统一基类 (添加日志和异常处理)
│   ├── skill_router.py        # 路由器
│   ├── mcp_skills.py          # MCP Skills
│   └── business_skills/       # 业务Skills (新增目录)
│       ├── quote_calculator.py
│       ├── task_allocator.py
│       ├── progress_tracker.py
│       ├── reminder_bot.py
│       └── document_parser.py
│
├── memory/                     # 记忆管理
│   ├── memory_manager.py      # 记忆管理器 (优化检索)
│   ├── sqlite_manager.py      # SQLite管理
│   └── vector_store.py        # 向量存储 (优化嵌入)
│
├── database/                   # 数据库
│   └── models.py              # 数据模型 (添加索引)
│
├── parsers/                    # 解析器 (完整实现)
│   ├── excel_parser.py        # Excel解析 (完整实现)
│   └── ocr_parser.py          # OCR解析
│
├── utils/                      # 工具函数 (新增)
│   ├── __init__.py
│   ├── llm_client.py          # LLM客户端封装 (同步包装)
│   ├── logger.py              # 日志系统 (新增)
│   ├── cache.py               # 缓存系统 (新增)
│   ├── file_utils.py          # 文件工具
│   └── validators.py          # 验证器
│
├── integrations/               # 集成 (新增)
│   ├── wecom.py               # 企业微信
│   └── email.py               # 邮件发送
│
├── api/                        # API服务 (新增)
│   └── main.py                # FastAPI入口
│
└── tests/                      # 单元测试 (新增)
    ├── test_skills.py
    ├── test_parser.py
    └── test_agent.py
```

---

## 📈 性能指标预期

### 优化前
- 启动时间: ~3-5秒
- 首次响应: ~2-4秒
- 内存占用: ~200-300MB
- 测试覆盖率: 0%

### 优化后 (预期)
- 启动时间: ~1-2秒 (缓存配置)
- 首次响应: ~1-2秒 (缓存 + 优化)
- 内存占用: ~150-250MB (清理冗余)
- 测试覆盖率: >60%

---

## 🔧 立即执行项 (优先级排序)

### P0 - 立即执行 ✅
1. [x] 清理冗余文件
2. [ ] 创建日志系统
3. [ ] 修复同步/异步问题
4. [ ] 统一异常处理

### P1 - 本周完成 📋
5. [ ] 实现Excel解析器
6. [ ] 完善Skills业务逻辑
7. [ ] 优化UI错误提示
8. [ ] 添加配置验证

### P2 - 下周完成 📋
9. [ ] 添加缓存机制
10. [ ] 优化向量嵌入
11. [ ] 企业微信集成
12. [ ] 编写单元测试

### P3 - 长期优化 📋
13. [ ] API服务开发
14. [ ] 性能监控
15. [ ] 部署文档
16. [ ] 用户手册

---

## 💡 建议

1. **保持渐进式优化** - 不要一次性重写所有代码
2. **保持向后兼容** - 旧的API保持可用,逐步迁移
3. **添加测试** - 每个新功能都要有测试
4. **文档同步** - 代码和文档同步更新
5. **版本控制** - 使用Git管理,做好分支和标签

---

生成时间: 2026-07-11  
版本: v1.0
