# Etsy 表演服数字员工 MVP 运维手册

本文面向本地负责人，覆盖启动、停止、恢复、Excel 任务重试、数据备份和电脑迁移。所有命令默认在项目根目录执行。

> 发布状态：截至 2026-08-19，本机生产路径已激活并通过真实 22 行图片工作簿验收，员工 Profile 校验通过。跨电脑部署请另见 [网站与数字员工部署迁移指南](网站与数字员工部署迁移指南.md)。

## 1. 运行位置与边界

- 页面：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8765`
- Hermes Profile：`C:\Users\<用户名>\AppData\Local\hermes\profiles\etsy-performance-us`
- 默认业务数据：`%LOCALAPPDATA%\etsy-performance-employee\data`
- Profile 与业务数据是两套独立目录；迁移包不包含 OAuth、API key、Cookie 或浏览器身份。

数据目录下的关键内容：

| 路径 | 用途 | 处理建议 |
| --- | --- | --- |
| `app.db` | 对话、任务、知识、审批与审计主数据 | 服务停止后备份 |
| `attachments` | 对话附件 | 与数据库一起备份 |
| `excel-jobs` | 上传源文件、作业临时区和结果 | 不要手工改写运行中的目录 |
| `trust` | 生成时使用的抽象知识和原创保护快照 | 与数据库一起备份 |
| `migration-packages` | 已导出的迁移包 | 可复制到离线介质 |
| `runtime\migration-capability` | 本机迁移接口临时能力令牌 | 不复制、不打印；正常停机时删除 |
| `runtime\.start-pids.json` | 启动脚本的进程所有权记录 | 不手工编辑 |

## 2. 启动前检查

确认 Python 3.11、Node.js、pnpm、Hermes 可用，并已安装项目依赖。只读校验员工 Profile：

```powershell
.\scripts\verify-employee.ps1
hermes -p etsy-performance-us auth status openai-codex
```

全新电脑应使用 Hermes CLI 0.18.2，并先运行 `.\scripts\provision-employee.ps1 -Provider openai-codex -ModelId gpt-5.6-sol`。provision 脚本会拒绝覆盖同名旧 Profile。若只读校验提示资产或清单版本不一致，先停止应用并完整备份旧 Profile；不要直接覆盖文件，由维护人员迁移，或把旧目录整体移出后重新 provision。显式 `DataDirectory` 的业务数据需要另外备份或导入。

若显示 `logged out`，在可见终端由本人执行：

```powershell
hermes -p etsy-performance-us auth add openai-codex --type oauth
```

不要把 OAuth 返回值、API key 或 `.env` 内容粘贴进日志或聊天。`start.ps1` 不会自动执行认证，只检查是否已就绪。

### 生产激活清单

1. 停止应用，完整备份旧 Hermes Profile 与显式 DataDirectory；
2. 迁移旧 Profile，或把整个旧 Profile 目录移出后重新运行 provision；脚本不会覆盖同名目录；
3. 在可见终端运行 `hermes -p etsy-performance-us auth add openai-codex --type oauth`；该命令已用本机 `hermes auth --help` 核对；
4. 运行 `.\scripts\verify-employee.ps1 -RunModelCheck -RunDoctor`，只有检查成功后才可把 Profile 视为可用；
5. 把真实工作簿复制到临时目录，只处理副本，核对五字段、图片、公式和布局。不要修改 Downloads 中的源文件。

### 图片 + Listing 一键训练

训练命令从 `.xlsx` 中按原顺序读取 Etsy 店铺链接，在可见的 Chrome/Edge 独立窗口中选择首个尚未成功训练的 Listing。每条样本只下载第一张主图；Listing 文本事实与图片可见事实会确定性合并，材质、尺寸、配件、性能等不可见信息不会由图片推断。候选知识必须经过独立 AI 审批和版本令牌复核，满足置信度与安全门禁后才会激活。

默认只训练一条，建议先这样验收：

```powershell
.\scripts\train-vision-listings.ps1 `
  -Workbook "C:\Work\shops-copy.xlsx" `
  -DataDirectory "D:\EtsyEmployeeData"
