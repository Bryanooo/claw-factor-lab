# Alpha191 — gtja-2017-v1

来源：[国泰君安《基于短周期价量特征的多因子选股体系》，2017-06，表 6（第 11–17 页）及附录 2（第 30–31 页）](https://guorn.com/static/upload/file/3/134065454575605.pdf)。本实现独立编写，计算公式位于 `factors/alpha191.formulas`，计算引擎在 `factors/alpha191.py`。未修改 Qlib 内部代码，也不依赖 DolphinDB。

这是明确解释了原文歧义的平台版本，不能保证与其他供应商同名 Alpha191 的数值完全一致。公式版本与实现 SHA256 随任务冻结；修改实现必须发布新的因子集版本，不能覆盖已有版本。完整 191 项均有实现，包括第 30 项的多元回归。

## 数据与运算约定

| 变量 | 含义 |
|---|---|
| O / H / L / C | 开/高/低/收，首日锚定的复权价格，单位元 |
| V | 反向复权的成交股数 |
| W | 成交额/原始股数，再进行同口径复权的 VWAP |
| A | 成交额，单位元 |
| BC / BO | 配置指定基准的原始收盘/开盘价 |
| MKT / SMB / HML | 外部提供的日频 Fama–French 三因子收益，十进制，不是百分数 |
| R | C / DELAY(C,1) - 1 |

Qlib 原数据含按个股首日收盘价归一化的价格和成交量。本桥接层恢复元/股单位，保留复权；不直接把 0–1 附近的归一化价格用于含固定阈值的 Alpha191。成交额保持元。基准使用该快照配置的指数，当前示例是沪深 300。

- 输入矩阵的行是交易日期，列是同一股票池的证券。RANK 是同日横截面百分比排名，平手取平均；TSRANK 是滚动窗口内的百分比排名。排名范围随股票池改变。
- 所有滚动窗口含当日，必须有完整的 n 个有效值。STD/COV 使用样本统计量（ddof=1）。CORR 对零方差序列返回缺失。
- SMA(x,n,m) 是递归平滑，权重 m/n；首个有效值作种子，缺失后重启。不用普通 n 日均值代替。
- WMA 使用归一化的 0.9^i 权重，越近权重越大；DECAYLINEAR 使用归一化的 1..n 权重。
- MIN/MAX 是逐元素极值；TSMIN/TSMAX 是滚动极值。原文把二者混写的地方按参数语义拆开。
- HIGHDAY/LOWDAY 是距最近一次最高/最低值的间隔，当日为 0，平手取最近值。
- REGBETA 和 REGRESI 使用含截距回归；趋势回归的解释变量是 1..n。第 30 项取每个 60 日窗口末日残差，再对平方做 20 日 WMA。
- 条件中的缺失继续传播；零分母、非正数对数、无效幂运算、无限值转为缺失，不以 0 冒充。SMA 不跨缺失插值。第 143 项按原文 SELF 语义保留已有状态，缺失日不输出也不计算跨缺失收益。
- `A049` 等名称是其他编号因子的显式依赖，不是额外数据。
- 公式解析只允许固定算术、比较和白名单函数，不执行任意 Python。

## 原文歧义与修订清单

以下约定属于本版本定义；没有悄悄套用另一个平台的同名实现：

- **010**：MAX(x,5) is a rolling maximum; repair misplaced parenthesis.

- **021**：REGBETA against sequence 1..6; intercept included.

- **022**：SMEAN spelling interpreted as SMA.

- **023**：Repair STD(CLOSE:20) and malformed ternary punctuation.

- **028**：Preserve printed TSMAX(LOW,9) in second denominator; not the common KDJ correction.

- **030**：Requires supplied point-in-time MKT/SMB/HML; no index-only proxy. OLS with intercept, last residual.

- **036**：Repair misplaced windows: CORR(...,6), then SUM(...,2).

- **054**：STD has no window in report; explicitly choose 10 sessions (same as CORR).

- **116**：SEQUENCE,20 interpreted as sequence 1..20.

- **127**：MEAN has no window; explicitly choose 12 sessions.

- **131**：DELAT spelling interpreted as DELTA.

- **143**：SELF starts at 1 at first valid close. Multiply by raw positive return, not 1+return; underflow may occur.

- **146**：Final SMA missing m: choose m=1. Preserve printed squared smoothed-return denominator.

- **149**：FILTER then 252 selected benchmark-down observations, not 252 calendar sessions; carry last fitted beta on non-selected days.

- **159**：HGIH corrected to HIGH; preserve printed 6*24 weight for the third term.

- **162**：Repair malformed trailing MIN so both rolling extrema apply to RSI12.

- **165**：Rescaled range: range of cumulative deviations inside each 48-session window, divided by window STD.

- **166**：Repair malformed ratio/window punctuation; retain printed first-power centered numerator (not skewness cube).

- **172**：Cancel common TR normalization algebraically; zero directional denominator remains missing.

- **181**：Missing denominator SUM window set to 20; benchmark CLOSE is price, not return; preserve subtraction.

- **183**：Same rescaled-range convention as 165, with 24 sessions.

- **190**：Balance parentheses; squared deviations use return minus geometric threshold consistently.

附录将基准开/收盘释义写反、LOWDAY 误写为最大值，本实现采用变量名称的语义。缺省 DELAY 解释为 1。VOL、VOLUME 是同一字段，AMOUNT 独立读取成交额。

## 缺失数据与模型选择

默认 `missing_factor_policy=error`，缺少依赖即拒绝提交。只有明确设置 `exclude` 才能排除缺少输入的因子，并在训练时进一步排除**训练区间全部缺失**的列；排除决策不查看验证或测试区间。不因局部缺失或常量就改写原公式。LightGBM 使用其自身的缺失值处理。

当前固定 12 股快照缺少 MKT/SMB/HML，因此第 030 项不能用真实数据计算。不可用沪深 300 单因子残差替代。第 064 项在当前样本的训练期全部缺失（小股票池排名常量导致相关系数未定义），显式排除后模型使用 189 项。扩大股票池可能改善覆盖率，但不会自动改变旧快照或旧报告。

报告附件：

- `alpha191-features.parquet`：所有具备依赖的计算列，含缺失值；未做训练期全空列删除。
- `alpha191-quality.json`：实现哈希、请求/计算/模型使用数量、缺失依赖、训练期排除项、逐因子有效行数与缺失比例。
- `lightgbm.txt`、预测、组合持仓、回测指标、收益曲线和完整源码随实验归档。

## 接入第 30 项的数据

准备从 claw-data 导出的三因子日收益 CSV：`date,mkt,smb,hml`，日期唯一，覆盖快照所有交易日，值以十进制表示。来源应说明市场范围、加权方法、可用时间及是否存在历史修订；本工具校验格式与哈希，不替来源保证经济定义或时点正确性。

先创建**新的**快照/provider，绝不修改已登记数据：

```sh
docker compose run --rm -v /absolute/export:/import:ro api \
  python scripts/attach_alpha191_ff.py \
  --snapshot /import/snapshot --provider /import/provider --ff /import/ff.csv \
  --source-description 'claw-data 三因子导出版本和可用时点说明' \
  --out /storage/staging/with-ff-v1

docker compose run --rm api python scripts/register_dataset.py \
  --snapshot /storage/staging/with-ff-v1/snapshot \
  --provider /storage/staging/with-ff-v1/provider --name daily-with-ff-v1
```

附加数据会进入新快照和 provider 的哈希清单，从而得到新的 dataset ID。目录同名时脚本拒绝覆盖。输入完备不代表每个统计量在每个交易日都有定义；仍需检查预热和常量序列。

## API

```json
{
  "dataset_id": "已登记数据集的 UUID",
  "title": "Alpha191 研究",
  "idempotency_key": "alpha191:research-001",
  "factor_set": "alpha191@gtja-2017-v1",
  "missing_factor_policy": "error"
}
```

提交到 `POST /api/v1/runs`。选择 Alpha191 后运行 Momentum20 基线与 Alpha191+LightGBM；Alpha158 模板仍保留。

`GET /api/v1/factors?factor_set=alpha191@gtja-2017-v1&limit=200` 返回全部 191 项定义、版本、输入和歧义说明。`GET /api/v1/datasets` 的 `alpha191` 字段显示当前数据集的输入完备度（不是最终计算有效率）。

## 验证范围

测试覆盖全部 191 项在合成数据上的执行与有限输出、独立手算算子、代表性公式、三因子回归残差、条件回归、缺失传播、SELF 递归及全部公式的前缀不变性。实际 claw-data 快照完成了 190 项计算和 189 项入模的端到端验收。没有声称与第三方商业因子库逐点等价，也没有把工程回测作为投资绩效验收。
