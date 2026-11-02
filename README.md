# 文枢 Wenshu

文枢将资料导入、阅读整理、Wiki 知识构建与文稿写作放在同一工作台，提供摘要、翻译、分类、检索、带来源引用的写作、配图、校验和 HTML／Word／PDF 导出。模型由用户自行配置，原文版本、人工修订及任务记录分别保留。

## 目录结构

```text
frontend/       React / Vite 前端与合成测试
server/         API、持久任务 Worker、业务逻辑与测试
desktop/        Windows 桌面包装器
deploy/         通用服务、Windows 打包与入口模板
scripts/        通用构建、运行与合成验收工具
resources/      配套帮助和许可证
docs/           用户使用文档
tools/          文档生成与检查工具
```

源码不包含用户资料库、模型权重、凭据、实际测试环境配置、运维记录或预置例文与学习素材。依赖、构建产物、录制报告和生成清单也不提交。开发文档保存在本地 `docs-dev/`，研究资料单独留存，二者均不纳入仓库。

## 本机开发

需要 Python 3.12、Node.js 22 与 npm。从本项目根安装锁定依赖：

```powershell
py -3.12 -m venv server/.venv
& ./server/.venv/Scripts/python.exe -m pip install -r server/requirements.lock
Push-Location frontend
npm ci
npm run build
Pop-Location
./scripts/start-local.ps1
```

本机工作台为 `http://127.0.0.1:4173`，API 使用回环 `8787`，前端通过同源代理访问。默认数据目录为 `data/wenshu/`，可通过 `-DataPath` 指定独立目录；停止脚本拥有的进程使用 `./scripts/stop-local.ps1`。

首次使用先配置模型连接，再显式导入资料。合成测试不调用真实模型或修改用户资料，浏览帮助及功能页面不自动触发加工。PDF 与桌面打包所需原生组件见相应文档。

默认资源仅提供两版只读使用帮助，不附带预置素材或写作示例，也不提供演示材料导入入口或自动预置逻辑。正常文件与网页导入保留；既有资料和历史内容保持原样。

## 运行配置

| 变量 | 用途 |
| --- | --- |
| `WENSHU_DATA_DIR` | 当前服务的数据目录，与源码分开 |
| `WENSHU_FRONTEND_HOST` | 前端监听主机，默认回环 |
| `WENSHU_API_PROXY_TARGET` | 前端同源 API 的回环上游 |
| `WENSHU_ALLOWED_HOSTS`／`WENSHU_ALLOWED_ORIGINS` | 业务允许的主机及来源 |
| `WENSHU_OPERATIONS_ALLOWED_HOSTS` | 独立运维入口白名单，不继承业务公网主机 |
| `WENSHU_RERANK_URL` | API 与 Worker 共用的可选重排地址，默认回环 `8788` |

实际部署值通过仓库外文件提供。服务端模板的挂载、用户、端口、模型运行目录与 GPU 设置见[服务部署说明](deploy/server/README.md)。

## 使用文档

| 文档 | 内容 |
| --- | --- |
| [使用文档索引](docs/README.md) | 阅读、写作、帮助与本地使用 |
| [使用流程](docs/current-workflow.md) | 资料、Wiki、智能指令与写作流程 |
| [服务模板](deploy/server/README.md) | 通用 Compose 与可选模型服务 |
| [Windows 安装版](docs/windows-desktop.md)／[绿色版](docs/windows-portable.md) | 安装、启动与数据位置 |
| [使用帮助](docs/help.md) | 服务端版与本地版图文帮助 |

所有实际主机、账户、域名、端口映射、数据路径、访问白名单、部署覆盖和运行记录都在仓库外管理；代码只提供通用模板。源码修改不自动改变任何已部署服务。

## 版本与交付

```powershell
git status --short
git log --oneline -5
git ls-files
```

`FILE-LIST`、目录树、根 `MANIFEST` 与 SHA 校验清单是本地生成物，不纳入 Git。需要交付时导出到一个尚不存在的目录：

```powershell
python scripts/organize-source.py --output output/wenshu-source
python scripts/organize-source.py --verify output/wenshu-source
```

交付目录仍不携带依赖、实际环境配置、运行数据或运维记录。
