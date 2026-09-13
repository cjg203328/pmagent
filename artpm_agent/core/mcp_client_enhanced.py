"""
Enhanced MCP Client - 连接Claude Code能力
利用Claude Code的工具能力(Read, Write, Glob, Grep, Bash, Agent)
"""
from copy import deepcopy
import logging
import os
import shlex
from typing import Dict, Any, List
from pathlib import Path


logger = logging.getLogger(__name__)
MAX_TOOL_FILE_BYTES = 10 * 1024 * 1024
MAX_TOOL_OUTPUT_CHARS = 32 * 1024
_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "secrets.json",
        "service-account.json",
        "service_account.json",
    }
)
_SENSITIVE_FILE_SUFFIXES = frozenset(
    {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx"}
)
_SENSITIVE_DIRECTORIES = frozenset({".git", ".ssh", ".aws", ".azure", ".gnupg"})

# Executable basename allowlist for the execute_command tool. It must be
# explicitly non-empty when command execution is enabled. This is defense in
# depth on top of the MCP_ALLOW_COMMANDS master switch.
def _parse_command_allowlist(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    items: List[str] = []
    for item in value.split(","):
        item = item.strip()
        if item:
            items.append(item)
    return frozenset(items)


_JSON_DATA_SOURCE_SCHEMA = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        {"type": "array"},
        {"type": "object"},
    ]
}

_ENHANCED_MCP_INPUT_SCHEMAS = {
    "read_file": {
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string", "minLength": 1},
            "encoding": {"type": "string", "minLength": 1},
            "lines_limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "additionalProperties": False,
    },
    "search_files": {
        "type": "object",
        "required": ["pattern"],
        "properties": {
            "pattern": {"type": "string", "minLength": 1},
            "directory": {"type": "string"},
            "recursive": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
    "search_content": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "file_pattern": {"type": "string", "minLength": 1},
            "directory": {"type": "string"},
            "case_sensitive": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
    "analyze_data": {
        "type": "object",
        "required": ["data_source"],
        "properties": {
            "data_source": _JSON_DATA_SOURCE_SCHEMA,
            "analysis_type": {
                "type": "string",
                "enum": ["descriptive", "summary", "statistics"],
            },
            "metrics": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    },
    "execute_command": {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                ]
            },
            "cwd": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
        },
        "additionalProperties": False,
    },
}


