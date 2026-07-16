# ArtPM Agent - 快速启动指南

## 📦 1. 环境准备

### 系统要求
- Windows 10/11 或 macOS 或 Linux
- Python 3.8+
- 4GB+ 内存
- 1GB+ 磁盘空间

### 检查Python版本
```bash
python --version
# 应该显示: Python 3.8.x 或更高
```

如果没有Python,请访问: https://www.python.org/downloads/

---

## 🚀 2. 快速安装

### 方法1: 一键启动(推荐)

```bash
# 双击运行
start.bat
```

`start.bat` 会自动:
1. 检查依赖
2. 安装缺失的包
3. 启动应用
4. 打开浏览器

### 方法2: 手动安装

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 健康检查
cd artpm_agent
python health_check.py

# 3. 启动应用
cd ..
streamlit run artpm_agent/app.py
```

---

## ⚙️ 3. 配置API密钥

### 为什么需要API密钥?
ArtPM Agent使用大语言模型(LLM)提供智能对话功能。没有API密钥也能使用基础功能,但无法使用智能对话。

### 获取API密钥

选择一个提供商:

#### OpenAI (推荐)
1. 访问: https://platform.openai.com/api-keys
2. 注册/登录账号
3. 创建API Key
4. 复制密钥(sk-开头)

#### Anthropic Claude
1. 访问: https://console.anthropic.com/
2. 注册/登录账号
3. 创建API Key
4. 复制密钥(sk-ant-开头)

#### DeepSeek (便宜)
1. 访问: https://platform.deepseek.com/
2. 注册/登录账号
3. 创建API Key
4. 复制密钥(sk-开头)

### 配置方法

#### 方法1: 通过UI配置(推荐)

1. 启动应用: `start.bat`
2. 点击左侧"⚙️ 设置"
3. 选择LLM提供商
4. 粘贴API Key
5. 点击"💾 保存配置"
6. 重启应用

#### 方法2: 手动编辑.env文件

1. 打开项目根目录的`.env`文件
2. 添加API Key:

```env
# OpenAI
OPENAI_API_KEY=sk-your-openai-key-here

# Anthropic (或使用这个)
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here

# DeepSeek (或使用这个,最便宜)
DEEPSEEK_API_KEY=sk-your-deepseek-key-here

# 选择提供商
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
```

3. 保存文件
4. 重启应用

---

## ✅ 4. 验证安装

### 运行健康检查

```bash
cd artpm_agent
python health_check.py
```

**成功输出示例:**
```
============================================================
ArtPM Agent - 系统健康检查
============================================================

📦 模块导入:
  ✓ streamlit           v1.28.0
  ✓ pandas              v2.0.0
  ✓ openpyxl            vN/A
  ✓ sqlalchemy          v2.0.0

🤖 LLM提供商:
  ✓ openai              v1.0.0
  ✓ anthropic           v0.8.0

💾 数据库:
  ✓ 连接状态: success
  ✓ 项目数量: 0

⚙️ 配置:
  LLM Provider: anthropic
  LLM Model: claude-3-5-sonnet-20241022
  API Keys:
    ✓ anthropic: configured

🤖 Agent:
  ✓ 初始化成功
  ✓ Skills数量: 10
  ✓ LLM可用: True

============================================================
✓ 系统健康 - 所有组件正常工作
============================================================
```

### 测试功能

启动应用后:

1. **测试对话功能**
   ```
   输入: "你好"
   期望: Agent返回欢迎消息
   ```

2. **测试利润计算**
   ```
   输入: "报价30万成本20万帮我算利润"
   期望: 显示详细利润分析表格
   ```

3. **测试概览页面**
   - 点击左侧"📊 概览"
   - 查看项目统计数据

---

## 💡 5. 开始使用

### 基础操作

#### 1. 对话助手
- 点击"💬 对话"
- 输入问题,比如:
  - "这个项目的利润率怎么样?"
  - "帮我分配任务"
  - "检查项目进度"

#### 2. 查看项目
- 点击"📁 项目"
- 查看所有项目列表
- 点击项目查看详情

#### 3. 上传文档
- 点击"📤 上传"
- 上传Excel报价单
- 点击"🔍 解析文档"

#### 4. 查看统计
- 点击"📊 概览"
- 查看项目统计和最近项目

### 高级功能

#### 利润计算
```
输入: "报价金额300000成本200000管理费15%税率6%"
```

#### 任务分配
```
输入: "分配建模任务给团队"
```

#### 进度跟踪
```
输入: "检查项目进度"
```

#### 催办提醒
```
输入: "帮我催一下进度"
```

---

## 🔧 6. 故障排除

### 问题1: 应用无法启动

**症状:** 双击start.bat后闪退

**解决:**
```bash
# 1. 手动启动查看错误
python artpm_agent/app.py

