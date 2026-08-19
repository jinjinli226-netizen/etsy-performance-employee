# Etsy 表演服数字员工

这是一个仅在本机运行的 Etsy 表演服工作台：通过长期对话训练独立的 Hermes 员工，并把 `.xlsx` 工作簿交给员工处理。员工按产品行原创生成并填写固定的五个 Listing 字段，系统另存结果，不覆盖源文件。

## 第一版能做什么

- 长期对话、附件与训练纠正记录持久保存；
- 上传一个或多个产品行的 `.xlsx`，生成 `head titles`、`13 tags`、`SPECIFICATION`、`Category`、`Instructions for buyers` 五个字段；
- 从审核过的抽象知识中学习，同时执行原创相似度保护；
- 导出和导入员工的可移植数据包（凭据不迁移）；
- 在同一台 Windows 电脑上以 Vue 3 前端和 FastAPI 后端运行。

它不会自动发布 Etsy 商品，不会登录 Etsy 店铺，也不会替你修改价格、库存、财务或账号设置。输出仍需店铺负责人审核。

## 环境要求

- Windows 10/11 与 Windows PowerShell 5.1 或 PowerShell 7；
- Python 3.11（`py -3.11` 可用）与 `uv`；
- Node.js 24、pnpm 11；
- 已安装 Hermes CLI 0.18.2；
- 已安装并登录 Codex CLI（用于 Excel 图片识别与 Listing 生成）；
- 独立 Profile `etsy-performance-us` 已通过 `scripts\verify-employee.ps1` 校验。

全新电脑先创建员工 Profile；脚本不会覆盖同名旧员工：

```powershell
.\scripts\provision-employee.ps1 -Provider openai-codex -ModelId gpt-5.4
```

如果校验提示旧 Profile 的员工资产或清单与当前版本不一致，不要强行覆盖。先停止应用并完整备份该 Profile，再由维护人员迁移或把旧目录整体移出后重新 provision；业务对话与知识仍应随显式 `DataDirectory` 单独备份/迁移。

首次安装项目依赖：

```powershell
Set-Location .\backend
uv sync --extra dev --frozen
Set-Location ..\frontend
pnpm install --frozen-lockfile
Set-Location ..
```

后端的 `uv.lock` 与前端的 `pnpm-lock.yaml` 都已提交；日常安装使用 frozen 模式，依赖升级时再显式更新锁文件。

首次使用模型时，由本人在可见终端完成 OAuth：

```powershell
hermes -p etsy-performance-us auth add openai-codex --type oauth
codex login
```

启动脚本只做只读 Profile 与凭据就绪检查，不会自动认证，也不会打印凭据。

## 一键启动

在项目根目录运行：

```powershell
$env:ETSY_EMPLOYEE_MODEL_ENGINE = 'codex'
$env:ETSY_EMPLOYEE_CODEX_MODEL = 'gpt-5.4'
$env:ETSY_EMPLOYEE_ROW_WORKERS = '3'
.\scripts\start.ps1
```

脚本先构建生产前端，然后只监听本机地址。启动完成后打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。API 仅监听 `127.0.0.1:8765`，前端通过本地代理访问。

如需把数据库和产物放到指定位置：

```powershell
.\scripts\start.ps1 -DataDirectory "D:\EtsyEmployeeData"
```

默认数据目录是 `%LOCALAPPDATA%\etsy-performance-employee\data`。其中包括 `app.db`、`attachments`、`excel-jobs`、`trust`、`migration-packages` 和临时迁移工作区。不要把该目录放进 OneDrive、网络盘或符号链接路径。

## 停止

在运行窗口按 `Ctrl+C`。脚本会核对 PID、进程创建时间、可执行文件、命令行标识和端口归属，只停止本次记录的两个进程。也可在另一个终端使用相同数据目录：

```powershell
.\scripts\start.ps1 -Stop
# 自定义目录启动时：
.\scripts\start.ps1 -Stop -DataDirectory "D:\EtsyEmployeeData"
```

完整的故障恢复与任务重试见 [运维手册](docs/operations/mvp-runbook.md)；换电脑或重新部署请按 [网站与数字员工部署迁移指南](docs/operations/网站与数字员工部署迁移指南.md) 执行。

## 开发与验证

开发模式：

```powershell
.\scripts\dev.ps1
```

核心检查：

```powershell
Set-Location .\backend
py -3.11 -m pytest -q
Set-Location ..\frontend
pnpm vitest run
pnpm build
```

## 当前发布状态

截至 2026-08-19，本机生产路径已经激活并通过真实工作簿验收：22 个有图商品行全部生成，五字段完整，描述为 5 段 emoji 卖点；员工 Profile 校验通过。生成任务采用有限并发和瞬时错误补跑，任一有图商品行最终失败时不会发布残缺工作簿。

迁移到新电脑时仍必须重新创建 Profile、重新登录 Hermes/Codex，并运行 `verify-employee.ps1 -RunModelCheck -RunDoctor`。只对真实表格的副本做验收，不修改源文件。

第一版使用本机单用户 SQLite，不包含 Etsy 自动发布、在线多用户部署、后台浏览器采集、AI 图片生成、宏工作簿（`.xlsm`）、旧版 `.xls`、移动端原生应用或无人审核的自动知识升级。Excel 单文件上限为 50 MB；原创保护是启发式相似度护栏，不能替代人工审核或法律判断。迁移包不包含模型凭据。
