# HermesAgent 配置指南

> 用途：在新电脑上把本仓库中的 Etsy 表演服数字员工完整配置到 HermesAgent，并启用图片 + Excel Listing 生成。
>
> 当前验证组合：Windows 10/11、Python 3.11、Hermes CLI 0.18.2、Codex CLI、模型 `gpt-5.4`。

## 1. GitHub 中已经包含什么

仓库包含：

- `employee/SOUL.md`：数字员工身份、职责和安全边界；
- `employee/skills/etsy-performance-listing/`：图片识别、事实合并、标题、标签、emoji 描述、类目和买家说明生成技能；
- `scripts/provision-employee.ps1`：创建独立 Hermes Profile；
- `scripts/verify-employee.ps1`：核对 Profile、技能文件和 manifest 哈希；
- `scripts/start.ps1`：验证员工后启动网站与后端；
- 网站前后端、数据库迁移、知识审批、原创保护和 Excel 作业代码；
- [网站与数字员工部署迁移指南](网站与数字员工部署迁移指南.md)。

仓库不会包含：

- HermesAgent 或 Codex CLI 的安装程序；
- OAuth、API Key、Cookie、浏览器身份或 `.env` 凭据；
- `%LOCALAPPDATA%` 下的本机凭据存储；
- 用户上传的真实工作簿、附件、训练图片或生成产物。

这些内容不能安全提交到 Git。新电脑需要安装程序并由本人重新登录。

## 2. 安装并检查依赖

需要安装：

- Git for Windows；
- Python 3.11；
- Node.js 24；
- pnpm 11；
- uv；
- Hermes CLI 0.18.2；
- Codex CLI。

检查命令：

```powershell
git --version
py -3.11 --version
node --version
pnpm --version
uv --version
hermes --version
codex --version
```

## 3. 下载仓库并安装项目依赖

推荐让 Codex 在克隆后的仓库根目录运行一键配置；脚本会执行本节及后续 Profile、登录和校验步骤：

```powershell
.\scripts\bootstrap-new-machine.ps1 -Start
```

系统级程序缺失时脚本会安全停止并列出缺失项，不会自行申请管理员权限。下面保留手工步骤用于排错。

```powershell
git clone https://github.com/jinjinli226-netizen/etsy-performance-employee.git
Set-Location .\etsy-performance-employee

Set-Location .\backend
uv sync --extra dev --frozen

Set-Location ..\frontend
pnpm install --frozen-lockfile

Set-Location ..
```

不要把 `backend/.venv`、`frontend/node_modules` 或旧电脑复制来的全局 Python/Node 环境当作迁移材料；在新电脑按锁文件重新安装。

## 4. 创建 Etsy 专用 Hermes Profile

Profile 固定名称为 `etsy-performance-us`。创建前确认新电脑不存在同名目录：

```powershell
$ProfilePath = Join-Path $env:LOCALAPPDATA 'hermes\profiles\etsy-performance-us'
Test-Path -LiteralPath $ProfilePath
```

如果结果是 `False`，执行：

```powershell
.\scripts\provision-employee.ps1 `
  -Provider openai-codex `
  -ModelId gpt-5.4 `
  -ReasoningEffort high
```

脚本会：

1. 创建独立 Profile；
2. 写入员工身份和 Listing 技能；
3. 记录员工资产 SHA-256；
4. 生成不含秘密的 provisioning manifest；
5. 自动运行首次只读校验。

脚本故意拒绝覆盖已有同名 Profile。如果 `Test-Path` 返回 `True`，先停止相关服务并完整备份该目录；不要直接把新文件覆盖进去。

## 5. 完成两套登录

网站的对话与训练流程使用 Hermes Profile；Excel 图片识别和 Listing 生成使用 Codex CLI。两边都必须登录。

### 5.1 Hermes 登录

```powershell
hermes -p etsy-performance-us auth add openai-codex --type oauth
hermes -p etsy-performance-us auth status openai-codex
```

状态应显示已登录。

### 5.2 Codex CLI 登录

```powershell
codex login
codex login status
```

登录必须在本人可见的终端完成。不要把授权码、访问令牌或 API Key 写进脚本、GitHub Issue、提交记录或聊天内容。

## 6. 校验 HermesAgent 数字员工

```powershell
.\scripts\verify-employee.ps1 -RunModelCheck -RunDoctor
```

只有以下项目全部通过才算配置完成：

- Profile 存在且身份正确；
- 仓库技能、Profile 技能和 manifest 哈希一致；
- Profile 模型配置符合清单；
- Hermes 模型检查成功；
- Hermes Doctor 没有阻断错误。

不能通过删除 manifest、关闭检查或手工改哈希来“修复”失败。

## 7. 设置当前生产运行参数

正常使用不需要手工设置；`start-configured.ps1` 会集中设置以下非秘密参数：

```powershell
$env:ETSY_EMPLOYEE_MODEL_ENGINE = 'codex'
$env:ETSY_EMPLOYEE_CODEX_MODEL = 'gpt-5.4'
$env:ETSY_EMPLOYEE_ROW_WORKERS = '3'
$env:ETSY_EMPLOYEE_HERMES_MAX_TURNS = '30'
$env:ETSY_EMPLOYEE_EXCEL_WORKER_TIMEOUT_SECONDS = '3600'
```

含义：

| 变量 | 当前值 | 用途 |
| --- | --- | --- |
| `ETSY_EMPLOYEE_MODEL_ENGINE` | `codex` | Excel 生成使用 Codex CLI |
| `ETSY_EMPLOYEE_CODEX_MODEL` | `gpt-5.4` | 图片事实提取和 Listing 生成模型 |
| `ETSY_EMPLOYEE_ROW_WORKERS` | `3` | 最多同时处理 3 个商品行 |
| `ETSY_EMPLOYEE_HERMES_MAX_TURNS` | `30` | 网站训练/对话最大轮次 |
| `ETSY_EMPLOYEE_EXCEL_WORKER_TIMEOUT_SECONDS` | `3600` | 单个 Excel 作业最长 1 小时 |

大表可把作业总时限提高到 `14400`。并发上限不能超过 4，当前验证值为 3。

这些变量不是凭据，可以写进本人电脑上的启动包装脚本，但不要把包含任何令牌或 API Key 的脚本提交到 GitHub。

## 8. 启动和停止网站

选择专用数据目录。不要使用磁盘根目录、OneDrive、网络盘或目录联接。

```powershell
$DataDirectory = 'D:\EtsyEmployeeData'
.\scripts\start-configured.ps1 -DataDirectory $DataDirectory -BackendPort 8765
```

启动后打开：

- 网站：`http://127.0.0.1:5173`
- Excel 页面：`http://127.0.0.1:5173/excel`
- 后端健康检查：`http://127.0.0.1:8765/api/health`

