# claw-factor-lab

独立部署的量化研究平台，以 claw-data 为数据提供方、Qlib 为研究与回测内核。Hibro 可以通过 API 提交研究请求，但不是本平台的运行依赖。

当前版本：0.1.0，日频研究工程原型。支持已验收的数据快照、因子目录、后台任务、统一结果归档和基础控制台。

## 启动

需要 Docker Compose 和 Python 3。首次构建会编译 Qlib，耗时较长。

```sh
python3 scripts/init_env.py
docker compose up -d --build --wait
```

打开 http://127.0.0.1:8610 即可使用，无需登录或平台访问密钥。服务仅监听本机。此版本面向本地使用；跨机器部署前需要增加认证、TLS 和访问控制。

四个长期运行的服务：API/控制台、worker、scheduler、PostgreSQL。研究以 worker 子进程运行，完成后退出；平台持续驻留。所有服务设置自动重启，Docker 本身也需要运行。

## 数据与结果

- PostgreSQL 命名卷保存数据集索引、因子版本、任务、事件、附件索引和计划。
- `storage/datasets/<dataset-id>/` 保存原始快照和 Qlib provider。
- `storage/runs/<run-id>/attempt-N/` 保存配置、执行日志、结果、模型及压缩包。
- 容器重建不会删除上述数据。不要执行 `docker compose down -v`，除非确实要删除数据库。
- 数据、数据库密码和研究产物不进入 Git；公共仓库只包含代码、配置模板和文档。

导入已验收的快照/provider（宿主机路径替换为实际目录）：

```sh
docker compose run --rm -v /absolute/export:/import:ro api python scripts/register_dataset.py \
  --snapshot /import/snapshot --provider /import/provider --name daily-snapshot
```

原始导出及适配入口分别是 `fetch.py`、`adapter.py`，使用 `--help` 查看参数。平台不会自动拉取最新行情；新增快照需要独立导出、验收并登记。`config/pilot.json` 是 12 只股票的固定样本配置，不能代表历史全市场选股结果。

## 首版支持范围

- 159 个版本化因子条目：Alpha158 的 158 个特征，以及 Momentum20。
- 两个内置组合：Momentum20 排序、Alpha158 + LightGBM，均使用 TopkDropout 日频回测。
- API 提交任务、幂等去重、状态查询、取消、失败重试、事件记录和附件下载。
- 提交时冻结数据哈希、配置、因子组合和引擎版本；重试保留独立 attempt。
- 简单固定间隔调度，默认禁用；错过多个周期时合并触发一次。

**因子目录当前只读，尚不支持用户新增因子或自由组合计算。** 后续应增加因子注册、表达式验证、依赖和版本管理、组合发布及兼容性验证。首版不是完整的多用户生产平台：没有用户/角色体系、交易日历调度、自动数据刷新、实盘交易或完整前端计划管理。

历史快照存在固定股票池选择偏差，也不提供完整历史时点数据版本。回测用于工程验收，不能据此判断策略收益有效性。

## API 与 Hibro

OpenAPI 文档：http://127.0.0.1:8610/docs 。本地 API 无需认证。

典型流程：查询 `GET /api/v1/datasets`，提交 `POST /api/v1/runs`，轮询 `GET /api/v1/runs/{id}`，按附件 ID 下载结果。

```json
{
  "dataset_id": "替换为已登记 UUID",
  "title": "日频基线研究",
  "external_ref": "hibro:request-123",
  "idempotency_key": "hibro:request-123",
  "parameters": {"topk": 4, "n_drop": 1, "seed": 42}
}
```

相同幂等键和请求返回同一任务；同键不同内容返回冲突。Hibro 只需保存任务 ID，无需直接访问本平台数据库或存储目录。

## 测试

平台测试只允许对专用 `factorlab_test` 数据库执行，测试会清空该测试库的业务表。

```sh
docker compose exec -T postgres psql -U factorlab -d postgres -c 'CREATE DATABASE factorlab_test'
docker compose run --rm -e DB_NAME=factorlab_test api python -m pytest -q
```

## 升级与备份

Qlib 固定为 v0.9.7 对应提交 `da920b7f954f48ab1bb64117c976710de198373e`，未修改其源码。通过 `adapter.py`、`pipeline.py` 和版本化任务配置隔离平台与内核。新引擎需要独立版本标识、依赖锁定、数据适配测试及固定样本回归，再迁移默认任务。当前 worker 拒绝不匹配的引擎版本；旧任务重跑需要保留对应旧镜像。

升级前同时备份数据库、`storage/` 和 `.env`，并保留旧镜像。为得到一致备份，先等待任务结束，再停止 API、scheduler、worker，使用 `pg_dump` 导出数据库并复制 storage，完成后启动服务。`.env` 备份应加密保存。仅备份报告文件不能恢复任务索引，仅备份数据库也不能恢复模型和报告。

首版数据库采用版本 1 初始化结构；未来结构变更应提供显式迁移和回滚说明，不应依赖自动建表来完成升级。
