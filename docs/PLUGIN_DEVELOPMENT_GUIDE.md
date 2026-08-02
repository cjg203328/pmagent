# 插件开发指南

**版本**: 2.0
**更新日期**: 2026-08-02

外部技能插件是**受信任的部署代码**，不是沙箱。它们在 ArtPM 进程内直接执行，
因此加载器默认关闭，且只由部署环境变量控制。安全模型概览见
`docs/PLUGIN_SYSTEM.md`；本文是完整的开发流程。

---

## 📚 目录

- [信任模型](#信任模型)
- [快速开始](#快速开始)
- [manifest 契约](#manifest-契约)
- [技能实现](#技能实现)
- [启用与加载](#启用与加载)
- [失败诊断](#失败诊断)
- [最佳实践](#最佳实践)

---

## 信任模型

加载一个插件模块必须**同时**满足以下全部条件，缺一即不执行：

1. `ARTPM_PLUGINS_ENABLED=true`（默认 false，默认不加载任何代码）
2. 插件位于绝对路径的受信任根目录下（相对路径直接报错）
3. 插件 ID 在 `ARTPM_PLUGIN_ALLOWLIST` 中显式列出
4. `artpm-plugin.json` 里的 `module_sha256` 与模块字节完全匹配
5. 加载路径上没有符号链接或 Windows junction
6. 技能名不与内置技能或已加载插件冲突

单个插件失败会被隔离进 `failures`，不影响其他插件与内置技能。

---

## 快速开始

### 1. 创建插件目录

受信任根目录的**每个直接子目录**是一个插件：

```bash
mkdir -p /opt/artpm-plugins/asset-review
cd /opt/artpm-plugins/asset-review
```

### 2. 编写技能模块

`plugin.py`：

```python
"""Asset review skill exported to ArtPM."""

from typing import Any, Dict

from artpm_agent.skills.base_skill import BaseSkill


class AssetReviewSkill(BaseSkill):
    # 必须与 manifest 里的 skills[].name 完全一致
    skill_name = "asset_review"
    description = "审阅资产包并给出问题清单"
    version = "1.0.0"

    input_schema = {
        "type": "object",
        "properties": {
            "package_path": {"type": "string", "description": "资产包路径"},
        },
        "required": ["package_path"],
    }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        package_path = inputs["package_path"]
        return {
            "success": True,
            "data": {"package_path": package_path, "issues": []},
        }
```

`execute` 是唯一必须实现的抽象方法。`run()` 由基类提供，负责校验、
计时、异常兜底并补上 `skill` 字段 —— 不要覆盖它。

### 3. 计算模块摘要

摘要是**单个模块文件字节**的 SHA-256，不是整个目录、也不是多文件拼接：

```bash
# Linux/macOS
sha256sum plugin.py | cut -d' ' -f1

# Windows PowerShell
(Get-FileHash plugin.py -Algorithm SHA256).Hash.ToLower()

# 跨平台
python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('plugin.py').read_bytes()).hexdigest())"
```

必须是 64 位小写十六进制。模块改动一个字节就要重算，否则加载被拒。

### 4. 编写 artpm-plugin.json

文件名必须**正好**是 `artpm-plugin.json`：

```json
{
  "schema_version": 1,
  "plugin_id": "studio.asset-review",
  "version": "1.0.0",
  "module": "plugin.py",
  "module_sha256": "<上一步得到的 64 位小写十六进制>",
  "skills": [
    {
      "name": "asset_review",
      "class": "AssetReviewSkill",
      "description": "审阅资产包并给出问题清单",
      "version": "1.0.0",
      "risk": "low",
      "read_only": true,
      "requires_approval": false,
      "capabilities": ["asset.analyze"]
    }
  ]
}
```

---

## manifest 契约

### 顶层字段

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `schema_version` | ✅ | 必须是整数 `1` |
| `plugin_id` | ✅ | `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$` |
| `version` | ✅ | `^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$` |
| `module` | ✅ | 插件目录内的相对 `.py` 路径，不允许 `..` 或绝对路径 |
| `module_sha256` | 视策略 | 64 位小写十六进制。`require_hash=True`（部署入口固定开启）时必填 |
| `skills` | ✅ | 非空数组，最多 64 项，`name` 需唯一 |

manifest 本身上限 128 KiB，模块文件上限 4 MiB。

### skills[] 字段

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `name` | ✅ | 同 `plugin_id` 的标识符规则，须等于类的 `skill_name` |
| `class` | ✅ | `^[A-Za-z_][A-Za-z0-9_]{0,127}$`，模块内的类名 |
| `description` | ✅ | 非空字符串 |
| `version` | ✅ | 版本号规则 |
| `risk` | ✅ | `low` / `medium` / `high` / `critical` / `untrusted` |
| `read_only` | ✅ | 布尔值 |
| `requires_approval` | ✅ | 布尔值 |
| `required_role` | ❌ | `user`（默认）或 `admin` |
| `capabilities` | ❌ | 标识符数组，最多 32 项且需唯一 |

**关键约束**：`read_only: false` 的技能必须 `requires_approval: true`。
声明为可写却不要求审批的 manifest 直接被拒 —— 插件不能给自己开后门
绕过审批闸门。

---

## 技能实现

### 可用的基类成员

```python
class BaseSkill:
    skill_name: str = ""
    description: str = ""
    version: str = "1.0"
    author: str = ""
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}
    dependencies: List[str] = []
    requires_llm: bool = False

    def __init__(self, context: Optional[Dict[str, Any]] = None): ...

    @abstractmethod
    def execute(self, inputs) -> Dict[str, Any]: ...   # 必须实现

    def validate(self, inputs) -> Tuple[bool, str]: ...  # 可覆盖
    def pre_execute(self, inputs) -> Dict[str, Any]: ... # 可覆盖
    def post_execute(self, result) -> Dict[str, Any]: ...# 可覆盖
    def run(self, inputs) -> Dict[str, Any]: ...         # 不要覆盖
```

构造时注入的 `context` 提供 `llm_client`、`memory`、`config`：

```python
def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
    if self.llm is None:
        return {"success": False, "error": "此技能需要 LLM，当前离线"}
    ...
```

需要 LLM 的技能请设 `requires_llm = True`，并且在 `self.llm is None`
时优雅降级 —— 项目的核心功能在无 API Key 时必须可用。

### 返回值约定

`execute` 必须返回 `dict`，否则基类会报错。约定形状：

```python
{"success": True, "data": {...}}
{"success": False, "error": "人类可读的原因"}
```

`run()` 会自动补 `skill` 与 `execution_time` 字段。

### 元数据如何暴露

manifest 里的 `risk`、`read_only`、`requires_approval`、`required_role`、
`capabilities` 会连同 `plugin_id`、`plugin_version`、
`is_plugin_skill: True` 一起进入 `SkillRouter.get_skill_metadata()`。

工作流宿主必须把这些 capability 与**自己的服务端 allowlist 和风险策略求交集**。
manifest 无法给自己授予工作流执行权限。

---

## 启用与加载

### 部署环境变量

只读进程环境，不读任何请求参数或应用设置：

```dotenv
ARTPM_PLUGINS_ENABLED=true
ARTPM_PLUGIN_ROOTS=["/opt/artpm-plugins"]
ARTPM_PLUGIN_ALLOWLIST=["studio.asset-review"]
```

两个列表都是 **JSON 数组字符串**，不能为空、不能有重复项。Windows 路径
需要转义反斜杠：

```dotenv
ARTPM_PLUGIN_ROOTS=["D:\\ArtPM\\plugins"]
```

### 代码接入

```python
from artpm_agent.plugins import build_plugin_manager_from_environment
from artpm_agent.skills.skill_router import SkillRouter

# 从进程环境构建 fail-closed 管理器；哈希校验与符号链接拒绝固定开启
plugin_manager = build_plugin_manager_from_environment()

router = SkillRouter({"plugin_manager": plugin_manager, "config": config})
print("已加载插件:", router.plugin_load_report.loaded_plugin_ids)
print("插件错误:", router.plugin_errors)
```

测试或自定义宿主可以直接构造策略：

```python
from pathlib import Path
from artpm_agent.plugins import PluginManager, PluginPolicy

manager = PluginManager(
    PluginPolicy(
        enabled=True,
        trusted_roots=(Path("/opt/artpm-plugins"),),
        allowed_plugin_ids=frozenset({"studio.asset-review"}),
        require_hash=True,
        reject_symlinks=True,
        max_plugins=64,
    )
)
report = manager.load_skills(reserved_names={"existing_skill"})
```

`SkillRouter` 只接受真正的 `PluginManager` 实例，传别的对象会记录
`plugin_errors` 并跳过加载，而不是照常执行。

---

## 失败诊断

`PluginLoadReport` 的结构：

```python
report.registrations       # 成功注册的技能
report.loaded_plugin_ids   # 成功加载的插件 ID
report.skipped_plugin_ids  # 发现了但不在 allowlist 里
report.failures            # PluginFailure(plugin_id, path, stage, error)
```

`stage` 指出失败发生在哪一环：

| stage | 含义 | 常见原因 |
| --- | --- | --- |
| `policy` | 策略拒绝 | 符号链接、插件 ID 重复、技能名冲突、超出数量上限 |
| `discovery` | 根目录不可用 | 路径不存在或无权限 |
| `manifest` | manifest 非法 | schema 字段缺失、摘要不匹配、模块越界 |
| `import` | 模块执行抛错 | 插件顶层代码异常、缺依赖 |
| `class` | 类契约不符 | 类不存在、不是 BaseSkill 子类、`skill_name` 不匹配 |

### 常见错误

**`manifest must be named artpm-plugin.json`**
文件名写成了 `manifest.json` 或 `plugin.json`。

**`schema_version must be 1`**
缺 `schema_version`，或写成了字符串 `"1"`。

**`plugin module SHA-256 does not match the manifest`**
模块改过但没重算摘要。注意换行符：CRLF 与 LF 的字节不同，摘要也不同。
这个校验发生在读取模块时（`manifest` 阶段之后），不在 manifest 解析期。

**`module_sha256 is required by plugin policy`**
`require_hash=True`（部署入口固定值）时缺 `module_sha256`。

**插件出现在 `skipped_plugin_ids` 里**
`plugin_id` 不在 `ARTPM_PLUGIN_ALLOWLIST` 中。这是静默跳过，不是错误。

**`skills[0] is write-capable and must require approval`**
`read_only: false` 却把 `requires_approval` 写成了 `false`。

**`class skill_name does not match manifest`**
类属性 `skill_name` 与 manifest 的 `skills[].name` 不一致。

---

## 最佳实践

### 1. 单文件模块

摘要针对单个 `module` 文件。把实现放在一个 `.py` 里最省事；确实需要拆分时，
入口模块内的 `import` 由 Python 正常解析，但**只有入口文件的字节参与校验**，
其余文件不在完整性保护范围内。

### 2. 顶层代码保持无副作用

模块在 `exec` 时立即执行。顶层不要发网络请求、不要连数据库、不要读大文件 ——
顶层抛错会让整个插件被隔离。

### 3. 保守声明风险

不确定就往高报。`read_only` 只在真的不产生任何副作用时才设 `true`。
声明宽松不会让技能跑得更顺，只会让审批闸门失去意义。

### 4. 版本号跟着模块走

`version` 参与模块的内部命名，改实现就升版本，避免同一进程内命中旧模块缓存。

### 5. 不要依赖 `sys.modules` 里的插件名

模块以 `_artpm_plugin_<hash>` 注册，名字由 ID、版本和摘要派生，不是稳定契约。

### 6. 别把凭据写进插件

插件文件的摘要要出现在 manifest 里、路径要出现在部署配置里。凭据走环境变量，
由宿主通过 `context` 注入。

---

**文档版本**: 2.0
**更新日期**: 2026-08-02
**维护者**: ArtPM Team
