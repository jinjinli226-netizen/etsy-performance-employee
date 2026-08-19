# Codex 一键配置 HermesAgent 设计

日期：2026-08-19  
状态：设计已获用户方向确认，等待书面规格复核  
适用项目：Etsy 表演服数字员工

## 1. 目标

用户把 GitHub 仓库地址或 README 交给一个新的 Codex 任务后，Codex 只需执行 README 中的一条命令，即可完成项目级配置：

1. 检查新电脑的系统依赖；
2. 按锁文件安装后端和前端依赖；
3. 创建或安全复核 `etsy-performance-us` Hermes Profile；
4. 检查 Hermes 与 Codex CLI 登录，缺失时启动本人可见的登录流程；
5. 运行员工 Profile、模型和 Hermes Doctor 校验；
6. 使用当前已验证的非秘密参数启动网站；
7. 给出明确的成功地址或可执行的失败原因。

成功标准是：不需要用户手工拼接环境变量或寻找脚本；除浏览器/OAuth 授权外，Codex 可以按 README 完成配置和验证。

## 2. 非目标

- 不自动安装 Git、Python、Node、uv、pnpm、Hermes 或 Codex 等系统级程序；缺失时停止并报告精确依赖，不申请管理员权限。
- 不保存、复制、打印或提交 OAuth、API Key、Cookie、访问令牌或 Profile `.env`。
- 不覆盖、删除、改名或重建已有 Hermes Profile。
- 不自动导入迁移包或覆盖业务 DataDirectory；数据迁移继续使用现有迁移指南。
- 不把本地网站变成公网、多用户或云端服务。
- 不绕过员工资产哈希、模型检查、Doctor 或 Excel 输出校验。

## 3. 方案选择

采用“安全一键配置”，不采用以下替代方案：

- **系统软件全自动安装**：依赖 `winget`、管理员权限和外部安装源，失败面与供应链风险较大。
- **Docker 化**：Hermes、Codex OAuth、可见浏览器和本机 Excel 文件交互会变得更复杂，不符合当前 Windows 本地应用边界。

安全一键配置只自动执行仓库可控、可校验、可重复的步骤；系统程序和本人授权保留为显式边界。

## 4. 用户入口

README 顶部新增“交给 Codex 一键配置”章节，要求 Codex：

1. 阅读 README 和本设计对应的操作说明；
2. 不读取或显示任何凭据文件；
3. 在仓库根目录运行：

```powershell
.\scripts\bootstrap-new-machine.ps1 `
  -DataDirectory 'D:\EtsyEmployeeData' `
  -BackendPort 8765 `
  -Start
