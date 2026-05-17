# PanUpdate — 浏览器自动登录 Implementation Plan (v2)

> **Goal:** 一键登录全部 5 家网盘。浏览器打开登录页 → 用户登录 → 系统自动提取 Token → 保存账号。全程无需手动复制粘贴。

**Architecture:**
- 百度网盘：本地 OAuth 回调服务器（已实现）— 浏览器中授权后自动捕获 code
- 其余 4 家：Selenium WebDriver 启动 Edge 浏览器 → 用户登录 → 自动执行 JS 提取 Cookie/localStorage 中的 Token

**New Dependencies:** `selenium`（已在 venv 中安装），`webdriver-manager`（已安装）

---

## File Structure

```
panupdate/
├── auth/
│   ├── __init__.py              # (exists)
│   ├── oauth_server.py          # (exists) — 百度 OAuth 回调
│   └── web_login.py             # NEW — Selenium 自动登录 + Token 提取
├── ui/
│   └── login_page.py            # MODIFY — 一键登录替代手动复制粘贴
tests/
└── test_web_login.py            # NEW
```

---

### Task 1: Selenium 自动登录模块 (web_login.py + test)

**Files:**
- Create: `panupdate/auth/web_login.py`
- Create: `tests/test_web_login.py`

**`ProviderLoginConfig`：**

```python
@dataclass
class ProviderLoginConfig:
    provider: str
    login_url: str
    success_url_keyword: str      # 登录成功后 URL 包含此关键词
    token_js: str                 # JS 表达式，返回 token 字符串
    token_name: str               # 用户可读的 token 名称
```

**配置表（4 家非百度网盘）：**

```python
SELENIUM_LOGIN_CONFIGS = {
    "aliyun": ProviderLoginConfig(
        provider="aliyun",
        login_url="https://www.aliyundrive.com/drive/",
        success_url_keyword="/drive/",
        token_js='JSON.parse(localStorage.getItem("token")||"{}").refresh_token||""',
        token_name="refresh_token",
    ),
    "tianyi": ProviderLoginConfig(
        provider="tianyi",
        login_url="https://cloud.189.cn/",
        success_url_keyword="/portal",
        token_js='(document.cookie.match(/COOKIE_LOGIN_USER=([^;]+)/)||[])[1]||""',
        token_name="COOKIE_LOGIN_USER",
    ),
    "pan123": ProviderLoginConfig(
        provider="pan123",
        login_url="https://www.123pan.com/",
        success_url_keyword="/dashboard",
        token_js='(function(){var t=localStorage.getItem("token");try{return JSON.parse(t).access_token||JSON.parse(t).token||t||""}catch(e){return t||""}})()',
        token_name="access_token",
    ),
    "kuaike": ProviderLoginConfig(
        provider="kuaike",
        login_url="https://pan.quark.cn/",
        success_url_keyword="/main",
        token_js='(document.cookie.match(/auth_token=([^;]+)/)||document.cookie.match(/QUARK_PARAM=([^;]+)/)||[])[1]||""',
        token_name="auth_token",
    ),
}
```

**`run_selenium_login(config)` — 核心登录函数：**

```python
def run_selenium_login(config: ProviderLoginConfig, timeout: float = 180.0) -> str | None
```

Flow:
1. 用 webdriver-manager 自动找到匹配的 Edge WebDriver
2. 创建 Edge 浏览器实例（`headless=False` — 用户可见）
3. 导航到 `config.login_url`
4. 轮询 `driver.current_url`（每秒一次），检测是否匹配 `success_url_keyword`
5. 检测到登录成功后等待 2 秒让页面加载完成
6. 执行 `driver.execute_script(config.token_js)` 提取 token
7. 检查 token 非空且长度 > 5
8. 关闭浏览器，返回 token
9. 超时或浏览器被用户关闭返回 None

**线程安全：** 这个函数在后台线程中运行（Flet 主线程通过 `page.run_task()` 调度）。Selenium WebDriver 可以安全地在子线程中运行。

**Tests (mock-based, no actual browser)：**
1. `test_config_data` — 验证所有 4 个 config 的字段完整且 URL 合法
2. `test_token_js_syntax` — 验证所有 token_js 是合法可执行的 JS
3. `test_success_detection` — 验证 URL 关键词匹配逻辑
4. `test_token_extraction_logic` — 模拟 token 验证（长度检查）

---

### Task 2: 修改 login_page.py — 一键登录

**File:** Modify `panupdate/ui/login_page.py`

**变更内容：**

1. 百度网盘（保持不变）：OAuth 回调服务器 → 浏览器授权 → 自动捕获 code → 自动保存
2. 其余 4 家（新增）：点击"开始登录" → 在后台线程启动 Selenium → 打开 Edge 浏览器 → 用户登录 → 自动提取 Token → 自动保存账号
3. 移除手动 `_auth_code_field` + "添加账号"按钮的二步流程（对非百度网盘）
4. 保留手动粘贴作为降级方案（显示为"高级：手动输入 Token"的折叠区域）

**新 flow：**

```
用户选择网盘 → 点击"开始登录"
  ├─ 百度：OAuth 回调 + 浏览器 → 自动完成
  └─ 其他：Selenium Edge 浏览器 → 用户登录 → 自动提取 Token → 完成
```

**UI 更新：**
- "开始登录"按钮 → 启动自动登录流程
- 登录时显示"正在等待登录..."状态 + 进度指示器
- 添加"高级：手动输入 Token"可展开区域（降级方案）

---

### Task 3: 更新构建配置

**Files:**
- Modify: `pyproject.toml` — 添加 selenium 依赖
- Modify: `requirements.txt` — 添加 selenium
- Modify: `panupdate.spec` — 添加 hidden-imports

---

## Verification

1. `pytest tests/test_web_login.py -v` — 4 tests pass
2. `pytest tests/ -v` — 所有已有测试仍然通过
3. 手动测试：启动应用 → 选择阿里云盘 → 点击登录 → Edge 打开 → 登录 → Token 自动提取 → 账号保存
4. exe 构建成功且启动正常
