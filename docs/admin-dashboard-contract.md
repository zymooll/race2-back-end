# 管理端评估数据与看板契约

## 评估记录

`AssessmentRecord.result_json` 保存评估引擎返回的完整结果快照。评估记录分页和详情响应均保留 `result`；另将管理端常用字段提升为顶层，便于列表和详情直接消费：

- `contributions`: 指标贡献项，包含 `factor`、`category`、`weight`、`score`。
- `deductions`: 扣分项，包含 `factor`、`score`、`reason`。
- `veto`: 一票否决原因，未命中时为 `null`。
- `overrides`: 被触发的规则标记，未触发时为空数组。
- `completeness`: 数据完整度，范围为 0 到 1。
- `riskAlerts` 与 `transparency`: 风险提示及本次评估的数据来源、指标状态、有效覆盖度和缺失项快照。
- `engineUsage`: 本次结果实际执行的专家评估调用数和成功参与融合的数据层应用数；旧记录缺少此字段时保持未跟踪。

历史记录没有相应结果字段时，顶层字段可能为 `null`；原始 `input` 和 `result` 快照仍完整返回。详情同时带回 `indicatorValues`。

`PUT /api/v1/risk/records/{id}/outcome` 支持登录用户回填本人记录，管理员可维护所有记录（含匿名记录）。结果枚举为 `pending`（待回填）、`normal`（正常还款）、`overdue`（逾期）与 `rejected`（未放款）；备注去除首尾空白后最多保存 255 字符。列表与详情均返回 `outcome`、`outcomeNote`、`outcomeAt`，回填不改写原始评分或结果快照。

## 管理端看板

`GET /api/v1/admin/dashboard?days=14` 返回最多 90 天的统计，默认 30 天。接口通过 `require_admin` 保护，不属于公开 `/api/v1/dashboard/*` 接口。

评估总数、平均分、风险等级分布、平均完整度、一票否决次数、规则触发次数和每日评估趋势均依据窗口内的 `AssessmentRecord`。规则触发次数是所有 `result_json.overrides` 列表元素的总数；否决次数是有非空 `result_json.veto` 的记录数。缺少完整度数据的历史记录不参与完整度平均值。

账号行为统计依据评估记录的 `user_id`：账号评估数统计非空归属，匿名评估数统计空归属，活跃账号数统计窗口内出现过的不同 `user_id`。用户排行为窗口内评估次数最多的前十个账号。

`engineUsage` 汇总结果快照中记录的真实专家评估调用和数据层融合应用，并给出窗口内存在 `engineUsage` 快照的记录数与没有快照的历史记录数。旧记录的调用数不会推测；日趋势只加总被跟踪记录。

`assessmentApiRequests` 按 `ApiLog` 中 `POST` 且路径以 `/risk/assess-dynamic` 结尾的请求日志计数。HTTP 非 2xx 计为失败；HTTP 2xx 时再读响应预览顶层业务 `code`，`200` 计为成功，其他业务码计为失败；响应预览缺失或无法解析时计为未知。该频次包含请求校验失败等错误请求，表示接口请求量，不能代替实际专家调用或数据层应用数。统计受 API 日志保留和清理策略影响；已清理的日志不会计入频次。

## 访问边界

`/admin/dashboard` 与 `/admin/stats` 一样要求有效管理员账号。前端管理看板调用此私有端点；公开竞赛看板继续只使用 `/dashboard/*` 现有汇总数据。
