# 🔧 开发模式安装修复

**时间**：2026-07-14  
**状态**：✅ 完全修复

---

## 问题分析

### 根本原因

虽然运行了 `pip install -e .`，但出现了以下问题：

1. **安装位置错误**
   - 第一次安装：非开发模式，代码复制到 `E:\python\Lib\site-packages`
   - 结果：代码修改不生效，导入路径错误

2. **多个进程冲突**
   - 两个 Streamlit 进程同时运行 (PID 27324, 6128)
   - 进程使用旧的安装版本

3. **dead_pages.py 问题**
   - 这是废弃代码，不被主程序使用
   - 但 Streamlit 会扫描所有 Python 文件
   - 导入错误影响应用启动

---

## 修复步骤

### 1. 卸载旧安装
```bash
pip uninstall artpm-agent -y
```

### 2. 停止所有进程
```bash
taskkill /F /PID 27324
taskkill /F /PID 6128
```

### 3. 重新安装（开发模式）
```bash
cd "d:\桌面\xiangmu\pmagent"
pip install -e .
```

### 4. 验证安装
```bash
pip show artpm-agent -v
```

**正确的输出**：
```
Name: artpm-agent
Version: 0.1.0
Location: E:\python\Lib\site-packages
Editable project location: D:\桌面\xiangmu\pmagent  ← 关键
```

---

## 开发模式说明

### 什么是开发模式？

使用 `-e` (editable) 标志安装：
```bash
pip install -e .
```

### 工作原理

1. 在 `site-packages` 创建 `.pth` 文件
2. 指向项目源代码目录
3. Python 直接读取源代码
4. **代码修改立即生效**

### 验证开发模式

检查 `pip show` 输出中是否有：
```
Editable project location: /path/to/project
```

---

## dead_pages.py 说明

### 什么是 dead_pages.py？

```python
"""从未被 main() 路由的死页面（从 app.py 拆分暂存，便于恢复）。"""
```

- **作用**：存放废弃的页面代码
- **状态**：不被 `app.py` 使用
- **问题**：Streamlit 会扫描并尝试导入

### 为什么会报错？

1. Streamlit 扫描 `pages/` 目录
2. 尝试导入 `dead_pages.py`
3. 文件使用绝对导入 `from artpm_agent...`
4. 如果项目未正确安装，导入失败

### 解决方案

**选项1：保持现状**（推荐）
- 正确安装开发模式后，不会报错
- 代码可以保留用于参考

**选项2：重命名文件**
```bash
mv artpm_agent/pages/dead_pages.py artpm_agent/pages/_dead_pages.py.bak
```

**选项3：删除文件**
```bash
rm artpm_agent/pages/dead_pages.py
```

---

## 启动应用

### 方式1：命令行
```bash
cd "d:\桌面\xiangmu\pmagent"
streamlit run artpm_agent/app.py
```

### 方式2：启动脚本
```bash
# Windows
start.bat

# Linux/Mac
./start.sh
```

---

## 验证清单

✅ **开发模式已正确安装**
```bash
pip show artpm-agent -v | grep "Editable project location"
```

✅ **导入测试通过**
```bash
python -c "from artpm_agent.utils.logger import setup_logging"
```

✅ **应用正常启动**
```bash
streamlit run artpm_agent/app.py
```

✅ **访问测试**
```bash
curl http://localhost:8501
```

---

## 常见问题

### Q: 为什么第一次安装失败？

A: 可能使用了 `pip install -e . --no-deps`，跳过了某些设置步骤。正确命令是：
```bash
pip install -e .
```

### Q: 如何确认是开发模式？

A: 运行并检查输出：
```bash
pip show artpm-agent -v
```
应该看到 `Editable project location: ...`

### Q: 代码修改后需要重启吗？

A: 
- Python 代码：需要重启 Streamlit
- 静态资源：刷新浏览器即可

### Q: 如何重启 Streamlit？

A:
1. 按 `Ctrl+C` 停止
2. 重新运行 `streamlit run artpm_agent/app.py`
3. 或在浏览器中按 `R` 键快速重启

---

## 访问应用

🌐 **http://localhost:8501**

---

**问题已完全解决！开发模式已正确配置。** ✅