class EnhancedMCPClient:
    """增强型MCP客户端 - 连接Claude Code能力"""

    def __init__(self, workspace_path: str = None, allow_commands: bool = None):
        """
        初始化增强型MCP客户端

        Args:
            workspace_path: 工作空间路径
        """
        default_workspace = Path(__file__).resolve().parents[2]
        self.workspace_path = Path(workspace_path or default_workspace).resolve()
        if not self.workspace_path.is_dir():
            raise ValueError(f"Workspace does not exist: {self.workspace_path}")
        if allow_commands is None:
            allow_commands = os.getenv("MCP_ALLOW_COMMANDS", "false").lower() == "true"
        self.allow_commands = allow_commands
        self.command_allowlist = _parse_command_allowlist(
            os.getenv("MCP_COMMAND_ALLOWLIST", "")
        )
        self.enabled = True

        # 注册可用工具
        self.available_tools = {
            "read_file": {
                "name": "read_file",
                "description": "读取文件内容(支持 TXT, MD, JSON, CSV, Excel等)",
                "execute": self._read_file,
                "input_schema": _ENHANCED_MCP_INPUT_SCHEMAS["read_file"],
            },
            "search_files": {
                "name": "search_files",
                "description": "搜索文件(支持 glob pattern)",
                "execute": self._search_files,
                "input_schema": _ENHANCED_MCP_INPUT_SCHEMAS["search_files"],
            },
            "search_content": {
                "name": "search_content",
                "description": "搜索文件内容(支持正则表达式)",
                "execute": self._search_content,
                "input_schema": _ENHANCED_MCP_INPUT_SCHEMAS["search_content"],
            },
            "analyze_data": {
                "name": "analyze_data",
                "description": "分析结构化数据(Excel, CSV, JSON)",
                "execute": self._analyze_data,
                "input_schema": _ENHANCED_MCP_INPUT_SCHEMAS["analyze_data"],
            },
            "execute_command": {
                "name": "execute_command",
                "description": "执行系统命令",
                "execute": self._execute_command,
                "input_schema": _ENHANCED_MCP_INPUT_SCHEMAS["execute_command"],
            }
        }

        logger.debug("Initialized with %d local tools", len(self.available_tools))

    def _resolve_path(self, value: str = None) -> Path:
        """Resolve a user path and keep it inside the configured workspace."""
        path = Path(value) if value else self.workspace_path
        if not path.is_absolute():
            path = self.workspace_path / path
        path = path.resolve()
        try:
            common = Path(os.path.commonpath([self.workspace_path, path]))
        except ValueError as exc:
            raise ValueError("Path is outside the workspace") from exc
        if common != self.workspace_path:
            raise ValueError("Path is outside the workspace")
        return path

    def _is_sensitive_path(self, path: Path) -> bool:
        """Return whether a workspace path may expose deployment credentials."""
        try:
            relative = path.resolve().relative_to(self.workspace_path)
        except ValueError:
            return True
        parts = {part.casefold() for part in relative.parts[:-1]}
        name = relative.name.casefold()
        return (
            bool(parts & _SENSITIVE_DIRECTORIES)
            or name in _SENSITIVE_FILE_NAMES
            or name.startswith(".env.")
            or name.startswith("credentials.")
            or name.startswith("secrets.")
            or relative.suffix.casefold() in _SENSITIVE_FILE_SUFFIXES
        )

    def _resolve_readable_path(self, value: str = None) -> Path:
        path = self._resolve_path(value)
        if self._is_sensitive_path(path):
            raise PermissionError("Sensitive workspace files are not readable by tools")
        return path

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(value, maximum))

    # ═══════════════════════════════════════════════════════════
    # 文件操作
    # ═══════════════════════════════════════════════════════════

    async def _read_file(self, file_path: str, **kwargs) -> Dict[str, Any]:
        """
        读取文件内容

        Args:
            file_path: 文件路径(相对或绝对)
            encoding: 文件编码(默认utf-8)
            lines_limit: 行数限制(默认全部)

        Returns:
            {
                "success": bool,
                "content": str,
                "metadata": {
                    "file_name": str,
                    "file_size": int,
                    "file_ext": str,
                    "lines": int
                }
            }
        """
        try:
            # 解析路径
            path = self._resolve_readable_path(file_path)

            if not path.is_file():
                return {
                    "success": False,
                    "error": f"File not found: {file_path}"
                }
            if path.stat().st_size > MAX_TOOL_FILE_BYTES:
                return {"success": False, "error": "File is larger than 10 MB"}

            # 读取文件
            encoding = kwargs.get("encoding", "utf-8")
            lines_limit = kwargs.get("lines_limit")
            if lines_limit is not None:
                lines_limit = self._bounded_int(lines_limit, 1000, 1, 1000)

            suffix = path.suffix.lower()
            if suffix in {".xlsx", ".xls"}:
                import pandas as pd
                content = pd.read_excel(path).to_csv(index=False)
            elif suffix == ".pdf":
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    content = "\n".join(page.extract_text() or "" for page in pdf.pages)
            else:
                with open(path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read(MAX_TOOL_OUTPUT_CHARS + 1)

            if lines_limit is not None:
                content = "\n".join(content.splitlines()[:lines_limit])
            truncated = len(content) > MAX_TOOL_OUTPUT_CHARS
            content = content[:MAX_TOOL_OUTPUT_CHARS]

            # 统计信息
            line_count = len(content.splitlines())

            return {
                "success": True,
                "content": content,
                "metadata": {
                    "file_name": path.name,
                    "file_path": str(path),
                    "file_size": path.stat().st_size,
                    "file_ext": path.suffix,
                    "lines": line_count,
                    "encoding": encoding,
                    "truncated": truncated,
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _search_files(self, pattern: str, directory: str = None, **kwargs) -> Dict[str, Any]:
        """
        搜索文件

        Args:
            pattern: Glob pattern (例如: *.xlsx, **/*.py)
            directory: 搜索目录(默认workspace)
            recursive: 是否递归搜索(默认True)
            limit: 结果数量限制(默认100)

        Returns:
            {
                "success": bool,
                "files": List[str],
                "count": int
            }
        """
        try:
            search_dir = self._resolve_path(directory)
            recursive = kwargs.get("recursive", True)
            limit = self._bounded_int(kwargs.get("limit"), 100, 1, 100)

            if not search_dir.is_dir():
                return {"success": False, "error": f"Directory not found: {directory}"}

            if recursive and not pattern.startswith("**"):
                pattern = f"**/{pattern}"

            # 搜索文件
            files = sorted(search_dir.glob(pattern), key=lambda item: str(item).lower())

            # 只保留文件(排除目录)
            safe_files = []
            for file_path in files:
                try:
                    resolved = self._resolve_path(str(file_path))
                    if resolved.is_file() and not self._is_sensitive_path(resolved):
                        safe_files.append(file_path)
                except ValueError:
                    continue
            files = safe_files

            # 限制数量
            files = files[:limit]

            # 转换为相对路径字符串
            file_paths = [str(f.relative_to(self.workspace_path)) for f in files]

            return {
                "success": True,
                "files": file_paths,
                "count": len(file_paths),
                "pattern": pattern,
                "search_dir": str(search_dir)
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _search_content(self, query: str, file_pattern: str = "*", **kwargs) -> Dict[str, Any]:
        """
        搜索文件内容

        Args:
            query: 搜索关键词或正则表达式
            file_pattern: 文件pattern (默认所有文件)
            directory: 搜索目录
            case_sensitive: 是否大小写敏感(默认False)
            limit: 结果数量限制(默认50)

        Returns:
            {
                "success": bool,
                "matches": List[{
                    "file": str,
                    "line": int,
                    "content": str
                }],
                "count": int
            }
        """
        try:
            import re

            search_dir = self._resolve_path(kwargs.get("directory"))
            case_sensitive = kwargs.get("case_sensitive", False)
            limit = self._bounded_int(kwargs.get("limit"), 50, 1, 100)

            # 编译正则表达式
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(query, flags)

            # 搜索文件
            files_result = await self._search_files(file_pattern, str(search_dir), recursive=True)

            if not files_result["success"]:
                return files_result

            # 搜索内容
            matches = []
            for file_path in files_result["files"]:
                if len(matches) >= limit:
                    break

                try:
                    full_path = self._resolve_readable_path(file_path)
                    if full_path.stat().st_size > 10 * 1024 * 1024:
                        continue
                    emitted_chars = sum(
                        len(item["content"]) for item in matches
                    )
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if pattern.search(line):
                                snippet = line.strip()[:2000]
                                if emitted_chars + len(snippet) > MAX_TOOL_OUTPUT_CHARS:
                                    break
                                matches.append({
                                    "file": file_path,
                                    "line": line_num,
                                    "content": snippet,
                                })
                                emitted_chars += len(snippet)

                                if len(matches) >= limit:
                                    break
                except (OSError, UnicodeError):
                    continue

            return {
                "success": True,
                "matches": matches,
                "count": len(matches),
                "query": query
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    # ═══════════════════════════════════════════════════════════
    # 数据分析
    # ═══════════════════════════════════════════════════════════

    async def _analyze_data(self, data_source: Any, analysis_type: str = "descriptive", **kwargs) -> Dict[str, Any]:
        """
        分析数据

        Args:
            data_source: 数据源(文件路径或数据对象)
            analysis_type: 分析类型(descriptive/summary/statistics)
            metrics: 要分析的指标列表

        Returns:
            {
                "success": bool,
                "analysis": Dict,
                "insights": List[str]
            }
        """
        try:
            import pandas as pd

            # 加载数据
            if isinstance(data_source, str):
                # 从文件加载
                path = self._resolve_readable_path(data_source)

                if not path.is_file():
                    return {"success": False, "error": f"File not found: {data_source}"}
                if path.stat().st_size > MAX_TOOL_FILE_BYTES:
                    return {"success": False, "error": "File is larger than 10 MB"}

                if path.suffix in ['.xlsx', '.xls']:
                    df = pd.read_excel(path)
                elif path.suffix == '.csv':
                    df = pd.read_csv(path)
                elif path.suffix == '.json':
                    df = pd.read_json(path)
                else:
                    return {"success": False, "error": "Unsupported file format"}

            elif isinstance(data_source, (list, dict)):
                df = pd.DataFrame(data_source)
            else:
                df = data_source

            # 执行分析
            analysis = {
                "rows": len(df),
                "columns": len(df.columns),
                "column_names": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()}
            }

            if analysis_type in ["descriptive", "statistics"]:
                # 描述性统计
                numeric_cols = df.select_dtypes(include=['number']).columns
                if len(numeric_cols) > 0:
                    desc = df[numeric_cols].describe()
                    analysis["statistics"] = desc.to_dict()

            # 生成洞察
            insights = []
            if len(df) > 0:
                insights.append(f"数据集包含 {len(df)} 行, {len(df.columns)} 列")

                # 检查缺失值
                missing = df.isnull().sum()
                if missing.sum() > 0:
                    insights.append(f"发现 {missing.sum()} 个缺失值")

                # 数值列统计
                numeric_cols = df.select_dtypes(include=['number']).columns
                if len(numeric_cols) > 0:
                    insights.append(f"包含 {len(numeric_cols)} 个数值列")

            return {
                "success": True,
                "analysis": analysis,
                "insights": insights,
                "preview": df.head(5).to_dict('records') if len(df) > 0 else []
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    # ═══════════════════════════════════════════════════════════
    # 命令执行
    # ═══════════════════════════════════════════════════════════

    async def _execute_command(self, command: str, **kwargs) -> Dict[str, Any]:
        """
        执行系统命令

        Args:
            command: 命令字符串
            cwd: 工作目录
            timeout: 超时时间(秒)

        Returns:
            {
                "success": bool,
                "stdout": str,
                "stderr": str,
                "exit_code": int
            }
        """
        if not self.allow_commands:
            return {
                "success": False,
                "error": "Command execution is disabled. Set MCP_ALLOW_COMMANDS=true to enable it."
            }

        if not self.command_allowlist:
            return {
                "success": False,
                "error": (
                    "Command execution requires a non-empty "
                    "MCP_COMMAND_ALLOWLIST"
                ),
            }

        try:
            import subprocess

            cwd = self._resolve_path(kwargs.get("cwd"))
            timeout = self._bounded_int(kwargs.get("timeout"), 30, 1, 120)
            args = command if isinstance(command, list) else shlex.split(command, posix=os.name != "nt")
            if not args:
                return {"success": False, "error": "Command is empty"}

            # Executable basename allowlist (defense-in-depth). Only the
            # explicitly listed executables may run.
            executable = os.path.basename(args[0])
            if executable not in self.command_allowlist:
                return {
                    "success": False,
                    "error": (
                        f"Command '{executable}' is not permitted by "
                        "MCP_COMMAND_ALLOWLIST. Allowed: "
                        + ", ".join(sorted(self.command_allowlist))
                    ),
                }

            # 执行命令
            result = subprocess.run(
                args,
                shell=False,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "command": command
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Command timeout after {timeout}s"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    # ═══════════════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════════════

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用工具

        Args:
            tool_name: 工具名称
            params: 参数

        Returns:
            执行结果
        """
        if not self.enabled:
            return {"success": False, "error": "MCP is not enabled"}

        tool = self.available_tools.get(tool_name)
        if not tool:
            return {"success": False, "error": f"Tool '{tool_name}' not found"}

        try:
            logger.info("Calling local tool: %s", tool_name)
            result = await tool["execute"](**params)
            return result
        except Exception as e:
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}"
            }

    def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有可用工具"""
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": deepcopy(tool["input_schema"]),
            }
            for tool in self.available_tools.values()
        ]

    def list_skills(self) -> List[Dict[str, Any]]:
        """Compatibility alias for callers that expose tools as skills."""
        return self.list_tools()

    async def call_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Compatibility alias for the legacy MCP client interface."""
        return await self.call_tool(skill_name, params)

    def is_enabled(self) -> bool:
        """检查是否启用"""
        return self.enabled


# ═══════════════════════════════════════════════════════════
# 全局实例
# ═══════════════════════════════════════════════════════════

_enhanced_mcp_client = None


def get_enhanced_mcp_client(workspace_path: str = None) -> EnhancedMCPClient:
    """获取增强型MCP客户端实例"""
    global _enhanced_mcp_client
    requested = Path(workspace_path).resolve() if workspace_path else None
    if (
        _enhanced_mcp_client is None
        or (requested is not None and requested != _enhanced_mcp_client.workspace_path)
    ):
        _enhanced_mcp_client = EnhancedMCPClient(workspace_path)
    return _enhanced_mcp_client