# 2. 查看日志
cat artpm_agent/logs/artpm_*.log

# 3. 重新安装依赖
pip install --force-reinstall -r requirements.txt
```

### 问题2: LLM不可用

**症状:** 对话功能提示"LLM未配置"

**解决:**
1. 检查`.env`文件中的API Key
2. 确认API Key正确(无空格)
3. 在UI的"设置"页面重新配置

### 问题3: 数据库错误

**症状:** 项目页面报错

**解决:**
```bash
# 删除旧数据库
rm data/artpm.db

# 重启应用
start.bat
```

### 问题4: 依赖缺失

**症状:** ImportError: No module named 'xxx'

**解决:**
```bash
# 安装特定模块
pip install xxx

# 或重新安装所有依赖
pip install -r requirements.txt
```

---

## 📝 7. 查看日志

### 日志位置
```
artpm_agent/logs/artpm_20260711.log
```

### 实时查看
```bash
# Linux/Mac
tail -f artpm_agent/logs/artpm_*.log

# Windows PowerShell
Get-Content artpm_agent/logs/artpm_*.log -Wait

# Windows Git Bash
tail -f artpm_agent/logs/artpm_*.log
```

### 搜索错误
```bash
grep "ERROR" artpm_agent/logs/artpm_*.log
```

---

## 🎓 8. 学习资源

### 文档

| 文档 | 说明 |
|------|------|
| README.md | 项目概览 |
| OPTIMIZATION_SUMMARY.md | 优化总结 |
| OPTIMIZATION_PLAN.md | 优化计划 |
| PROJECT_STRUCTURE.md | 项目结构 |
| MCP_SKILLS_QUICKSTART.md | MCP Skills指南 |

### 日志分析

查看Agent执行日志:
```bash
grep "skills" artpm_agent/logs/artpm_*.log
```

查看LLM调用日志:
```bash
grep "LLM" artpm_agent/logs/artpm_*.log
```

---

## 📞 9. 获取帮助

### 运行诊断

```bash
cd artpm_agent
python health_check.py > diagnosis.txt
```

将 `diagnosis.txt` 和 `logs/artpm_*.log` 一起发送以获取支持。

### 常见命令

```bash
# 健康检查
cd artpm_agent && python health_check.py

# 查看日志
cat artpm_agent/logs/artpm_*.log | tail -50

# 重启应用
start.bat

# 清理缓存
rm -rf __pycache__
rm -rf artpm_agent/__pycache__
rm -rf artpm_agent/*/__pycache__
```

---

## 🎉 10. 下一步

现在你已经成功安装并配置了ArtPM Agent!

### 推荐操作

1. **创建测试项目**
   - 点击"📁 项目"
   - 创建一个测试项目
   - 上传报价单

2. **测试利润计算**
   - 输入: "报价30万成本20万"
   - 查看利润分析

3. **浏览功能**
   - 尝试所有菜单项
   - 测试对话功能
   - 上传文档

### 进阶学习

- 阅读 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md) 了解系统架构
- 阅读 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) 了解代码结构
- 阅读 [技术架构重构方案.md](技术架构重构方案.md) 了解技术细节

---

**祝你使用愉快! 🎉**

如有问题,请查看日志文件或运行健康检查获取诊断信息。
