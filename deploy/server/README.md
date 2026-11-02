# 通用服务部署

此目录提供 Linux 主机网络模式的 Compose 模板、Python 镜像与字体许可。模板默认只使用本机回环地址；实际主机、账号、数据、网络入口和运行记录由维护者在仓库外保存，不提交源码。

## 配置与启动前检查

将 `.env.example` 复制到仓库外，填写目标机器的配置。从项目根检查合并后的模板，示例中的私有文件路径仅是占位符：

```bash
docker compose --env-file /path/to/private/wenshu.env -f deploy/server/compose.yaml config
```

先安装 `frontend/` 的锁定依赖、构建后端镜像，并确认数据挂载目录可由选定 UID/GID 写入，再按目标环境流程启动。模板使用主机网络，API 绑定回环地址，浏览器经前端同源代理访问 API。不要把多个环境的数据目录交叉挂载。

| 变量 | 用途 |
| --- | --- |
| `WENSHU_COMPOSE_PROJECT` | 独立 Compose 项目名 |
| `WENSHU_SOURCE_ROOT` | 目标机器上的源码挂载目录 |
| `WENSHU_DATA_PATH` | 目标机器的数据目录；容器内映射为所属服务的数据位置 |
| `WENSHU_UID`／`WENSHU_GID` | Linux 容器运行身份与挂载权限 |
| `WENSHU_FRONTEND_HOST`／`WENSHU_FRONTEND_PORT` | 前端监听，默认本机回环与 `4173` |
| `WENSHU_API_PORT` | API 回环端口，默认 `8787` |
| `WENSHU_ALLOWED_HOSTS`／`WENSHU_ALLOWED_ORIGINS` | 业务访问的明确 Host 与 Origin 列表 |
| `WENSHU_OPERATIONS_ALLOWED_HOSTS` | 独立运维入口允许主机，默认只允许回环 |
| `WENSHU_RERANK_URL` | API 与 Worker 共用的可选重排地址，默认回环 `8788` |

业务白名单不能代替运维白名单。运维列表使用逗号分隔的具体主机名或 IP，不接受协议、路径、端口或通配符。前端与后端应使用一致的所属环境配置；允许业务入口访问不代表允许从该入口打开运维页。

## 可选重排

重排服务需要单独准备运行环境和模型权重，均不随源码提供：

```bash
docker compose --env-file /path/to/private/wenshu.env \
  -f deploy/server/compose.yaml -f deploy/server/compose.reranker.yaml \
  --profile reranker config
```

启用可选模板时设置 `WENSHU_RERANK_RUNTIME`；模型路径由 `WENSHU_RERANK_MODEL_PATH` 指定，端口由 `WENSHU_RERANK_PORT` 指定，GPU 由 `WENSHU_RERANK_GPU` 选择。API 与 Worker 必须指向同一所属服务。模板只声明所选设备，不管理其他模型容器。

## 数据、帮助与日常使用

业务模型通过系统设置配置，API 与 Worker 共用所属环境的数据和凭证资源。备份前停止对应服务，再整体保存数据库及伴随文件、快照、图片和私有凭证；Linux 加密资源需保全，Windows 跨账户或设备通常需重新输入密钥。

两版图文帮助位于项目运行资源中，按应用类型只读展示。启动和阅读不把帮助或演示资料导入文库，不创建示例文稿或调用模型；正常文件、拖放和网页导入继续可用。

依赖安装、构建与通用配置见[项目说明](../../README.md)，实际操作见[用户使用文档](../../docs/README.md)。部署覆盖、机器清单、凭据、运维指纹及验收记录始终在仓库外维护。