```

如果 8765 被占用，Codex根据只读端口检查改用 8766，并在后续所有命令中保持一致。用户未指定 DataDirectory 时，脚本使用 `%LOCALAPPDATA%\etsy-performance-employee\data`。

## 5. 组件设计

### 5.1 `scripts/bootstrap-new-machine.ps1`

职责是配置编排，不承载网站运行实现。参数：

- `DataDirectory`：可选，默认本机标准数据目录；
- `BackendPort`：默认 8765；
- `FrontendPort`：默认 5173；
- `ModelId`：默认 `gpt-5.4`；
- `ReasoningEffort`：默认 `high`；
- `Start`：配置与校验成功后进入网站监督进程；
- `NonInteractive`：禁止启动登录流程，发现未登录就失败；
- `HermesExecutable`、`CodexExecutable`：默认从 PATH 解析，支持测试和非标准安装路径。

执行阶段严格按以下顺序：

1. 从 `$PSScriptRoot` 解析仓库根目录，不依赖调用者当前目录；
2. 验证 DataDirectory 不是磁盘根目录，不允许路径联接或重解析点；
3. 检查 PowerShell、Git、Python 3.11、Node、pnpm、uv、Hermes 和 Codex；
4. 在 `backend` 执行 `uv sync --extra dev --frozen`；
5. 在 `frontend` 执行 `pnpm install --frozen-lockfile`；
6. Profile 不存在时调用 `provision-employee.ps1` 创建；Profile 已存在时不修改；
7. 检查 Hermes/Codex 登录；交互模式下只在缺失时调用官方登录命令，非交互模式下失败；
8. 调用 `verify-employee.ps1 -RunModelCheck -RunDoctor`；
9. 输出配置完成摘要；带 `-Start` 时调用 `start-configured.ps1`。

任何外部命令非零退出都立即停止。脚本不得在失败后继续启动部分配置的服务。

### 5.2 `scripts/start-configured.ps1`

职责是集中保存经过验证的非秘密运行参数，然后调用现有 `start.ps1`。它设置：

- `ETSY_EMPLOYEE_MODEL_ENGINE=codex`
- `ETSY_EMPLOYEE_CODEX_MODEL=gpt-5.4`
- `ETSY_EMPLOYEE_ROW_WORKERS=3`
- `ETSY_EMPLOYEE_HERMES_MAX_TURNS=30`
- `ETSY_EMPLOYEE_EXCEL_WORKER_TIMEOUT_SECONDS=3600`

参数包括 DataDirectory、BackendPort、FrontendPort、HermesExecutable、HermesHome 和 `Stop`，均透传给 `start.ps1`。启动与停止必须使用同一组 DataDirectory 和端口。

这个脚本不写注册表、不修改用户级环境变量，只影响当前进程及其子进程。

### 5.3 README 与运维文档

README 提供 Codex 唯一入口、预期停顿点和完成判定。HermesAgent 配置指南补充参数解释和手工恢复；部署迁移指南继续负责业务数据导入。

README 明确告诉未来 Codex：

- 不要询问用户提供 API Key；
- 不要读取 Profile `.env` 或输出配置文件全文；
- OAuth 页面出现时让用户本人完成；
- 缺少系统依赖时报告，不擅自安装；
- 只有完整验证通过才能报告配置完成。

## 6. 登录与秘密处理

Hermes 登录命令固定为：

```powershell
hermes -p etsy-performance-us auth add openai-codex --type oauth
```

Codex 登录命令固定为：

```powershell
codex login
```

脚本只调用状态命令判断是否需要登录，不读取凭据存储。登录子进程继承可见终端，用户直接与官方流程交互。状态和错误日志必须避免回显令牌；仓库的秘密扫描继续作为推送前检查。

## 7. 幂等性与已有状态

- 重复运行依赖安装使用 frozen 锁文件，结果可重复；
- Profile 不存在才 provision；已存在只验证，不覆盖；
- 已登录时不重复触发登录；
- 已有 DataDirectory 不删除、不清空、不导入；
- `-Start` 使用现有启动器的进程身份和端口所有权保护；
- 端口冲突时报错，由 Codex读取只读端口信息后选择另一个端口重试。

## 8. 错误处理

错误信息必须指出阶段和下一动作，同时不泄漏输入或凭据：

| 阶段 | 失败行为 |
| --- | --- |
| 缺少系统命令 | 列出缺失名称与版本要求，停止 |
| 锁文件安装失败 | 保留包管理器退出码，停止 |
| Profile 已存在但校验失败 | 要求备份/人工迁移，不覆盖 |
| Hermes/Codex 未登录 | 交互模式启动登录；非交互模式停止 |
| 模型检查或 Doctor 失败 | 不启动网站 |
| 端口占用 | 不停止未知进程，提示更换端口 |
| DataDirectory 不安全 | 拒绝磁盘根、重解析点和链接路径 |

## 9. 测试设计

遵循测试驱动开发：先新增失败测试，再实现脚本。

自动化测试覆盖：

1. 两个 PowerShell 文件可由 PowerShell 解析器无错误解析；
2. README 包含唯一一键命令、OAuth 人工边界和禁止读取凭据说明；
3. 启动包装器设置五个固定运行参数并透传端口、DataDirectory 与 Stop；
4. bootstrap 使用 frozen 安装、只在 Profile 缺失时 provision，并检查每个外部命令退出码；
5. bootstrap 不包含删除 Profile、修改执行策略、`winget`、凭据读取或秘密回显；
6. `NonInteractive` 在缺少登录时失败，不静默跳过；
7. 现有启动、Profile、迁移和 Excel 测试保持通过。

验证命令至少包括：

```powershell
py -3.11 -m pytest backend/tests/test_startup_docs.py backend/tests/test_employee_assets.py -q
py -3.11 -m pytest backend/tests -q
pnpm --dir frontend build
.\scripts\verify-employee.ps1
git diff --check
```

不在当前真实 Profile 上模拟未登录或重新 provision；破坏性分支通过临时目录、假命令或静态安全契约测试。

## 10. 完成判定

只有同时满足以下条件才可提交并推送：

- README 中 Codex 的单条入口命令有效；
- 新脚本通过 PowerShell 语法和自动化测试；
- 现有后端测试与前端构建通过；
- 当前员工 Profile 只读校验仍通过；
- Git 工作区无未提交修改；
- GitHub `master` 与本地 HEAD 哈希一致；
- 推送内容不包含凭据或用户业务文件。