```

明确限量或批量运行：

```powershell
.\scripts\train-vision-listings.ps1 -Workbook "C:\Work\shops-copy.xlsx" -Limit 5
.\scripts\train-vision-listings.ps1 -Workbook "C:\Work\shops-copy.xlsx" -Batch
```

`-Batch` 必须显式提供，且不能与 `-Limit` 同时使用。每次 Etsy 页面访问后固定等待 15–25 秒（默认 20 秒）；遇到人机校验或空页面时不会尝试绕过。源工作簿只读，运行前后 SHA-256 必须一致；规范化图片保存在 `training-evidence`，运行、样本、AI 审批和激活谱系保存在 `app.db`。终端只输出运行 ID、状态和计数，不输出原始 Listing、图片内容或凭据。

## 3. 启动与停止

默认启动：

```powershell
.\scripts\start.ps1
```

显式指定数据目录：

```powershell
.\scripts\start.ps1 -DataDirectory "D:\EtsyEmployeeData"
```

启动脚本会构建前端、检查 8765/5173 端口、记录进程身份，并持续监督端口归属。停止首选运行窗口中的 `Ctrl+C`；窗口异常关闭后，可运行：

```powershell
.\scripts\start.ps1 -Stop -DataDirectory "D:\EtsyEmployeeData"
```

脚本不会按进程名批量结束 Python 或 Node。若 PID 已被复用或身份不一致，它会拒绝停止；此时先用 `Get-CimInstance Win32_Process` 和 `Get-NetTCPConnection` 人工确认，不要删除所有 Python/Node 进程。

## 4. 异常退出恢复

应用启动时会把中断的对话和 Excel 作业标为可识别的失败状态。恢复顺序：

1. 用相同的 `-DataDirectory` 重新运行 `start.ps1`；
2. 确认页面能加载历史对话和任务；
3. 对 `interrupted` 的对话点击消息重试；
4. 对 Excel 历史失败任务重新选择原 `.xlsx` 再上传；浏览器刷新后不会保留本地 File 对象；
5. 已完成并有产物的任务直接下载，不要重复覆盖原文件。

若 `.start-pids.json` 指向仍存活但身份不匹配的进程，启动器会保护性停止。先核对 PID、创建时间、命令行和端口所有者；仅在确认记录对应进程已经不存在后，备份并移走这个元数据文件。

## 5. Excel 任务与错误处理

任务失败时，先保留源工作簿。系统没有“复用旧服务端文件”的重试接口：同一次页面会话可点“使用原文件重试”；刷新或换电脑后必须重新选择原文件上传。每次成功都会生成新工作簿，源文件 SHA-256 不应变化。

| HTTP/错误码 | 含义 | 恢复动作 |
| --- | --- | --- |
| `507` / `knowledge_capacity_exceeded` | 竞品证据保护容量达到上限，生成被阻断 | 不要盲目重复上传；MVP 没有证据删除/归档界面，恢复最近一次容量正常的完整备份，或交由维护人员安全整理数据库后再启动 |
| `507`（迁移导出） | 数据盘空间或迁移包存储不可用 | 释放空间、检查权限后重试导出 |
| `cleanup_failed` | 作业临时目录清理失败 | 停止服务，备份数据目录，检查占用/杀毒软件；不要手工删除范围不明的目录 |
| `interrupted` | 应用重启时任务仍在排队或运行 | 重新选择原文件并创建新任务 |
| `worker_unavailable` | 员工 Excel 子进程未能启动 | 校验 Python 依赖、Profile 与脚本文件，再重试 |
| `invalid_worker_event` | 员工返回的 JSONL 进度协议无效 | 保留日志与作业 ID，重启后重试；持续出现则停止使用该产物 |
| `originality_failed` | 输出与受保护竞品证据过近 | 调整输入或审核知识规则后重新生成，不能绕过原创保护 |
| `source_modified` | 处理过程中源文件发生变化 | 关闭会修改文件的软件，重新选择未改变的源文件 |
| `invalid_artifact` | 输出结构、校验和或安全检查失败 | 不使用产物；检查磁盘与工作簿兼容性后重试 |
| `worker_failed` / `failed` | 员工未完成该任务 | 查看页面上的安全错误摘要，确认凭据与网络后重试 |

仅旧版本遗留任务可能出现 `needs_review` 状态；该状态不代表当前版本会创建新的待复核任务。遇到遗留记录时，必须人工审核五个字段后再用于 Etsy。

## 6. 日常备份

建议每次批量训练或重要 Excel 处理后做两类备份：

1. 按 `Ctrl+C` 正常停止服务，把整个显式 `DataDirectory` 复制到仅本人可访问的离线位置；
2. 生成经过校验和、秘密扫描与证据脱敏的可移植迁移包。

不要在应用运行、SQLite 正在写入时直接复制 `app.db`。不要把 `runtime\migration-capability`、Hermes `.env`、OAuth 存储或浏览器 Profile 当作迁移材料。

## 7. 导出员工

服务运行时，脚本从 DataDirectory 内私有的 `migration-capability` 读取本机能力令牌，调用仅限 localhost 的迁移 API，并验证下载大小、SHA-256 和 ZIP 文件头：

```powershell
.\scripts\package-employee.ps1 `
  -DataDirectory "D:\EtsyEmployeeData" `
  -OutputPath "D:\Backups\etsy-performance-us-2026-08-14.zip"