如果 8765 被其他项目占用：

```powershell
.\scripts\start-configured.ps1 -DataDirectory $DataDirectory -BackendPort 8766
```

停止时必须使用与启动一致的数据目录和端口：

```powershell
.\scripts\start-configured.ps1 -DataDirectory $DataDirectory -BackendPort 8765 -Stop
```

也可以在启动窗口按 `Ctrl+C`。不要按进程名结束全部 Python、Node 或 Hermes 进程。

## 9. 恢复训练知识和历史数据

Hermes Profile 保存员工能力和模型配置；训练知识、审批版本、对话和 Excel 任务保存在网站的 `DataDirectory`，两者不是同一套数据。

换电脑时还必须选择一种业务数据恢复方式：

1. 导入 `etsy-performance-us.zip` 员工迁移包；或者
2. 在停机状态下恢复完整 DataDirectory。

不要同时向同一个目标目录执行两种方式。具体导出、dry-run、正式导入和回滚命令见 [网站与数字员工部署迁移指南](网站与数字员工部署迁移指南.md)。

## 10. 配置完成后的验收

服务启动后执行：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8765/api/health'
Invoke-RestMethod 'http://127.0.0.1:8765/api/employee/status'
```

再上传一个含图片的 Excel 副本，确认：

- 每个有图商品行五个输出字段完整；
- 标题 3–14 个单词；
- 标签恰好 13 个、去重且每个不超过 20 个字符；
- 描述恰好 5 行，每行以 emoji 开头；
- 不臆测材质、尺寸、手工制作或套装内容；
- 原图片、公式、超链接、Sheet 名和布局保留；
- 任一有图商品行最终失败时，不发布残缺工作簿。

## 11. 常见问题

| 问题 | 处理 |
| --- | --- |
| `hermes` 命令不存在 | 安装 Hermes CLI 0.18.2，并确认其目录在 PATH |
| `codex` 命令不存在 | 安装 Codex CLI，并确认 `codex --version` 可用 |
| Hermes 显示 logged out | 重新执行 Hermes OAuth 登录 |
| Codex 显示未登录 | 重新执行 `codex login` |
| Profile 已存在 | 停止程序并备份旧目录；不要覆盖，确认后使用全新 Profile |
| Profile verification failed | 仓库、Profile 或 manifest 不一致；重新 provision 或恢复可信备份 |
| 页面无法连接本地服务 | 检查后端端口、启动窗口错误和端口占用 |
| Excel 作业超时 | 检查登录/网络；大表提高总时限或拆分批次 |
| `rows_failed` | 至少一个有图行补跑后仍失败；不会生成残缺文件，排除模型/网络问题后整单重跑 |

## 12. 安全红线

- 不向 GitHub 提交 Profile `.env`、OAuth、API Key、Cookie 或访问令牌；
- 不直接复制旧电脑的凭据存储；
- 不关闭 Profile 哈希验证；
- 不修改用户的源 Excel，只处理副本；
- 不把 `127.0.0.1` 服务直接暴露到公网；
- 不把原始竞品文案直接写入生成知识；
- 不允许有图商品行失败后仍发布残缺工作簿。

完成本指南后，GitHub 仓库负责可复现的程序、员工身份、技能和配置流程；每台新电脑只需重新安装依赖、创建 Profile、完成两次登录并恢复业务数据。
