# PanUpLoad — 多网盘备份工具

本项目由 AI 辅助编程完成。
一键将本地文件/文件夹同时备份到多个云盘。

## 支持的网盘

| 网盘 | 认证方式 | 状态 |
|------|----------|------|
| 百度网盘 | Cookie (BDUSS) / OAuth | 可用 |
| 夸克云盘 | Cookie (Web 登录) | 可用 |

## 安装

### 方式一：直接运行 exe（Windows）

下载 `dist/PanUpLoad.exe`，双击运行。数据存储在 `%LOCALAPPDATA%\PanUpLoad\`。

### 方式二：从源码运行

```bash
# 克隆项目
cd PanUpLoad

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行
python main.py
```

## 使用指南

### 1. 添加网盘账号

1. 切换到「账号管理」标签页
2. 在「设置」标签页中配置百度网盘 API 凭据（如使用百度网盘）
3. 选择网盘类型，点击「获取授权链接」
4. 在浏览器中完成授权，复制授权码
5. 粘贴授权码，点击「添加账号」

**各网盘 Token 获取方式：**
- **百度网盘**：在账号管理页面选择百度网盘，点击登录后在浏览器中扫码登录，Token 自动提取
- **夸克云盘**：在账号管理页面选择夸克云盘，点击登录后在浏览器中扫码登录，Token 自动提取

### 2. 执行备份

1. 切换到「备份任务」标签页
2. 点击「选择文件」或「选择文件夹」选取要备份的内容
3. 勾选目标网盘（可多选），设置每个网盘的目标文件夹
4. 点击「开始备份」
5. 在进度区查看实时上传状态

## 安全说明

- 网盘 Token 使用 AES-256-GCM 加密后存储在本地 SQLite 数据库
- 加密密钥通过 Windows DPAPI（CryptProtectData）绑定当前 Windows 用户
- 所有数据仅在本地存储，不上传到任何第三方服务器

## 开发

```bash
# 安装开发依赖
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-httpx

# 运行测试
python -m pytest tests/ -v

# 打包为 exe
python -m PyInstaller panupdate.spec
```



## 技术栈

- Python 3.11+
- Flet (桌面 GUI)
- httpx (异步 HTTP)
- cryptography + Windows DPAPI (加密)
- SQLite (本地存储)