```

目标 `.zip` 必须是尚不存在的新路径。导出包含可移植的对话、抽象知识、规则版本、审批/回滚记录、审计和员工资产；不包含附件内容、原始竞品文案、登录凭据或浏览器身份。导出响应中的 `credential_status` 为 `pending` 是正常现象。

## 8. 导入到另一台电脑

1. 在新电脑安装相同项目依赖并创建/校验独立 `etsy-performance-us` Profile；
2. 用新的空 DataDirectory 启动服务；
3. 先向 `POST /api/migration/imports?dry_run=true` 发送 `multipart/form-data`，文件字段名必须为 `file`，并携带当前数据目录 `runtime\migration-capability` 的 `X-Migration-Capability` 请求头；
4. 仅当响应 `ready=true` 且 `conflicts` 为空时，再向 `POST /api/migration/imports?dry_run=false` 导入同一文件；
5. 导入会校验 schema、逐文件 SHA-256、路径安全和内容限制，并重建 FTS；
6. 在新电脑单独运行 `hermes -p etsy-performance-us auth add openai-codex --type oauth`；
7. 重新启动，检查历史对话、知识版本和一个测试工作簿。

导入不是覆盖式灾难恢复：已有业务数据发生冲突时会返回 `409`。不要为了强行导入而删除现有数据库。先备份，再换一个空 DataDirectory 或按冲突报告处理。

MVP 暂未提供迁移导入页面或独立导入脚本；导入需要由维护人员通过上述本机 API 完成。能力令牌必须由客户端直接从文件读入内存并放入请求头，不要复制到命令行参数、终端回显或运维工单。

## 9. 安全清理与磁盘问题

- 遇到 `cleanup_failed`，只处理错误对应的作业操作目录；不要对 `excel-jobs`、DataDirectory 或磁盘根目录执行递归删除。
- 遇到 507，先停止新增任务，检查 DataDirectory 所在盘的可用空间和写入权限。
- 不要将 DataDirectory 放在符号链接、目录联接、OneDrive 同步或网络共享内。
- 不要编辑已发布产物后仍把它当作系统校验过的原件；重新生成或另存人工修改版。
- 保留最近一次可恢复的完整目录备份和至少一个经验证的迁移包。

## 10. MVP 已知限制

- 仅支持 `.xlsx`；不支持 `.xls`、`.xlsm` 和含宏工作簿；
- 一次运行仅供本机单用户使用，不是公网、多租户或多人协作服务；
- 业务数据使用本机 SQLite；Excel 单文件上限为 50 MB；
- 只有负责人显式运行图片 + Listing 训练命令时，系统才会在可见浏览器中读取公开 Etsy 页面；不会绕过验证、登录店铺或发布 Listing；
- 当前不生成产品图片；聊天可接收图片作为上下文，但图片生成不在 MVP 范围；
- 竞品数据必须经清洗和抽象知识审批，生成侧拿不到原始竞品正文；
- 原创保护属于启发式相似度护栏，不能替代人工审核或法律判断；
- 训练候选只有通过独立 AI 审批、置信度、安全约束和活动版本令牌复核后才会自动激活；未通过的候选保持 proposed 或 rejected；
- Excel 任务失败后没有服务端原文件“一键重跑”，刷新页面后需重新选文件；
- 迁移不包含模型凭据、附件二进制、浏览器会话和 Etsy 账号信息；
- AI 输出可能需要人工补事实、调整类目或遵守最新平台政策，发布责任仍由店铺负责人承担。
