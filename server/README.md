# 文枢后端

后端使用 Python 3.12、FastAPI、SQLite 和持久任务 Worker，为前端与 Windows 桌面包装器提供同一套业务接口。依赖、通用配置与完整入口见[项目说明](../README.md)，用户操作见[使用文档](../docs/README.md)。

## 功能与代码入口

| 范围 | 主要文件 |
| --- | --- |
| API、存储、解析与原文版本 | `app.py`、`store.py`、`parser.py`、`fetcher.py`、`publication_date_extraction.py` |
| 模型、摘要、翻译、分类与任务 | `ai_api.py`、`model_gateway.py`、`enrichment.py`、`classification.py`、`worker.py`、`idle_processing.py` |
| 全文／向量检索与重排 | `retrieval*.py`、`reranker_runtime.py` |
| Wiki、知识图谱与证据 | `wiki*.py`、`year_context.py` |
| 写作、引用、配图、校验与导出 | `writing*.py`、`image_*.py` |
| 智能指令与配套帮助 | `prompts*.py`、`prompt_*.py`、`presets.py`、`presets_api.py` |
| 模型凭证保护 | `credential_cipher.py` |

模型结果、Wiki 与图谱是基于原文的派生内容。结构检查、引用登记和作者确认不代表外部事实核验；摘要与翻译保留结构、来源版本、人工修订与检查点保护。

## 本地运行

从项目根安装 Python 依赖和前端依赖：

```powershell
py -3.12 -m venv server/.venv
& ./server/.venv/Scripts/python.exe -m pip install -r server/requirements.lock
Push-Location frontend
npm ci
Pop-Location
./scripts/start-local.ps1
```

本地默认前端为回环 `4173`，API 为回环 `8787`；可选重排服务默认回环 `8788`。本地停止使用 `./scripts/stop-local.ps1`。通过 `-DataPath` 指定独立资料目录，模型连接在系统设置中配置。源码不包含模型地址、密钥、权重或用户库。

通用服务端运行见[部署说明](../deploy/server/README.md)。Windows 应用的打开与资料维护见[安装版使用](../docs/windows-desktop.md)与[绿色版使用](../docs/windows-portable.md)。机器专用配置、远程账号和实际运行记录单独维护，不进入代码库。

## 数据与任务

API 与 Worker 使用同一所属环境的数据目录，保存数据库及 WAL、原文快照、图片、任务与凭证资源。多个环境不得交叉挂载或回写。备份前正常停止服务，再整体保存所属目录；Linux 凭证资源须一并保全，Windows DPAPI 密钥跨账户或设备需重新输入。

原始资料按固定版本保存，完整字节 SHA-256 用于上传去重；同名不同内容不因文件名合并。任务冻结实际模型、指令和来源版本，保留人工修改、锁定、取消与迟到保护。浏览帮助、指令和任务不保存、迁移或触发模型。

默认资源仅保留只读帮助与许可，不含学习文章、教程或示例文稿。启动和帮助读取不导入资料或创建文稿，演示材料专用入口及导入 API 已移除；正常文件和网页导入继续使用既有流程。旧库内容、人工修改、历史记录与旧帮助快照保留，见[只读帮助说明](../docs/help.md)。

## 隔离测试

`server/tests/` 使用合成资料、临时目录与模拟服务。测试前移除可能继承的在线重排地址；pytest 专用 `--basetemp` 不能指向真实资料。可从项目根运行下列隔离测试命令；实际机器结果与历史验收日志在仓库外保存。

```bash
env -u WENSHU_RERANK_URL WENSHU_DATA_DIR=/tmp/wenshu-source-test \
  python -m pytest server/tests -q --basetemp /tmp/wenshu-source-pytest
```

## Windows 源码打包

在 Windows x64 项目根准备 Python 3.12、Node.js 22 与打包工具，然后运行：

```powershell
./scripts/build-windows.ps1
./scripts/build-windows-portable.ps1
```

桌面依赖锁定在 `deploy/windows/requirements.lock`，前端构建输入为 `frontend/dist/client/`；构建脚本准备平台所需 PDF 原生组件，并只按白名单收集程序、两版帮助、许可、字体和已知依赖。绿色版应基于对应版本的已构建程序制作，不收集用户库、凭据、模型权重或演示素材。

构建输出、安装器、ZIP 与检查记录留在本地忽略目录。构建成功不代表目标机器的 WebView2、安装、升级与卸载分支已完成验证；发布前使用隔离资料和模拟模型分别检查这些行为。
