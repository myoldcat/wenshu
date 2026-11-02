# 文枢前端

本目录是文枢 React 19／Vite 6 前端源码，统一使用 `frontend/`。文库、阅读、Wiki 图谱、智能指令与六步写作调用后端持久业务接口；完整入口见[项目说明](../README.md)、[后端说明](../server/README.md)和[用户使用文档](../docs/README.md)。

## 安装与构建

在本目录执行：

```powershell
npm ci
npm run build
```

依赖由 `package-lock.json` 固定。Vite 客户端产物位于 `dist/client/`，包含主应用和独立写作运维页。`npm run build` 还运行原有本地模板整理脚本；`.openai/`、`worker/` 和相关 Sites 模板保留在源码中，构建不代表向任何托管平台发布。

Windows 本地联调需同时启动 API 与 Worker。从项目根目录执行：

```powershell
./scripts/start-local.ps1 -FrontendPath (Join-Path $PWD 'frontend')
```

浏览器入口 `http://127.0.0.1:4173`，默认代理到回环 API `8787`。只运行 `npm run dev` 不会启动后端或后台任务。停止使用根目录 `./scripts/stop-local.ps1`，安装 Python 依赖与资料目录说明见[后端说明](../server/README.md)。

## 服务配置

`vite.config.mjs` 使用 `WENSHU_FRONTEND_HOST`、`WENSHU_ALLOWED_HOSTS` 和 `WENSHU_API_PROXY_TARGET` 控制监听、主机白名单及回环代理目标。变量与通用开发配置见[项目说明](../README.md)，服务部署见[服务端说明](../deploy/server/README.md)。实际主机地址、账号、运行数据和验收记录在仓库外单独保存。

## 功能与主要源码

| 入口 | 主要职责 |
| --- | --- |
| `src/App.jsx`、`src/HomeWorkspace.jsx` | 应用导航、首页与持久服务状态 |
| `src/DocumentCardGrid.jsx`、`src/DocumentDotNav.jsx`、`src/CategoryTree.jsx` | 资料卡片、选择、定位点阵与主题／来源／日期目录 |
| `src/DocumentReader.jsx`、`src/AIReader.jsx`、`src/OriginalFilePreview.jsx` | 固定原文版本、摘要／译文／对照、人工修订及来源预览 |
| `src/GraphWorkspace.jsx` | 基于真实 Wiki 数据的知识图谱与来源浏览 |
| `src/Writing.jsx` 及 `src/writing*` | 主题设定、资料筛选、写作纲要、正文编写、文章配图、检查校验与导出 |
| `src/Knowledge.jsx`、`src/PromptWorkspace.jsx`、`src/TemplatePromptEditor.jsx` | 结构模板与当前智能指令 |
| `src/SettingsPanel.jsx`、`src/ThemeSwitcher.jsx` | 模型设置、帮助入口与外观切换 |
| `src/theme-layout.css`、组件 CSS、`src/assets/`、`public/` | 共享布局、主题、品牌和界面素材 |
| `tests/` | Node 测试；根目录 `scripts/qa-*.mjs` 等提供隔离界面检查 |

已保存资料、文稿、模型配置和指令版本由后端维护；未保存编辑草稿仍需显式保存。外观偏好可记忆于浏览器，但模型密钥不进入 localStorage。正式主题包括青玉 `jade`、流动玻璃 `glass`、丹朱鎏金 `civic`，另有通过明确配置使用的 `demo` 演示主题。

帮助固定按应用类型展示[服务端版或 Windows 本地版](../docs/help.md)，包含最新六步及两种 Windows 发行方式。浏览帮助不安装示例、不调用模型、不覆盖旧手册快照。

源码和应用包不附学习文章、教程或例文，界面不提供添加或导入演示材料的入口。帮助只读，不写入文库或创建文稿；资料使用正常文件选择、拖放和网页链接导入，旧资料与人工修改保留。

## 验证

常规源码整理检查使用本地构建及相关 Node 测试；交互验收使用合成资料和模拟 API，避免真实保存、删除、模型调用或自动加工。跨主题及窄屏验证范围按当前功能要求执行。不要因页面或旧文档中的演示数据推断服务已连接或任务已完成。

界面设计过程稿、机器专用信息与实际运行证据不作为前端运行资源提交；运行所需品牌图片和静态素材位于 `src/assets/` 与 `public/`。
