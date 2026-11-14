# HANDOFF —— 给学长（及学长的 AI）的项目交接包

> **用途**：单文件交接包。学长的 AI 只需摄入本文档即可获得完整上下文；深入细节请按第 0 节索引读原文件。
> **更新日期**：2026-11-09 ｜ 维护者：项目组 + AI 结对开发（对外材料一律不含真名/校名/昵称）

---

## 0. 文档索引（按需深入）

| 文件 | 内容 |
|---|---|
| `docs/ROADMAP.md` | **先读这份**：骨架与进度总览。五层 × 四档状态（有肉/半有肉/纸面/空）、三层证据不能互相冒充、里程碑对齐、4 条致命风险。只导航，不定义口径 |
| `docs/BACKLOG.md` | **未完成清单（备忘录）**：28 条（20 条未开、3 条做一半、5 条已关并留痕），每条带「为什么现在不做 / 做完判据 / 十分钟验证 / 谁推」；末尾有收工清单与已关闭留痕 |
| `../项目计划书草稿-v0.4.md` | 完整计划书：定位/背景/差异化/技术方案/创新点/里程碑/风险/依据 |
| `../协作规范-v1.md` | 开发协作规范：小步迭代流程、分支与提交规范、验证沉淀 |
| `docs/data-model.md` | 数据模型 v0 占位（M-1 第 2 步定稿七个实体字段） |
| `docs/diagnosis-report.schema.json` / `docs/diagnosis-report-schema.md` | DTO v2 的**唯一结构真相源**与说明副本。S3-3-6 按 §20.2 第 1 项把 `artifact_invalid` 扩入 `reason_code` 封闭枚举（TD-008 销项），两处同步；状态层按 code 分支、不解析中文 |
| `docs/review-packet.md` | **现役外部评审包**（生成物，勿手改）：仓库实况 + 待拍板清单 + 本轮问题，带内容指纹 |
| `docs/review-packet-manual.md` | 评审包的**手工部分**：项目定位、已定决策、P 清单、Q 问题。改这里，然后跑 `review.bat` |
| `src/arena/review_packet.py` | 评审包生成器。`--check` 可检测过期（仓库漂移）与手改（正文被改）；默认模式现场跑全量单测并如实记录通过率（约 38s），`--fast` 跳过执行、仅收集用例数（约 2s，**对外发包禁用**） |
| `tests/test_dependencies.py` | 依赖可复现性冒烟（Q8）：离线比对 pin 版本与已安装版本 + 核心模块 import。供 CI 调用；本地 `pytest tests/` 也会跑到 |
| `src/arena/assertion_evaluator.py` | **S3-1 + S3-3-2/3/4/5：R3 / R5 / R1 / R2 / R4 五个断言执行器 + 错误模式分类器**。判定表达式与分类规则都从 `tasks/*.yaml` 派生；**S3-3-2 起 `evaluate_assertion()` 是唯一分发入口**（按 `type` 分派 R3 `trace_sequence` / R5 `trace_final_status` / R1 `file_exists` / R2 `json_field_equals` / **S3-3-5 的 R4 `trace_param_schema_valid`**，其余抛 `NotImplementedError`；`evaluate()` 仍是 R3 专用）。`load_task_config()` 只缓存文件文本、每次返回新 dict；五个 `_validate_*_spec()` 均加载期 + 判定期各校验一次（TD-003 同款纪律）。`_validate_calls()` 除 `call_index` 连续外，**Q12(a) 补 `status ∈ status_enum` + `params` 是 dict**，允许值从 YAML 派生。**R1/R2 共用第三个输入源 `artifacts`（产物本体），绝不从 trace 的 `write_report` 自述推断**；`artifacts` 缺 / 坏判 unscored、没交产物判 fail。**R2 按 `docs/executor-semantics.md` §3 实现 `subset` + `tolerance`（禁止浮点 `==`），分类「收集全部命中的 case」而非首命中即止；`depends_on` 折叠须显式传 `dependency_results`，缺了抛错**。**R4 按 `docs/executor-semantics.md` §2 实现 required / type / range / additional_properties 四项 + `all_calls_must_be_valid`，工具声明从 `expected.schema_ref` 指向的 YAML 表读（`load_tool_schemas()`），分类按 cases 首命中即止，`bool` 排除出 `integer`，未知工具名判 unscored**。`--demo` 打印九行对照表。**S3-3-6**：`load_tool_schemas()` 可传 `task`（工具声明不再回落默认 YAML）；R2 补 `parse_error` / `raw_excerpt` 结构化 evidence，三类坍缩可分（TD-015 销项），判定口径与词表 / DTO 未改。**S3-3-7（round-27 P1 + round-28 P2，§29/§30）**：`_filter_expr()` 投影补 `i: call_index`、`_judge()` 返回四元组、新增 `_ambiguous_step()`（P1 的 `params_match` 分支 + P2 的 `http_status` 缺失/null 分支，值写错仍判 fail）、失败步缺字段判 `unscored/trace_invalid`、`call_count` 改数目标工具调用条数；不碰 `_validate_calls()` / audit 派生器 / DTO。**F3 判定层已闭合（TD-018 销项）**。**S3-3-7（round-32，§33）F4/F5 落地**：R4 未知工具不再屏蔽已观测违规（有违规照判 `fail`，`unknown_tools` 降为注记，仅违规为空才 `unscored`）；`expected.target_tools` 非空数组必填（加载期 + 判定期 fail-fast，须在 `schema_ref` 覆盖内）；`checked_calls` 语义改为「受检数」（扣除未知工具）；无目标工具受检调用（含空集）判 `unscored/trace_invalid`（Q4=(b) 复用码）；TD-019a/019b/028 销项 |
| `src/arena/trace_pipeline.py` | **S3-3-6：全链路入口，五条断言全现算**。`raw.trace` + `raw.artifacts` → `recompute_all()` 逐条走 `evaluate_assertion()` → 覆盖 `assertion_results`（手写结果只读 id 壳、内容一律不读）→ `build_report()` → `validate_report()`。`--fixture all` 跑五份夹具。spec / trace 约束 / 工具声明**全部从传入的 `task` 派生**（F2 消解）；`recompute_r3()` 是 S3-2 兼容入口，只现算 R3、不在主链路上，供 71 条历史锁调用。五夹具分数序与 S2 基线逐项一致 |
| `src/arena/store.py` | **M1 第一步（round-75）：SQLite 落库**。7 张表按 `docs/data-model.md` 逐字段对齐（`users`/`skills`/`tasks`/`error_patterns`/`submissions`/`user_skill_states`/`review_items`）+ 2 索引；**外键真开启**（SQLite 默认关，忘设等于无约束）；建库幂等；技能/模式/任务三种种子幂等 upsert；`save_submission` / `load_submission` / `load_report` / `list_submissions`。四条设计决定见模块文档：`user_id` 必须显式给（不从 `agent_id` 推）、`detected_patterns` 是派生列（只认 `state=fail`，真相源是 `diagnosis` 列）、重复 `submission_id` 抛错不覆盖、`created_at` 优先取 `evaluated_at`（复习间隔的时间轴不能记成"今天"）。**只做了"存"，没做"用"**：后两张表有表无读写 |
| `tests/test_store.py` | **16 条落库约束锁**（round-75）：建表幂等 / 外键真生效 / 关连接重开仍读回 / 只落 `ratio` 不落反事实分 / 派生列只认 fail（**含一条构造锁**：夹具测不出"过滤与不过滤"的差别，必须构造 pass+blocked 带模式才咬得住）/ 派生列不回写报告 / 重复 id 不覆盖 / 空 `user_id` 与未注册任务拒绝 / 坏报告不留半条 / 时间轴锚评测时刻 / 列表倒序 + limit / 范围约束在表上。8 组字节级变异全 `DETECTED` |
| `src/arena/submission_api.py` | **M1 第二步（round-76）：黑盒提交服务（HTTP）**。标准库 `http.server`，**零新依赖**（`requirements.txt` 全 pin 且 `test_dependencies.py` 比对本机版本，引框架要连带核 license）。3 端点：`POST /submissions`（提交 → DTO v2）、`GET /submissions/{id}`（读回）、`GET /healthz`。**黑盒的含义**：`trace_audit` 与 `assertion_results` 的 id 壳由平台派生，外部填不了（外部能填审计字段 = 让被测方自报结论）。`task_version` 必填（B-21，缺则 400 专用码且不落库）；给错版本 ≠ 缺字段——走 `voided` 报告。端点只做 TCP 探测不发业务请求（发请求会让被测 Agent 真跑一次，污染 R3）；不可达 → 200 + 全 `unscored`（**不是 `blocked`**，见 schema 文档 §6 订正）。每请求新建 sqlite 连接（`ThreadingHTTPServer` 跨线程，连接不共享）。**无鉴权、只绑 `127.0.0.1`** |
| `tests/test_submission_api.py` | **24 条**（round-76）。真 socket（`port=0` 系统分配），测的是真服务而不是假对象。最硬的一条是**五夹具逐字段对拍**：HTTP 入口与内部链路必须完全相等，把「薄协议层」从注释变成可验证事实。另有 B-21 不落库断言、端点不可达的全 unscored + 归因开关、**「文案不甩锅」锁**（不许出现「请补交步骤」）、重复 id 不覆盖、默认只绑本机。9 组变异全 `DETECTED` |
| `src/arena/mock_tools.py` | **B-03（round-77）：由任务 YAML 驱动的 mock 工具服务**。实现且只实现 YAML 已声明的两件事：`fault_injection`（该 `(tool, match)` 第 N 次调用按声明失败、`then` 放行）与 `tools[].mock.pagination`（`half_open` 区间 / 页大小上限 / 越界返空 / `total` 由夹具长度推导）。`reset_policy: per_submission` 的执行者是 `reset()`——不重置会让上一个学习者用掉故障额度。**刻意不发明**：额外参数忽略（是否算幻觉交 R4 从 trace 判，服务端不替评测器判分）、缺必填 400 `missing_param`（本模块定义，注释已标）；`range != half_open`、未知 `total_source`、未知 `then` 一律**构造期报错** |
| `src/arena/reference_agent.py` | **B-03（round-77）：确定性参考 Agent**（不是 LLM）。真 HTTP 调工具、遇 5xx 真同参重试、真写 `/workspace/report.json`；分页大小与工具名从任务 YAML 派生，不写死。四种 `flaw`：`correct` / `wrong_filter`（不剔除无效行 ⇒ 只打 R2）/ `extra_param`（多传 `schema` 外参数 ⇒ 只打 R4）/ `no_retry`（不重试 ⇒ 打 R1+R5+R3）。跑挂也照样交一份 `crashed` 提交（保留已有调用记录），不干脆不提交 |
| `tests/test_mock_tools.py` | **13 条**（round-77）：故障点只打匹配参数、`reset()` 恢复额度、分页半开区间与 `total` 推导、越界返空、超上限码、未声明工具、产物账本、深拷贝不被调用方污染、三种未实现形态报错 |
| `tests/test_reference_agent.py` | **12 条**（round-77）：trace 结构自洽 + `schema_valid=True` + `call_count>0`|；真跑拿满分；**答案与 `test_inputs.expected` 对撞**；重试同参；四形态矩阵（失败集与 ratio 逐一断言）；崩溃提交不带产物；配置错误在构造期暴露 |
| `src/arena/llm_judge.py` | **B-04（round-79）：judge 双评的语义层**。`JudgeVerdict`（构造即校验：档位 `1–4` 整数，越界**不 clamp** / 不给 `bool` 过；`patterns` 不许重复）/ `double_judge`（n≥2，故障与不一致分开）/ `DoubleJudgeResult`（一致率 = 两两比较中 `rating` 相同的比例；`needs_arbitration` **无默认阈值**）/ `StubJudge`（离线测机制）/ `parse_verdict`（只认 `suspected_patterns`，不做猜测式修复）/ `build_judge_context`（**白名单**：`expected` 与 `assertion_results` 绝不外流）/ `LLMJudge`（`max_tokens` 默认 **4096**：1024 会被思考 token 吃光） |
| `tests/test_llm_judge.py` + `tests/test_llm_judge_llm.py` | **52 条**（round-79）：输出不碰分数与 `detected_patterns` / 越界、词表外、n<2 三条都报错 / 材料序列化后查 `expected`·`1837.99`·rubric id 痕迹 / 清单不带 `detection_rule` / 两次评价必须各打一次接口（不缓存） |
| `src/arena/llm_client.py` | **B-04/B-03b（round-79）：LLM 传输层**，零新依赖。`LLMConfig`（密钥 `repr=False` **且**手写 `__repr__` 打码，两层）/ `LLMClient`（transport 与 sleep 可注入）/ `Completion`。三条实测教训写进代码：**`finish_reason=length` 与「200 但空正文」分开报**（截断要加预算，接口坏才查网络）、重试只对 429/5xx/网络层、`.env` 逐键回落且拦「键名被抄两遍」 |
| `tests/test_llm_client.py` | **30 条**（round-79，全离线）：密钥不进 repr/异常 / 截断与空正文的报错文案必须不同 / 4xx 不重试、5xx 退避递增且封顶 / 请求体与 `response_format` / `field(repr=False)` 这层"死代码"的机械锁 |
| `docs/judge-agreement-distribution.json` | **B-04 实测原始记录**（round-79）：4 批 17 次真调用 `deepseek-flash`、10 对比较 9 对一致；含 `max_tokens=1024` 的**原始报错**与三批目的说明 |
| `scripts/judge_agreement_experiment.py` | **B-04 实验脚本**（round-79）：`--dry-run` 只打印材料不花钱；`--all` / `--runs` / `--max-tokens` / `--purpose`；单份失败不中断全批；结果**累积不覆盖** |
| `src/arena/mastery.py` | **B-07（round-80）：掌握度 P**。`skill_evidence()`（报告 → 单技能证据，按断言 `skill_id` 加权、决策 7 剔除 unscored/blocked）/ `update_p()` / `replay()`（按**解析后时刻**排序，同刻按 id 定序）/ `build_series()`（读库）/ `series_for_lambdas()`。**只算不落库**（`user_skill_states` 无 P 字段）；**不实现衰减**（依赖 B-08 的复习间隔） |
| `tests/test_mastery.py` | **36 条**（round-80）：加权 `c_k` / 决策 7 两条路（剔除 vs 真失败记 0）/ 越界不 clamp / 时间轴按时刻不按字符串 / 同刻定序 / 初值播种 vs 显式 `p0` / 跨用户隔离 / **`test_decay_is_not_implemented_yet`（现状锁，B-08 反转它）** / **`test_gate_failure_still_lifts_credit_skills`（现状锁，"不交产物"仍抬 P）** |
| `scripts/mastery_walk.py` | **B-07 十分钟验证**（round-80）：造三次提交真落库再读回 → 打印 P 序列 + 手算对照 + λ 敏感性（打整条序列，因为 λ 与终值**非单调**）；零依赖、不联网 |
| `fixtures/tool-call-cases.json` | **14 个**边界 trace 用例集（乱序/零调用/只拉首页/跳过故障点/穿插/假重试等）。**Q10-A** 把 4 个原走 default 的 case 改为明写规则命中；**TD-004** 把 `expect.source` 升级为完整 `classification_source` 字符串并新增 `expect.observed_offsets`；**Q12(a) 补 `bad_status_enum` / `params_not_object` 两个 case**（期望 `unscored/trace_invalid`）。后续补 case 仍先改这里 |
| `fixtures/raw-*.json` | 五份 S2 原始提交夹具。**S3-3-3 按 Q13(a) 补顶层 `artifacts`**（产物本体，与 trace 是两份独立证据）；`raw-fake-retry` 刻意保留「trace 无 `write_report` 但产物存在」，留给 P3 的 R6 做交叉比对。手写 `assertion_results` **一字未改**（S3-3-6 起「不再被读」≠「已被删除」，留作 S2 基线）；`size_bytes`（TD-009）、`raw-empty` 的旧分数描述（TD-021 / F10）均已订正或销项 |
| `tests/test_assertion_evaluator.py` | S3-1 的 **55 条**约束用例（含 `call_index` 乱序回归锁、Q10-A 四条明写规则锁、TD-003 八条坏 spec 参数化 + H1 原样反例 + 加载期校验、TD-004 精确来源点名锁、**Q12(a) 五条回归锁（畸形调用判 unscored、枚举派生、枚举缺失拒绝猜测）**、YAML 派生锁、输出可直喂 `build_report()`） |
| `tests/test_file_exists.py` | **S3-3-3 新增，62 条**：R1 判定四态口径、产物绝不从 trace 推断、坏 spec 在接触产物之前失败、加载期校验、精确分类来源、五夹具 R1 与报告基线一致、现算结果可直喂 `build_report()`；**TD-008** 把「`artifact_invalid` 未进 DTO `reason_code` 枚举」钉成可执行红灯证据 |
| `tests/test_final_status.py` | **S3-3-2 新增，29 条**（S3-3-3 / S3-3-4 各移除 1 条已实现的未实现 case）：R5 判定/分类、枚举与规则全从 YAML 派生、未上报与越界判 unscored、坏 spec 在 trace 之前失败、加载期校验、分发路由、未实现 type 拒绝伪造、执行器结果过 DTO + gate 语义（crashed → `disqualified`） |
| `tests/test_json_field_equals.py` | **S3-3-4 新增，110 条**：R2 的 subset / tolerance 边界、禁止浮点 `==`、bool 与非有限数守卫、缺字段与多 pattern 收集、`depends_on` 四态、产物绝不从 trace 推断、27 条坏 spec 参数化、加载期校验、五夹具三方 state 一致、现算可直喂 `build_report()`；**TD-008 在 R2 的红灯证据**、**M7 假锁补强**（`True == 1` 真覆盖） |
| `tests/test_param_schema.py` | **S3-3-5 新增，104 条**：R4 四项校验各命中对应 pattern、range 边界、bool 不当 integer、类型错后跳过 range、多类违规按 YAML cases 顺序取最优先、注入 schema 反证参数名/边界/`additional_properties` 均来自 YAML、六类 trace 不可信一律 unscored、未知工具名假设①、坏 spec / 坏 tools 表的加载期+判定期参数化、现算结果过 `build_report()` + `validate_report()`；**TD-008 在 R4 不成立的正向锁**（`reason_code` 两值都在 DTO 枚举内）、**TD-011** `raw-no-trace` 手写 R4 假基线分歧锁、十六组变异的基线 |
| `tests/test_trace_pipeline.py` | S3-2 的 **71 条**约束用例（夹具合规、R3 确由执行器现算、分数序不变、边界 case 精确 `source` + `observed_offsets`、真相源冲突拒绝、用例集完整性锁；**S3-3-2 新增五夹具 R5 与报告基线一致的参数化判据 + `raw-no-trace` 手写 pass 被改写的分歧锁**；**S3-3-6 新增 24 条**：报告层四项一致性锁、Q5 哨兵污染锁、注入 task 两条正向锁、P-A 双分支、artifacts 正向传递、P-B 回归锁、TD-013 的 0.75 锁、T11 入口防御）；**round-27/28：F3 锁由 7 条现状锁全部反转为 16 条正向/边界锁**（① 三条 + ② 四条转正向，新增 9 条边界锁含 `after` 过滤反例与 C9 显式 null），`-k f3_td018` 16 passed；**round-29/32：F4/F5 锁 7 条现状锁随 Step1/Step2 反转/改写为 13 条正向/边界锁**（`test_f4_td019a_*` / `test_f5_td019b_*`，含 target_tools fail-fast 与 TD-028 验收锁），`-k "f4_td019a or f5_td019b"` 13 passed；**round-33：F9 新增 8 条现状/边界证据锁**（`test_f9_td020_*`，含两层重复、typo deps 静默路径、agent_id audit、跨层/结果层对照），`-k f9_td020` 8 passed；**round-36：6 条现状锁反转为正向锁 + 新增 3 条（key 必需 / 字符串数组 / 类型支持），`-k f9_td020` 11 passed；round-37：③ 锁反转 + 新增 trace/submission 不一致正向锁，`-k f9_td020` 12 passed** |
| `docs/review-response-v1.md` §10 / §12 / §13 / §14 / §15 / §16 / §17 / §18 / §19 / §20 / §21 / §22 / §23 / §24 / §25 / §26 / §27 / §28 / §29 / §30 / §31 / §32 / §33 / §34 / §35 / §36 / §37 / §38 / §39 / §40 | §10：round-8 处置记录。**§12：Q10 拍板 A；TD-001/002、S3-3 判据与拆分**。**§13：S3-3-1（Q10-A）落地**（Q13=(a)、Q12=(a 最小)）。**§14：外部验收回件核证 —— 自述三处被推翻、H1、TD-003~006、D1–D5**。**§15：最小放行批 S3-3-1b/1c，TD-003/004 销项**。**§16：S3-3-2 落地（R5 执行器 + 真分发 + Q12(a)）、判据口径澄清、TD-007、七组变异证据**。**§17：外部评测口径澄清（有判据地提，非禁令）、S3-3-3 落地（Q13(a) 产物本体 + R1 执行器）、TD-008/009、十组变异证据**。**§18：S3-3-4 落地（R2 执行器 + subset/tolerance + 依赖折叠）、两处假设、TD-010、§17.7 自述纠正、十三组变异证据**。**§19：S3-3-5 落地（R4 执行器 + 四项 schema 校验 + 首命中分类）、两处假设、TD-011（no-trace 手写 R4 假基线）、TD-008 在 R4 不成立、十六组变异证据**。**§20：S3-3-6 开工只读核证（五条全现算模拟逐项一致 / §12.5 判据歧义 / TD-008 触发实测 / evidence 差异）、四项人拍板、外评委托包生成与双道逐字自检证据；代码待回件核证后落地**。**§21：S3-3-6 外评回件核证 —— 回件自述「是建议不是验收结论」且未按 §0.4 交付（无核证总表 / 无最小复现），只当建议清单；21 条逐条本地实测核证（成立 11 / 不成立 5 / 现状已符合 4 / 待拍板 1）；回件 5 处事实错误登记（章节映射错位、`_validate_calls` 必填误述、`validate_report` 漏过非法值推测、`build_report` 强制改写因果写反、无效等价变异）；新登记 TD-012（artifacts 缺失扩枚举后「没产物拿 0.5」）、TD-013（`[0,8,17]` 绕开故障点拿 0.75）、TD-014（Q12(a) 边界非 bug）、TD-015（坏 JSON evidence 不可结构化区分）；四项待人拍板（P-A~P-D），本轮未改产品代码**。**§22：同侪审计回件核证（round-20）——回件格式基本符合 §0.4；本地只读探针复核 F1–F10 / Q1–Q6，F1（trace_audit 三处矛盾）/ F2（task 不进判定）/ F3（R3 谎报零调用 + 缺 http_status 误归因）/ F4（未知工具屏蔽违规）/ F5（空集 pass 计入掌握度）/ F6 / F7 前半 / F8 / F9 / F10 / Q5（假锚点 + note 污染锁）**均成立**；两处推翻我方简报（P-A 的 B 前提「无可判定观测字段」错——`artifacts` 缺失 vs `{}` 已区分 0.5 / 0.0；TD-015「evidence 完全相同」不精确——`observed: null` vs `{}` 已是区分器），已在 `peer-brief` 订正并留痕；Q1「5 条 FAILED」按字面变异实测 `23 passed / 0 FAILED`（5 条负例直接调 `build_report` 不经 `_build`）、F7「24→10 failed」依赖其自建副本标「假设」；新登记 TD-016~021；新增 **P-E 范围拍板**（建议 S3-3-6 只做机械切换，新修复另开 S3-3-7 系列）**。**§23：S3-3-6 落地 —— P-A~P-E 全 A、五条全现算、TD-007~011/015/021 销项、13 组变异全真 FAILED、评审包两遍重生成；F2 随机械切换消解，S3-3-7 序列调整为 F1 → F3 → F4/F5 → F9；TD-016/018/019/020 未修**。**§24：S3-3-7 第 1 步（round-22）—— F1/TD-016 三处矛盾本地只读复现（`call_count` / `final_status` / `schema_valid` 冲突均被 schema 放行），`tests/test_trace_pipeline.py` 追加三条缺口证据锁（断言现状、非应然，F1 落地时反转不删除），3 组真变异全 `FAILED` + 1 组负对照如实排除，`542 → 545 passed`、评审包两遍重生成 `6fd7f20c2b81d1f9` → `7d11a3f77272e95c`；**产品源码 / 夹具 / DTO schema 一个字节未改**；Q1 契约定位 / Q2 冲突处置 / Q3 最小加固待人拍板，F3 第二层仍单独挂 S3-3-7；按 §23.5 记档判据**备好可外发简报但不发起**。**§25：F1 外评回件核证（round-23）—— 高质量回件，A/B/C、P1–P9、五夹具派生一致性、§7 补丁副本验证（Step A `3 failed, 95 passed` / Step B `545 passed` + `--check OK` / Step C 九条全 `PipelineError`）逐项本地复现成立；订正我方测试注释残留失真、回件 §1.4 读取点盘点不全、回件 §3.3 R2「不涉及结构降级」与磁盘事实不符（实测 (b) 改写令 P1/P2 `0.5→1.0`、P3 `0.5→0.0`/DQ）；新登记 TD-022/023/024；**判定语义仍未改**，待人对 P-F1-1~6 拍板；本轮不再提外评**。**§26：round-24 F1 / TD-016 修复落地（2026-11-09）—— 人拍板 ② 派生物 + R1 严格拒绝 + `recompute_all` + 补 A3/A4 两条 allOf + 不进平台更严规则；`_derive_trace_audit` / `_refuse_audit_conflict` 落地，三条缺口锁反转成正向锁、函数不删除，新增 28 条回归锁（P1–P9 / 五夹具派生一致性 / P-B 护栏 / 兼容入口旁路 / 原因码 / 新 allOf）；Step A `3 failed, 95 passed` → Step B `545 passed` → 最终 `573 passed`；13 组真变异全 `FAILED` + 1 组负对照，逐组 `RESTORED: True`；M11 首轮锚点因 `diagnosis-report.schema.json` 是 CRLF 失配、改单行锚点后重跑全绿（如实留痕）；评审包两遍重生成 `26aac73a0be9a09a` → `263da1985856752e`，`--check OK`；TD-016 / TD-023 销项，TD-022（选 R1 不触发）/ TD-024（兼容入口旁路 + 派生器重复实现）保持；P8 拒绝点由 schema 前移到入口；五夹具分数序不变；工作区实测 35 项（初稿误写 36，已按磁盘订正）**。**§27：round-25 F3 / TD-018 第一步（2026-11-09）—— 人对 §26 验收后开 F3，按 P-E 序列只做「本地复现 + 7 条现状证据锁 + 备可外发决策简报」，判定语义一字未改；六场景全链路复现（A1 参数幻觉被 R3 谎报 case:no_tool_calls 且 R4 同时抓到幻觉、A2 只有 write_report 仍报零调用 ratio=0.75、A3 投影丢 null 改写 offsets 序列、B1/B2 除 observed.http_statuses 外报告逐字相同）；新增发现：F1 派生的 audit 只查结构不查条件必填，F3 第二层若落地须同时决定派生器是否产出 status_error_without_http_status，否则重新引入自相矛盾；该原因码在 traceInvalidReason 枚举内但无任何生产者（行为级锁）；6 组真变异全 FAILED + 1 组负对照 PASSED，逐组 RESTORED=True；M4 首轮 ANCHOR_COUNT=2（R3/R4 同行）收窄到四行唯一块后重跑全绿；局部 133 passed、全量过渡态 1 failed, 579 passed → 580 passed；评审包两遍重生成 036aed6fb07fafdb → 0154eccb1392de85，--check OK；按 §4.3 已记档判据提外评（F3 归因分界属本地裁判覆盖不到的契约应然），简报已备好但未发送、发起权在人；Q1–Q6 待拍板，F4/F5 未开工；工作区实测 36 项**。**§28：round-26 F3 外评回件核证（2026-11-10）—— 人把同侪回件转回，按核证→登记→处置→落仓→人拍板处理，判定语义一字未改；附录 A 13 场景 + 附录 B 分数副作用逐项本地复现成立；隔离副本逐字应用回件补丁（5 锚点全唯一、7→15 锁）实测 588 passed / 15 passed / --check OK (cb0fe31598d7403c) / 五夹具不变；M1 9 / M2 5 / M3 1 failed，逐组 RESTORED=True、FINAL_RESTORED=True；回件 §5.6 指纹写的是 PASS1 过渡态 499dfd5d80edcdd1，PASS2 实际 cb0fe31598d7403c（已订正）；首次副本全量 pytest 被环境 shim SystemExit 拦截、拆独立进程补跑 588 passed（如实留痕）；我方四处订正：Q1(c) 被 C2 反例、Q2(a)+(c) 被 C1/C5 反例、Q5(a) 被附录 B 0.75→0.5 推翻，§27.3「必须同步派生器」被 P-B 先例 + executor-semantics §4 推翻；新登记 TD-025~029；本轮不再提外评（第二真相源已到位、副本覆盖实现面）；D1–D6 + P-F3-1 待人拍板；工作区仍 36 项**。**§31：round-29（2026-11-10）—— 人确认 P2 并授权按建议推进；`docs/data-model.md:3` 历史昵称清理后宽口径 36 文件 0 命中 CLEAN；F4/F5 只读复现六场景（F4-S2 out_of_range 被屏蔽、F5-S2 TD-028 倒置、新证据 A0 零调用也拿 0.75）+ 7 条现状证据锁（`test_f4_td019a_*` / `test_f5_td019b_*`）；`src/` 零改动；全量 596 passed、评审包两遍重生成 `421b64b3f5d4fa7e` → `af7120667a528568`、`--check OK`；「什么算通过」决策包（F4-A/B、F5-A/B，我方建议 F4-B+F5-B 拆两步）与是否发小外评待人拍板**。**§32：F4/F5 外评回件核证（round-31）—— 11 项事实主张全成立 0 推翻；唯一错误在我方简报（五夹具表 raw-no-file/raw-no-trace 两行 ratio 互换，已订正）；隔离副本逐字应用 Step1/Step2 两段 diff：598 passed / `0d115102a94fc4cf`、602 passed / `d20372f75f885bbb`、五夹具不变、M1/M5 变异 DETECTED（M5 含 F3 A2 锁，证明 F5 不可拆 A→B）；D-F4F5-1~7 决策包待人拍板**。**§33：round-32 Step1/Step2 落地原仓（2026-11-10）**—— 人回「按照你的想法来」＝ D-F4F5-1~7 全收；Step1(F4)→Step2(F5) 小步落地，每步应用→局部→全量→两遍重生成；最终 602 passed / 13 passed / `d20372f75f885bbb` / 五夹具不变；TD-028 验收 A0=A1=A2=0.5；4 组变异（M1/M5/M6/M7）全 DETECTED + RESTORED=True；TD-019a/019b/028/032 销项，Q4(d) 单开后续，F9 / D5/D6 未动**。**§34：round-33 F9 / TD-020 只读复现与决策包（2026-11-10）—— `src/` 零改动；九场景复现（S1 gate 层内重复产 6 条断言/两个 R1/validate PASS/by_state.pass=6；S3 拼错 depends_on 抛裸 KeyError；S5 `agent_id.required` 无检查 ratio=1.0）+ 两处新证据（S2b 同步改 total_weight 后 credit 层同样穿透＝两层都漏；S3b 非 blocked 路径完全静默＝缺口在加载期引用完整性）；8 条现状/边界锁 `-k f9_td020` 8 passed；全量 610 passed、评审包 `97f30c2a0935755b`；D-F9-1~4 决策包待人拍板（建议 ①②=两层都查 + 加载期引用校验、合一步不动分数；③ `agent_id` 契约应然暂不拍；拆两步落地）；PASS1/PASS2 指纹相同的触发点未核清，标假设并已做机制核证（旧包下生成器如实记 2 failed）**。**§35：round-35 F9 外评回件核证（2026-11-11）—— 11 项事实主张成立 / 0 推翻 / 1 未核证 / 1 行号偏移；三处推翻我方简报表述（§2.1「全链路」实为 `build_report()` 直连、§2.2 `uniqueItems` 碰巧抓 S1、Q1(a) 先撞 F1 audit 对账）已订正且匿名仍 CLEAN；回件找到仓内契约依据（`executor-semantics.md` 冲突规则 + §4 范围表不含 `agent_id`）＝本轮第二真相源；隔离副本逐字应用 Step1/Step2b：613 / 614 passed、局部 11 / 12 passed、评审包 `c2cdfa53faff1a88` / `9fe9db7a2ecf881a`、五夹具不变，6 组字节级变异全真 FAILED + RESTORED=True；**方法论发现（已核证）**：`cp -a` 带入 `__pycache__` 会让副本首次全量误绿，建副本必须先清 pyc；PASS1=PASS2 触发点仍标假设；新登记 TD-033（死声明+key/shape）、TD-034（scoring 策略字段未执行）、TD-035（低优先一致性）；Q1 是否选 (b) 删声明 + 身份合并、Step1 六项收口范围、拆两步、TD-033/034 排期均**待人拍板**；**原仓 `src/` 一字未动、TD-020 未销、39 项未提交**；第二真相源已到位，本轮不再提外评**。**§36：round-36 F9 / TD-020 Step1 落地原仓（2026-11-11）**—— 人回「这次按照你的想法来」＝ D-F9-1/2/4 按建议收；只落地 ①+② 六项加载期校验（不改分数），6 条现状锁反转 + 3 条新增 → `-k f9_td020` **11 passed**；全量 610 → **613 passed**、评审包 `97f30c2a0935755b` → **`c2cdfa53faff1a88`**、五夹具不变；5 组字节级变异 3/2/1/1/1 全真 FAILED + RESTORED=True；不加 `uniqueItems` / Step1b（判据见 §36.1）；**Q1 未碰**（YAML `agent_id: {required: true}` 仍在），Step2b 不构成补丁；TD-020 **部分收口、维持未销**，TD-033 建议销项待人确认，TD-034/035 维持；证据链异常（`git apply --check` 失败但磁盘已落地、写入触发点未核清）与「基线 610/8 曾被误判为 pyc 假象后订正」如实留痕；§34/§35 悬案：过渡态记录机制已由受控实验核证（`2 failed, 611 passed` / `f82891b02578f0b1`），PASS1 触发点仍标假设**。**§37：round-37 Q1(b) 依据到位、F9 / TD-020 Step2 落地（2026-11-11）**—— 人确认「只将会话发给评测 Agent、无仓外消费者、无代码只读权限」＝ Q1(b) 反证前提消除；逐字应用 Step2b（4 文件、不含 `src/`、不改分数），5 文件与副本 `cmp exit=0`；我方订正回件草图「round-34 落地」失真注释 1 行；613 → **614 passed**、局部 **12 passed**、评审包 **`9fe9db7a2ecf881a`**、五夹具不变；M6/M7 两组变异各 `1 FAILED` + RESTORED=True；**TD-020 销项**，TD-033/035 建议销项待人确认，TD-034 维持待排期；过渡态未报红的触发点仍标假设**。**§38：round-38 TD-034 只读复现与口径决策包（2026-11-11）—— 人回「按你的想法来」＝授权按 §37.6(2) 单开一步先定口径；**判定代码 / YAML / 测试一字未动、不加证据锁**（口径未定，现状锁下一步必反转）；**订正两处文档失真**：§36.5/§37.5 把 TD-035 串号成「两个 agent_id 不一致」（原始登记实为 `tool_calls.required=false` 未读 + 后置依赖顺序，且 §35.6 明写该现象不单开 TD）、TD-033 漏计第四子项（条目 shape），连带 §36.6/§37.6「建议销项」作废；四项反例 T1/T2（rubric 条目非 dict → 裸 TypeError）、T3（required=false 仍报缺字段）、T4（R2 依赖后置 R3 全程通过）证明 **TD-033 部分收口维持未销、TD-035 维持未销**；TD-034 13 组内存探针分三类：8 组完全未执行（formula / gate 四字段 / credit 三字段）、`total_weight` 仅对账、`attribution` 有消费者不算本 TD；硬约束 `review_packet._task_struct()` 强依赖 5 个字段，删字段会破坏生成器；口径三案 (A) 文档性声明 /(B) 真执行 /(C) 加载期对账，**我方建议 (C)+只锁 5 组+TD-033/035 各单开**，D-TD034-1~3 待人拍板；基线 614 passed / 12 passed / `9fe9db7a2ecf881a`，39 项未提交；本轮不提外评**。**§39：round-39 第三方回件核证（消费者驱动契约 / 死字段，2026-11-11）**—— 人转来非评测渠道的另一个 Agent 意见（主张「不要删 agent_id 字段 / 不要删 required，先把隐性消费者显性化」）；按核证→登记→处置→落仓→人拍板处理，**不改代码、不回滚、不改契约**。回件针对的 Q1 已在 round-37 拍板 (b) 销项，回件不知此处置；**TD-034 (C) 落地停在只读探查、未写文件，本轮继续暂停**。逐条核证 10 项：成立/部分成立 5、前提失真或不成立 4、命令不可用 1，**0 项推翻 Q1(b)**。决定性事实：把 required 加回去造「旧版」，四夹具全链路 `score/trace_audit/断言 state/attribution_policy` **全等**，缺 trace.agent_id 时新旧版 `_trace_audit_issues()` 都返回 `[]` ⇒ 声明从来无执行者、删它零行为影响；**字段本身没删**（5 份 fixture 的 `submission.agent_id` + `trace.agent_id` 都在，DTO schema `submission.agent_id` 仍 required）；YAML `:116-118` **已补消费方注释**（回件核心建议已满足）；`git blame` → `876e5ba2`「字段风格统一为对象式（评审第 6 条）」，是格式统一而非身份契约决议；计划书 v0.1~v0.4 与现役评审包 `agent_id` **0 命中**。**采纳的部分**：补齐三层消费者清单（L1 代码无 / L2 上报方仓内无实现且仓外由人确认不存在 / L3 未来意图非契约 / 契约层＝DTO submission.agent_id），如实登记我方 round-35/37 只核代码消费者、未做清单化与考古的过程缺口；**指出其循环论证**：「文档命中⇒有消费者」会让 TD-034 那 8 组死字段也全不能删，与 YAGNI 自相矛盾，讨论记录≠契约承诺。待人拍板：是否降级 `required: false`（建议不采纳）、清单是否写进契约文档（建议只留 §39）、**(C) 是否继续**（人已拍板、被本回件打断、代码未动）。本轮不提外评**。**§40：round-40 TD-034 (C) 加载期对账落地（2026-11-11）**—— 人回「按照你的建议来」＝ 继续 (C)、只锁 5 组、TD-033/035 各单开、不采纳 `required: false` 降级、三层清单只留 §39；无仓外任务 YAML 源已确认。`src/arena/diagnosis_report.py` 字节级插入 4 常量 + `_policy_value_matches()` + `_check_scoring_policy()` 并在 `rubric_layers()` 末尾调用（CRLF 保留），`tests/test_diagnosis_report.py` 新增 9 条锁；**不改分数、不动 YAML、不动 builder 判定逻辑**（判定行仍在 `:279/:289/:296`）。614 → **623 passed**、`-k td034` **9 passed**、`-k f9_td020` 12 passed、五夹具不变、PASS1=PASS2 **`16ab1fadac170efe`**；6 组字节级变异 M1 7 / M2 1 / M3 2 / M4 1 / M5 1 / M6 2 FAILED，逐组 RESTORED=True、FINAL_RESTORED=True、ALL_OK=True；匿名 0 命中 CLEAN。**TD-034 建议销项待人确认**；TD-033 部分收口、TD-035 维持未销，**新登记候选 TD-036**（`_task_struct()` 直读 YAML 不经对账）。**40.6 四条边界**：(C) 对账的是「声明 vs 常量」不是「builder 逻辑 vs 常量」；`_task_struct()` 不经对账；锁死配置自由度；说明字段故意不锁已由正向锁钉住。§34 悬案：旧计数包下全量 `2 failed, 621 passed` + 恢复后 `--check OK` 再次证明机制有效，源码排除「测试重写默认包」，但 PASS1/首次全量触发点**仍标假设**。工作区 **40 项**（+1 是首次改动 tracked 的 `tests/test_diagnosis_report.py`），HEAD `03cf8c9`，未 commit / 未 push；待人拍板见 §40.8；本轮不提外评**。**§41：round-41 TD-036 立项并落地**——`review_packet._task_struct()` 复用 `_check_scoring_policy()`，漂移转 `PacketError` 拒绝生成，9 条 `td036` 锁含同源锁；632 passed / `9d443bd3641e99c4`；匿名扫描口径两处订正留痕**。**§42：round-42 TD-033 第 4 子项落地**——`_check_rubric_item_shapes()` 公共函数 + 6 条 `td033_shape` 锁，T1/T2 裸 `TypeError` 改可读 `ReportError`；638 passed / `00894ca2d34edb79`；4 组字节级变异全真 FAILED；**新观察（实证）**：`--check` 不覆盖源码实现文本（禁用校验仍 OK / RC=0），登记候选 TD-037 待人立项；本轮不提外评**。**§43：round-43 TD-035 ① 落地**——三态内存复现证明 `tool_calls.required` 无执行者，消费者盘点 0 读取点；按 Q1(b) / §39.6 既有拍板口径删该死声明 + 补注释，新增 3 条 `td035` 锁；三案取舍（A 删 / B 真执行 / C 加载期对账）与回退成本已记；**过渡态首次按预期真红**（计数锁 1 failed, 640 passed，§34 悬案仍不编原因）；641 passed / `a2646bbd35144673`；4 组字节级变异 2/1/2/1 FAILED；TD-033 / TD-036 按人验收授权销项，TD-035 ② 下一轮单开；本轮不提外评** |
| round-44 | TD-035 ② **只读核证与决策包**（§44） | 8 组内存探针复现 T4/T4b：后置依赖在报告层 `ratio=1.0`、执行器层 `blocked/dependency_blocked`，**两层结论相反**（比原登记「无可读错误」更重）；找到两处未登记的契约依据（`diagnosis-report-schema.md:41`「先 gate 后 credit 与执行顺序一致」、`trace_pipeline.py:205`「gate 在前，R1 一定先于 R2 算出」）；§35.7 / §36.6 把「是否加 Step1b 执行顺序检查」列为**待人拍板项**，§36.1 只裁「独立排期、不塞进本步」，从未批准添加 ⇒ 本轮**不落地代码**，出 A（加载期硬约束）/ B（只澄清口径）/ C（静态防线）三案与反证条件；`src/`/`tests/`/YAML/评审包一字未动，指纹仍 `a2646bbd35144673`；TD-035 ② 维持未销；本轮不提外评** |
| round-45 | 对 §44.5 回件的核证（§45） | 人指定先跑 round-43 四条复核，**四条全中**（641 passed / 3 passed,167 deselected / 0/0/0.25/0.5/1.0 / `OK (a2646bbd35144673)`）；A 的契约前提、C 不单独采用、① 销项证据链、037 低优先单开均核证成立；**提交策略「按 round-43/44 分两批 commit」有反例**：40 项＝16 tracked + 23 untracked + 1 rename，跨 1~44 轮累积（`review-response-v1.md` 4185 行纯新增），同文件跨轮无法按轮次拆分；commit 不会让 `--check` 变 STALE（按 snapshot-sha 重放），但源码明写「不要为对齐 HEAD 重生成」，且会丢失 44 轮刻意保持的 HEAD 基线 ⇒ 动机成立、手段不成立，本轮**零 git 写操作**；另收窄 A 的措辞（只能挡住配置入口，非两层同口径）并补错误优先级 / 反向依赖 / 不重复查环三处边界；TD 状态一律不变，待人一句「按 A + 执行器不引入拓扑排序」；本轮不提外评** |
| round-46 | TD-035 ② **案 A 落地**：`depends_on` 后置依赖加载期硬约束（§46） | 按人「按照你的想法来」授权推进 A：新增公共 `_check_dependency_order(gate, credit)`，全局 `gate+credit` 1-based index 比较，后置即可读 `ReportError`（点名双方 id / 序号）；调用放在 Step1 之后保证错误优先级；11 条 `td035_order` 锁覆盖现役 `R2→R1` 与跨层 `R2→R5` 合法、T4/T4b 后置拒绝、gate→credit 反向拒绝、拼错优先、每个 dep 都查、不改分数；环检查刻意不重复实现（`-k acyclic` 仍 1 passed）；契约注记写进 `diagnosis-report-schema.md`（**当前可核证事实 + 显式契约变更点**，不冒充人已承诺永不拓扑排序）；全量 **652 passed**、五夹具不变、两遍 PASS1=PASS2 **`7974ca6f877d3e6a`**、`--check OK`、正文独立复算 MATCH=True；5 组 CRLF 字节级变异 6/6/7/7/2 FAILED 逐组恢复 + 计数锁受控实验真红 + 说明字段负向对照不误伤；§34 悬案第七次「过渡态直绿」仍标假设；**TD-035 ① 销项、② 已落地待人验收、整体未销、TD-037 立项低优先**；本轮零 git 写操作、不提外评** |
| round-47 | ② 验收的**证据边界核证**：`--check` 到底覆盖什么（§47） | 按人「按你的建议来」推进，但 §46.8 路线第一步「② 由人验收」不代做：**不代验收 / 不代销项 / 不 commit / 不插队 TD-037**，只把 ② 证据链里 `--check OK (7974ca6f877d3e6a)` 的证明力用当前版本实测钉死。4 组字节级变异（逐组 RESTORED=True、FINAL_RESTORED=True、PACKET_UNCHANGED=True）：**M-B**（合法配置被误拒、夹具脚本退出码非 0）→ `FAIL / RC=1`；**M-A 禁用 round-46 的 A / M-C 放过后置依赖 / M-D 纯注释字节改动** → 全部**仍 `OK`**。结论细化 §42.8：`_snapshot()` 不含源码哈希，但 `build()` 现场跑夹具 ⇒ **只覆盖会改变现算量的源码改动，不覆盖源码实现文本本身**；故 `--check` 绿不能证明 A 的实现仍在，该项由 `-k td035_order` / 5 组变异 / `git diff` 覆盖。TD-037 两案可判定判据已给（A 纳入源码哈希：注释也 STALE、破坏锚点设计；B 只写清口径：零代码、依赖本地裁判），**我方倾向 B、归人拍板**。产品代码 / 测试 / YAML / 包正文一字未改，源码 sha 仍 `a448d3d41938076e`；零 git 写操作、本轮不提外评** |
| round-48 | ② **验收通过**、TD-035 整体销项与提交授权（§48） | 人明示「已通过，可以提交」：按 §46.8 **TD-035 整体销项**（① 已销 + ② 本轮验收通过）；验收**不把 `--check OK` 当「A 实现仍在」的证据**（§47.4 实证禁用 A 后照样绿），改以 11 锁 / 5 组变异 / 652 passed / 五夹具 / 源码 sha `a448d3d41938076e` / `git diff` 为准。**TD-037 仍待人拍 A/B、本轮不实现**。提交按主题分 2 批、**只 commit 不 push**、commit 后不重生成评审包；提交前只读核查：40 项、index 遗留 `R100` staged rename、`core.autocrlf=true` 但 CRLF 受控实验证明不影响指纹、禁词命中仅为 `FORBIDDEN` 词表与内部记录历史正文（外发 0 命中）、凭据 0 命中、绝对路径仅在内部记录。**匿名风险留痕**：内部记录含历史昵称与本机绝对路径，按 §1428 照现状提交；push 到公开仓库前须先清理，归人拍板。本轮不提外评** |
| round-49 | push 前匿名清理、TD-037 拍板 B 与推送授权（§49） | 人回「1：现在清理并Push 2：方案B」。**TD-037 拍 B 并销项**：只把「`--check` 只覆盖生成器现算量、不覆盖源码实现文本」写成契约（§49.4 + §47.4），不实现 A、`--check` 零代码改动；反证条件（源码被改但本地裁判恰好没跑、包仍 OK）触发则复开改拍 A。**匿名清理**：处置记录昵称 5 处（§48.3 记 2 处，口径差订正）→「维护者」/「历史昵称」，本机绝对路径 64 处 → `$env:USERPROFILE\`（命令仍可直接粘贴运行），`spike` 运行示例 1 处 → 通用命令，`FORBIDDEN` 词表 5 处 → Unicode 转义且运行期元组逐字相等（表示形式改写，不属 TD-037 范围）；凭据与外发文件 0 命中。**历史边界**：`03cf8c9` 已含旧文本、普通 push 不会消除，是否重写公共历史归人单独授权。两个未推送提交用 `git reset --mixed` 重写后按主题分两批，fast-forward push，不重生成评审包、不 `push -f`。本轮不提外评** |
| round-50 | 已推送历史边界订正、可见性核实与「闸门前置」纪律（§50） | 人明示「目前没有任何克隆方，且以后要避免这种问题」。**事实订正**：§49.3「仅基线含旧文本」低估 —— 逐提交扫描 23 个提交 / 102 唯一 blob，**21 个提交**含旧文本（`c830b1b`~`1096045`），彻底清需重写 **22 个**；当前 HEAD 树 CLEAN（54 blob 0 命中）。首版扫描脚本 meta 段数判断错误曾报假 CLEAN，作废重跑、未据以下结论。**重写代价实证**：临时把 `snapshot-sha` 换成不可达 sha → `--check FAIL / RC=1`（`git rev-parse` exit=128），恢复后 OK、字节哈希不变 ⇒ 重写会抹掉锚点、评审包在他人 clone 里无法重放；GitHub Docs 明确 force push 后旧 commit 仍可经 SHA-1 缓存视图 / PR / fork 访问。**可见性**：匿名 API 404 + owner API 200 + 人提供登录态 `Private` 徽标截图 ⇒ 当前私有**已独立核实**（§49.3「标为假设」按 §1428 不回改、以 §50.3 订正）；历史公开性 / fork 状态仍属假设。**处置**：维持不重写，改用 push 前**全历史闸门**（扫 `origin/<branch>..HEAD` 每个提交的整树 blob + diff 新增行 + commit message，0 命中才 `PUSH_GATE=CLEAN`；旧脏提交反证须 `BLOCKED`、当前 HEAD 须 `CLEAN`），已写入维护者侧长期规则；仓内脚本化属后续小步。本轮只追加 §50 + 本索引 / 状态块 / §2 证据标注，不改生成器 / 模板 / 评审包 / 测试；推送结果不写回仓内正文。本轮不提外评** |
| round-51 | push 闸门**仓内脚本化**与 `verify.bat` 接入（§51） | 人回「继续按照你的原有想法走」⇒ 按小步纪律只做 §50.4 后续小步，**不启动 D5/D6**（D6 牵动 TD-024 与 B1 `0.75→0.5` 分数变化，须单独拆步）。新增 `scripts/push_gate.py`：默认扫 `<upstream>..HEAD` 每个提交的整树 blob + diff 新增行 + commit message；空范围报 `NO_COMMITS` **不假绿**；禁词取自 `review_packet.py` 的 `FORBIDDEN`（单一真相源）、路径/用户名由环境推导、凭据前缀用相邻字面量，**防闸门自我命中**。`verify.bat` 新增 `[5b]` 并把 `GATE_EXIT` 纳入硬判定。**硬约束**：`_pytest_count()` 使 `tests/` 不能加用例（否则 `--check` STALE），裁判改用 `--self-test` + 一次性命令。实测：`SELF_TEST_PASS=True` / `c830b1b` → `BLOCKED`(2 命中) / `HEAD` → `CLEAN` / `c830b1b^..HEAD` → 22 提交 65 命中 `BLOCKED`（证明逐提交扫）/ 独立外部自扫 CLEAN / **652 passed** / `--check OK (7974ca6f877d3e6a)` / 评审包字节 sha 未变 / 匿名 CLEAN / PowerShell 下六条复核命令原样跑通。`verify.bat` 整体因末尾 `pause` **仍未实跑**（只实跑 `[5b]` 等价命令）；一条 Bash→`cmd.exe` 诊断命令被权限策略拦下、未绕路重试。**零 git 写操作**，HEAD/origin 均 `efa9ff6`，提交/push 归人拍板。本轮不提外评 |
| round-59 | 六条拍板落仓、批次 A 决策与文档层收口（§52） | 人回 6 条裁断并授权「马上做出决定、尽快进行下一步」。**Q4(d) 移入 A 线但交付物是决策包、只提前决策时点，§51.7 实现顺序不动**；新增 `docs/decision-packet-q4d.md`。**批次 A 决定**：6 条一次落仓，零产品代码 / 零新增测试 / 不动 `_snapshot()` 覆盖量（加测试必让 `--check` STALE，与第 6 条预期冲突）。落仓：附 B `:270` 补「不计掌握度」半句（分数语境）、YAML `:126` note 改述并标 TD-026、`executor-semantics.md` §1 补条件必填层归属（**格 2 层错位：改述 / 移位置、不删、不补结构层**，D2 仅执行器层）、schema `traceInvalidReason` 加 reserved description（enum 不变）。**分歧留痕**：round-58 我方主张格 3 被否，按人拍板格 2 执行，但如实写明执行器按「失败步 spec 声明字段」取输入、不按 `status=error` 做全量条件必填。批次化放行契约 §52.4：每步 `--collect-only` 计数增量 + **node id 集合 diff** + 语义 pytest 不后移 + 改快照量的步骤不并入批次。改前核证 `_task_struct()` 不读 note、`build()` 不含 schema ⇒ 无需重生成评审包。改后实测：JSON / YAML `RC=0`、`test_task_yaml.py` 23 passed、F3 锁 16 passed、`--check OK (7974ca6f877d3e6a)`、包字节 sha `839eb4b66a954ba7` 未变。本轮不提外评 |
| `docs/redteam-brief-round8.md` | **已过期的历史快照**（Q10-A 后内联代码 / 用例集失真，§9 指纹不再匹配）：逐字内联两个执行器 + 任务 YAML / 词表 / 12 个边界 case，含 4 条攻击命题与基线输出。**仅供 round-8 留档，勿当现役契约**；新一轮红队须重新生成。不含绝对路径与真名 |
| `docs/peer-brief-s3-3-7-f9.md` | **历史外发包（round-34 备好、round-35 回件核证、round-36/37 两步落地完毕，勿再按「回件未到」理解）**：F9 / TD-020「配置声明未执行算不算 bug」的判据选择。九场景实测表（含 S2b 两层都漏、S3b 非 blocked 静默）+ Q1–Q5 可判定命题（Q1=③ 契约应然、Q2=② 引用完整性归属、Q3=① 查重范围与 `uniqueItems`、Q4=拆两步、Q5=盲区波及面）+ 我方倾向（逐条标假设与反证条件，Q1 暂不拍）+ **§2.2 三处易读错口径**（两层 `agent_id` 不是同一字段 / 静态环检查只覆盖磁盘 YAML / `assertions` 只有 `minItems:1`）+ 相对路径只读清单 + 回件格式。**匿名收紧**：`src/arena/review_packet.py`（含禁词字面量）、`spike/`（含本机绝对路径）、`docs/review-response-v1.md`（内部记录）明确排除，并写明只给文件级只读、不给整个仓库目录。新文件匿名 0 命中 CLEAN，只读清单 21 路径全部存在；新增 docs 不影响 `--check`（仍 `97f30c2a0935755b`）。**round-38 订正**：F9 修复已于 round-36/37 落地、TD-020 已销，D-F9-1~4 全部闭合；原文「F9 修复代码未动，D-F9-1~4 仍待人拍板」是 round-34 历史快照，勿再据此给建议。**round-35 订正（2026-11-11）**：回件已收到并逐项本地核证，本文件头部加订正块，改 §2.1（「全链路」→ `build_report()` 直连 + chain 三道护栏）、§2.2 第 3 点（`uniqueItems` 碰巧抓 S1）、§3 Q1(a)（先撞 F1 audit 对账）；三处只改暴露面与后果描述，不改结论与待拍板项；订正后匿名仍 0 命中 CLEAN。§3/§4 为 round-34 历史快照，现行决策见 §35 / §36 / §37；**round-37 提示**：Q1 已拍板 (b)、Step2 已落地，F9 / TD-020 全部收口；本包 §2 九场景实测表是**修复前快照**，Q1–Q5 均为历史快照，勿按「待拍板」给建议 |
| `docs/peer-brief-s3-3-7-f4-f5.md` | **历史决策留档（round-30 发出、round-31 回件核证、round-32 落地完毕，勿再按「待拍板」给建议）**：F4/F5 的「什么算通过」判据选择决策输入。六场景实测表（含 A0 新证据）+ Q1–Q5 可判定命题 + 我方倾向（逐条标假设与反证条件）+ 相对路径只读清单。回件见维护者侧 `review-s3-3-7-f4-f5.md`（不外发），核证与落地记录见 `review-response-v1.md` §32/§33；§2.2 两行夹具 ratio 互换已在 round-31 订正 |
| `docs/peer-brief-s3-3-7-f3.md` | **历史决策留档（round-25 起，round-28 已落地完毕，勿再按「待拍板」给建议）**：F3 / TD-018 的归因分界契约决策输入。六场景实测表 + Q1–Q6 可判定命题 + 我方倾向（逐条标假设与反证条件）+ §2.2 跨模块张力 + 相对路径只读清单；**只写相对路径、不写本机绝对地址、不写死体量**。按 §4.3 判据提出外评，**尚未发送给任何外部 AI**，发起权在人；明确不给内部处置记录 / 过期包 / `.git/` / `.redteam-tmp/` / 凭据。完整五组只读路径仍见 `docs/peer-brief-s3-3-6.md` §4.2。**round-26 已收到同侪回件并逐项核证，Q1/Q2/Q5 我方旧倾向被反例推翻；文件头部已加状态订正，§3/§4「待拍板」均为历史快照。**round-28：D1–D6 + P-F3-1 已拍板并两步落地完毕（§29/§30），P1/P2 均已落地、F3 判定层闭合；§1–§4 全为历史快照，勿再据此提建议** |
| `docs/peer-brief-s3-3-7-f1.md` | **历史决策留档（round-24 已拍板落地，勿再按「待拍板」给建议）**：原为 round-22 的可外发单文件简报，给同侪 AI 判断 F1／TD-016 的**契约定位**问题。§0 对话开头指令、§1 现状与三处矛盾实测表（`call_count=999` / `final_status` 反向 / `call_index=9`）、§2 Q1–Q5 可判定命题、§3 我方倾向（明确标「假设」+ 反证条件）、§4 边界与相对路径只读清单。不内联源码、不含本机绝对路径 / 昵称 / 校名 / 凭据，匿名 CLEAN。**只备包、未外发**；发起权在人，是否升级 `trace_audit` 为第二真相源是新契约决策，AI 不替决。完整五组只读路径仍见 `docs/peer-brief-s3-3-6.md` §4.2。**round-24 已拍板 ② + R1 并落地，文件头部已加状态订正；§1.3 现状、§1.4 缺口锁、§2「待拍板」均为历史快照** |
| `docs/verify-brief-s3-3-6.md` | **已过期的历史快照**（S3-3-6 落地后内联的 15 个源文件已变；其 §6「包内块 vs 磁盘字节」自检现在必然 `MISMATCH`，属设计使然，勿据此判定仓库被篡改；下轮外发前须重新生成）。原为 2026-11-09 的现役外评委托包，自包含单文件。§0 任务书（只评**尚未落地**的方案、不翻已拍板项）、§2 决策摘录（§12.5/§12.7 逐字，其余剪裁，标「不作证据」）、§3 自报基线 + 只读模拟表、§4 逐字内联 **15 个现役文件**（任务 YAML / DTO schema / schema 说明 / 执行器语义 / 词表 / 三个产品源文件 / 两个测试文件 / 五份夹具）、§5 T1–T12 中性命题、§6 指纹表 + 用户侧自检、§7 边界与匿名。**修掉 §15.6 模板缺陷**：自检同时比对「包内块 vs 指纹表」与「包内块 vs 磁盘字节」；生成器 15 块双 OK、`SELFCHECK: PASS`，包内印刷命令单独抽出实跑也 `PASS / EXIT 0`；一字节变异两处均 `DETECTED`、`RESTORED: True`；匿名 `CLEAN`。生成器 `.redteam-tmp/gen_verify_brief_s3_3_6.py` 已 gitignore。原纪律「只外发这一份文件，不给整个仓库 / 过期包 / 绝对路径日志 / 凭据」仍适用于其作为现役包的时期；现在按 §23.6 标为过期 |
| `docs/peer-brief-s3-3-6.md` | **历史简报 + 现役只读路径清单**：§0–§3 是 round-19/20 的状态与想法（**round-21 后 S3-3-6 已落地、四项口径已拍板，勿再按「待拍板」给建议**），当前唯一现役用途是 **§4.2 的精确只读路径清单**（判定链 / 契约 / 夹具 / 判据 / 治理件五组，全部仓库相对路径、只给读权限）+ §4.3 的启用判据。可外发单文件（round-20 已订正三处事实错误并留痕，见 §22；体量不写死，避免编辑后自指失真）。§0 可直接作对话开头的指令（交付格式 / 可判定命题 / 不翻已拍板项）、§1 只列关键状态（治理结构、当前真值、五个执行器、四项已拍板、只读模拟结论、上轮回件教训、**四项待拍板 P-A~P-D**）、§2 我方判断（四项倾向全选 A、六步落地路线、**「只读 + 统一 diff 文本不落盘」优于写权限的四条理由**、Q1–Q6 可咬命题）、§3 事实 / 假设 / 待拍板分界、§4 附件与索取清单。**不重复内联代码**（现役委托包已做），只给导航；匿名 `CLEAN`，新增 docs 不影响 `--check`。本机绝对地址含用户名，按匿名纪律**不写进任何外发文档**，由维护者在本机侧填写；是否给权限、何时启用由人按 §4.3 判据定，AI 不替决 |
| `docs/verify-brief-s3-3-1.md` | **已过期的历史快照，勿当现役契约**：§4 内联的是 round-12 源码，round-13 后 4 个磁盘源文件已变；包内 8 块仍逐字自洽（`SELFCHECK: PASS`），但**自检不与磁盘比对**，通过不等于现役一致（模板缺陷，见 `review-response-v1.md` §15.6）。原内容：§0 任务书、§2/§3 自述与基线（标「不作证据」）、§4 内联 8 个源文件、§5 T1–T8、§6 指纹表。生成器 `.redteam-tmp/gen_verify_brief.py` 已 gitignore。回件核证见 §14；下轮外发前重生成，一并修 §0.2 写死前提、§2 过期指纹、自检缺磁盘比对。**含混合行尾，只能字节级编辑**。不含绝对路径与真名 |
| `.redteam-tmp/test_redteam_round8.py` | 红队回件的**隔离运行副本**（已 gitignore）。`pytest .redteam-tmp/test_redteam_round8.py -q` → 87 passed；原路径跑是 `5 passed/82 skipped` 假绿。含恒真断言与第二真相源，**暂不纳入 `tests/`**，理由见 `docs/review-response-v1.md` §11 |
| `review.bat` | 一键重生评审包 + 自检；**改完 manual 后先跑它，再复制 `docs/review-packet.md`** |
| `verify.bat` | 一键五段验证（单测 / A2E 哨兵 / DTO 夹具 **+ 全链路双跑** / 评审包一致性 / git 状态）。第 3 段现在同批夹具跑两遍，结果必须一致；第 4 段 `--check` **不重跑测试** |
| `spike/README.md` | **A2E 承载能力判定报告**：判据、三级证据、26 指标映射表、对项目的三条影响 |
| `docs/archive/review-packet-v1.md` | 已归档的 v1 手写评审包（数字已过期，仅作历史与 V1–V8 可验证点留档） |
| `README.md` | 仓库结构与匿名纪律声明 |

> **round-29~46 状态补充（详见 `docs/review-response-v1.md` §31–§46）**：
> ① 昵称已清；② F4/F5 外评已核证并落地，TD-019a / 019b / 028 / 032 销项
> （A0=A1=A2=0.5）；③ **F9 / TD-020 两步已全部落地**、TD-020 销项；
> ④ round-40 TD-034 (C) 加载期对账落地，TD-034 于 round-41 销项；
> ⑤ round-41 TD-036 立项并落地（生成器与执行器同源）；
> ⑥ round-42 TD-033 第 4 子项落地（`_check_rubric_item_shapes()` + 6 条锁）；
> ⑦ **round-43 TD-035 ① 落地**：三态内存复现证明 `tool_calls.required` 无执行者，
> 按 Q1(b) / §39.6 既有拍板口径**删该死声明 + 补注释**，新增 3 条 `td035` 锁；
> **源码 0 改动、判定语义 / 分数零变化**；
> ⑧ **round-44 TD-035 ② 只读核证与决策包（本轮未落地代码）**：8 组内存探针复现
> T4/T4b，坐实「报告层 `ratio=1.0` vs 执行器层 `blocked/dependency_blocked`」
> **两层结论相反**（比 §38.3 原登记更重，已就地订正留痕）；找到两处契约依据
> （`diagnosis-report-schema.md:41`、`trace_pipeline.py:205`）+ §6.2 决策原文。
> **不落地的硬依据**：§35.7 / §36.6 把「是否加 Step1b 执行顺序检查」列为待人拍板项，
> §36.1 只裁「独立排期、不塞进本步」，从未批准添加；加校验会收紧合法配置集合，
> 与 ① 的「行为零变化」不同型。给出 A（加载期硬约束，我方倾向）/ B（只澄清口径）/
> C（静态防线）三案 + 反证条件 + 十分钟验证（§44.4 / §44.8）。
> **round-46 TD-035 ② 案 A 落地**（§46）：新增 `_check_dependency_order()`，
> 全局 `gate+credit` index 比较，后置依赖在加载期拒绝；11 条 `td035_order` 锁；
> 契约注记把「数组顺序 = 执行顺序」写成**当前可核证事实 + 显式契约变更点**
> （改拓扑排序须同步反转校验与锁），**不冒充人已承诺未来**；执行了 §45.4 的
> 「配套四件事 + 三处补充」。**不改 YAML / 判定逻辑 / 分数**。
> 当前裁判：全量 **652 passed**、`-k td035_order` **11 passed, 38 deselected**、
> `td033_shape or td034` **15 passed**、`td036` **9 passed**、
> `f9_td020 or td035` **15 passed**、`-k acyclic` **1 passed, 22 deselected**、
> 五夹具 `0/0/0.25/0.5/1.0`、两遍重生成 PASS1=PASS2 **`7974ca6f877d3e6a`**、
> `--check OK`、包内计数 652、正文指纹独立复算 MATCH=True；
> 5 组字节级变异 6/6/7/7/2 FAILED，逐组 RESTORED=True、FINAL_RESTORED=True、ALL_OK=True；
> 计数锁受控实验 `1 failed` 真红（机制有效）；编译 / 行尾 / 匿名 CLEAN。
> 工作区 **40 项**，全部未 commit / 未 push，HEAD `03cf8c9`。
> **TD 状态**：**TD-035 ① 销项**（已落地、行为零变化、死声明已删）；
> **TD-035 ② 已落地、维持未销待人验收**（契约变更不代销）；**TD-035 整体未销**；
> **TD-037 已立项、低优先、不插队**（排在 ② 验收之后）；TD-033 / TD-036 已销不变。
> **§34 悬案**：本轮过渡态**第七次直绿**（652 passed 无 failed），与 round-43 首次真红
> 并列；受控实验再次证明计数锁机制有效，**触发点仍无证据，维持假设，不编原因**。
> **A 的授权边界**：人本轮未逐字给出 §45.5 那句承诺，AI 不代人保证「执行器永不拓扑排序」，
> 只落当前事实 + 可判定契约变更点；若理解有误，回退成本 2 代码/测试文件 + 3 文档，
> 一句话可改走 B。
> 下一步：② 验收 → TD-035 整体销项 → TD-037 小步 → D5/D6 → Q4(d)；
> builder 引用常量建议暂不做。**提交策略**：零 git 写操作；A 已落地，
> 「等 A 落地后整理」条件已到，若提交须按主题分批且遵守
> `review_packet.py:417-420`「commit 后不为对齐 HEAD 重生成」，提交与否仍归人拍板。
> **本轮不提外评**（依据全在仓内；产品意向由人拍板，外评替代不了）。
> **round-47 ② 验收证据边界核证（§47，零产品代码改动）**：4 组字节级变异实证
> `--check` 的边界比 §42.8 更细——M-B（改变夹具现算量）报 `FAIL / RC=1`，
> 而 **M-A 禁用 round-46 的 A** / M-C / M-D 仍 `OK (7974ca6f877d3e6a)`。
> 即「只覆盖生成器现算的量，不覆盖源码实现文本本身」；**`--check OK` 不能证明 A 的实现仍在**，
> 验收时该项须看 `-k td035_order` / 5 组变异 / `git diff`。
> TD-037 两案判据已给、我方倾向 B（只写清口径），**归人拍板、本轮不插队**；
> 十分钟验证（§47.6，PS 单引号 here-string、自动恢复）与 §47.8 复核命令均已原样跑通。
> **TD 状态一律不变**：② 已落地、维持未销待人验收，整体未销；零 git 写操作；本轮不提外评。
> **最新状态（round-48，覆盖上文「待人验收」表述）**：人明示「已通过」⇒
> **② 验收通过、TD-035 整体销项**（① 已销 + ② 验收通过，证据链见 §48.1）。
> §46.8、状态块及 round-44/45/46/47 索引行里「② 维持未销待人验收」均为**历史快照**，
> 勿再据此判断现行状态。**TD-037 已立项、低优先，A/B 仍待人拍板、本轮不实现**；
> 下一步：TD-037 拍板 → 小步 → D5/D6 → Q4(d)。
> 提交：人「可以提交」⇒ 按主题分 2 批、只 commit 不 push；提交前只读核查与匿名风险
> 留痕见 §48.3；**commit 后不为对齐 HEAD 重生成评审包**（`review_packet.py:417-420`）。
> **最新状态（round-49，覆盖上文「A/B 仍待人拍板」「只 commit 不 push」表述）**：
> 人回「方案 B」⇒ **TD-037 拍 B 并销项**（只写清口径、不实现 A、`--check` 零代码改动，
> 契约口径见 §49.4，反证条件触发则复开改拍 A）；后续顺序 D5/D6 → Q4(d)。
> 人回「现在清理并 Push」⇒ 按 §49.2 清理昵称 / 本机绝对路径 / 词表字面量，
> 用 `git reset --mixed 03cf8c9` 重写两个**未推送**提交后分两批重新提交并 fast-forward push；
> **不重生成评审包、不改基线、不 `push -f`**。已推送历史 `03cf8c9` 里的旧文本不因本次
> push 消除，是否重写公共历史归人单独授权（§49.3）。
> **最新状态（round-50，覆盖上文「可见性标为假设」「仅基线含旧文本」表述）**：
> 人明示「目前没有任何克隆方」⇒ 维持**不重写已推送历史**（21 个提交含旧文本、
> 彻底清需重写 22 个；重写会抹掉评审包锚点使其无法重放，官方口径亦明确
> force push 擦不干净缓存视图）。人提供登录态 `Private` 徽标截图 ⇒
> **当前可见性已核实为私有**，但历史公开性 / fork 状态仍属假设（§50.3）。
> 人「以后要避免这种问题」⇒ 判据升级为 **push 前全历史闸门**：
> 扫 `origin/<branch>..HEAD` 每个提交的整树 blob + diff 新增行 + commit message，
> 0 命中才 `PUSH_GATE=CLEAN`；已写入维护者侧长期协作规则（§50.4）。
> 本轮按人「落仓、尽量 push」授权追加 §50 并 fast-forward 推送；**不重生成评审包**。
> **最新状态（round-51，覆盖 §50.4「脚本化属后续小步、本轮未落地」表述）**：
> push 闸门已仓内脚本化（`scripts/push_gate.py`）并接入 `verify.bat` 第 `[5b]` 段**硬判定**；
> 正式放行判据仍只认默认 `origin/<branch>..HEAD`（§51.3），空范围 `NO_COMMITS` ≠ `CLEAN`，
> `--range` 仅作诊断入口、不是安全边界。闸门只覆盖**待推送**提交，21 个已推送提交里的
> 旧文本仍在，「不重写历史」结论不变。本轮**零 git 写操作**、未提交未 push；
> 后续 D5/D6 → Q4(d) 不变，开工前先出只读决策包（§51.7）。
> **最新状态（round-59，§52，补充上文读法而非覆盖）**：人对 round-57/58 的 6 项裁断落仓。
> ① **Q4(d) 移入 A 线，但交付物是决策包不是代码**：新增 `docs/decision-packet-q4d.md`；
> 只提前**决策时点**，**§51.7「D5/D6 → Q4(d)」实现顺序一字未改**。
> ② 附 B `:270` 属分数语境 ⇒ 补「不计掌握度」半句，不改写为 `unscored`。
> ③ `http_status` 按**格 2 层错位**处置：改述 / 移位置、不删、**不补结构层**（D2 仅执行器层）；
> `error` / `result_summary` 保留声明 + note 内标 TD-026，不新开 TD。
> ④ 批次化接受，放行契约见 §52.4：每步 `--collect-only` 计数增量 + **node id 集合 diff**
> + 语义 pytest 不后移 + 改快照量的步骤不并入批次。
> ⑤ schema 枚举加 reserved `description`，**enum 数组不变**；包不嵌 schema ⇒ `--check` 仍 `OK`。
> 本轮为 **批次 A：决策与文档层收口**，零产品代码 / 零新增测试 / 零判定语义改动；
> **D5/D6 实现属下一批次**。实测：652 passed / `--check OK (7974ca6f877d3e6a)` /
> 包 sha `839eb4b66a954ba7` 未变 / 闸门自检 PASS / 匿名 0 命中。
> 提交 / push 归人拍板；本轮不提外评。
> **最新状态（round-61，§53，补充上文读法而非覆盖）**：人给出 Q4(d) 新口径，
> **取代 `docs/decision-packet-q4d.md` §3 的 (a)/(b)/(c) 三选一**——按「这题要不要过程」把题目分两类，
> 门槛只加在明确要求过程的题上；缺步骤＝流程不完整（不打分、不记缺点、可留中性提醒）；
> 零工具调用仍按「归因不明」，不冤枉人。核证见 §53.2：三条现成支持（决策 7 /
> `on_unscored=do_not_disqualify` / `target_calls==0 → unscored`），**两条是仓内没有的新东西**
> （题目分类维度 0 命中；现行「过程证据」全绑 `trace_*` 工具记录，与「步骤 ≠ 工具调用」冲突）。
> 本轮只落决策记录，零代码 / 零测试 / 零判定语义改动；实现顺序 `D5/D6 → Q4(d)` 不变。
> **最新状态（round-64，§54，补充上文读法而非覆盖）**：人授权 AI 直接决策、不再过目 ⇒ **D5 / TD-025 销项**。
> R3 分类 probe 改对象投影（`tasks/dbg-001-order-report.yaml:245`）+ 执行器原位还原
> （`src/arena/assertion_evaluator.py:1319`）：缺 offset 的位置保留 `None`、序列不缩短；
> **不改谓词 / 不改判定 / 不改分数 / 不新增测试用例** ⇒ 评审包未重生成、锚点与历史指纹未动。
> 实测 652 passed、五夹具分数不变、`--check OK (7974ca6f877d3e6a)`、包 sha 未变、
> 闸门自检 PASS、变异 `3 failed` → 恢复 `16 passed`、匿名 0 命中。
> **纠错留痕**：round-63 把空格画在第 3 位，实测是第 2 位（§54.6）。
> 下一步 **D6**（TD-026 + TD-024，牵动 B1 分数变化，单独拆步）；Q4(d) 依 §53 顺序不插队。
> 本轮不提外评（判据见 §54.1），零 git 写操作，提交 / push 归人拍板。
> **订正注（round-66）**：本块「下一步 D6…牵动 B1 分数变化，单独拆步」按 §28.6 旧定义书写，已由 §55 / §56 取代；D6 实际只做 TD-024 零行为收敛，结构层暂缓、分数不变。

> **最新状态（round-65，§55，补充上文读法而非覆盖）**：核出 **D6 原始定义（§28.6）与 round-59 人裁断冲突**——
> 前者要求补结构层并接受 B1 `0.75→0.5`，后者明写「不要补结构层，那会推翻 D2」，且磁盘 YAML 注释、
> §53.4 分数硬锁、3 条 F3 核心测试都与后者一致。按「后拍板覆盖先拍板」把 D6 缩为
> **契约收口 + TD-024 实现债收敛（零判定语义变化）**：两入口防御合并为 `_check_raw_consistency()`；
> 两处重复规则合并为 `call_sequence_problems()` 单一内核，执行器与 F1 派生器只做输出格式映射。
> **不做**：结构层条件必填、`schema_valid=false`、B1 分数变化、第五态、`recompute_r3` 的 F1 旁路（下一小步）。
> 实测 652 passed、五夹具分数不变、`--check OK (7974ca6f877d3e6a)`、包 sha 未变、
> 计数 652 / 重复 node id 0、闸门 `SELF_TEST_PASS=True`、双侧变异同时报红（4 + 3 failed）、
> Step A 补做变异 2 failed、匿名 0 命中。
> **开一轮外评**（`peer-brief-round65-d6.md`，仓外，含可直接复制的白话提问）：判据是
> 「两次拍板优先级」属本地裁判覆盖不到的契约盲区；**不等回件、不阻塞本批次**。
> 下一小步：TD-024 剩余项（`recompute_r3` 的 F1 旁路收敛，须先复核 71 条历史锁与现状锁）。
> 本轮零 git 写操作，提交 / push 归人拍板。
> **订正注（round-66）**：「把 D6 缩为…」准确写法是「本轮只完成 **D6 的 TD-024 部分**（零行为收敛）」；TD-026 保持「已登记、未实现」，结构层是暂缓非取消。

> **最新状态（round-66，§56，补充上文读法而非覆盖）**：round-65 外评回件结论「有条件恰当」，
> 8 条事实逐条核证全部成立（三条测试行号偏移 +3，断言内容一致）；回件 §4 命令实跑 **3 passed**。
> **三个放行条件全部落地**：① §54.8 / HANDOFF round-64 块加订正注（历史正文不回改）；
> ② 范围明确登记为「D6 的 TD-024 部分，TD-026 暂缓非取消」；
> ③ 以 `git show 0ca8492:` 的**真实 git 对象**为基线做函数级穷举分支对拍 + 全链路对拍（§56.8）：
> `L1 30 检查 / L2 20 用例，0 差异，FINAL_MATCH=True`。可复跑对拍器已落 `scripts/equivalence_probe.py`（退出码 0/1）。
> **本轮新发现**：`docs/diagnosis-report-schema.md:94-95` 的「两处独立实现」也已过时，已改为新事实
> （回件把它当事实全文引用而未察觉，属双方共同盲点）。
> 本轮无产品口径待人拍板；零代码、零测试、零判定语义改动；提交 / push 归人拍板。
> **订正注（round-67）**：上句「提交 / push 归人拍板」已由 §57 取代——人已指示提交，累积改动推送为 `1a319ad`。

> **最新状态（round-67，§57，补充上文读法而非覆盖）**：人指示「提交，继续向前推进」。
> **① 提交与 push 已完成**：round-59~66 累积改动提交 `1a319ad`（11 文件），按规则 11 逐提交扫
> `origin/main..HEAD` 得 `PUSH_GATE=CLEAN`（1 提交 / 57 文件 / 903 新增行 / 0 命中），
> 负向对照 `BLOCKED(2 命中)`，`git ls-remote` 远端 = 本地 HEAD = `origin/main`。
> **② TD-024 剩余项收敛、TD-024 整体销项**：F1 派生对账抽成 `_reconcile_audit()` 单一实现，
> `recompute_all()` 与兼容入口 `recompute_r3()` 共用；后者新增可选 `task`（缺省口径与主链路一致），
> 71 条历史锁调用形式一字未改。兼容入口从「能组装矛盾报告」变为「矛盾即拒绝」＝**本轮唯一有意的判定语义变化**。
> **③ 测试**：现状锁反转为正向锁（函数名保留）、边界参数化锁预同步 audit；**零新增用例** ⇒ 评审包未重生成。
> 实测：652 passed / 170 passed / 15 passed、五夹具分数不变、`--check OK (7974ca6f877d3e6a)`、
> 包 sha `839eb4b66a954ba7` 未变、`FINAL_MATCH=True`（主链路零行为变化）、
> 变异 M1 `1 failed` / M2 `13 failed` 且恢复后全绿、闸门 `SELF_TEST_PASS=True`。
> **④ 如实留痕**：实现首轮 `66 failed` 系抽函数时误删 `requirements` 定义导致 `NameError`，已修复并记入 §57.8；
> §57.5 原判据「两条锁同时报红」经实测不成立，已按事实订正。
> **⑤ 不提外评**：依据全在仓内 + 一次性内存探针 + git 真实对象，无跨模块口径盲区。
> 下一步：Q4(d) 两类题分流实现或 A 线设计层 P1/P2/P3（排期选择，归人定方向）。

> **最新状态（round-68，§58，人已选「先补 A 线」）**：本轮只做**不依赖人拍板**的部分——
> ① 联网核证计划书 §2.2 / §4.3 / §九 的文献、库、版本、状态主张，订正 3 处可核实事实错误
> （Anki 23.10 是「首次内置可选」非「成为默认」；Dunlosky 2013 的自我解释/精细询问是 **moderate** 非 high；
> OTel GenAI semconv 2026-11 仍为 **Development** 非已发布标准），并撤回 2 处查不到来源的定性
> （「KT 领域公认开放问题」「RFM 范式」）。参考清单补成卷页 + DOI + 项目号。
> ② **Q3 有答案**：两段式评分对应 Wheeler (1993) ERIC ED374160 的 **combined model** 与
> conjunctive/compensatory 框架；Q3 所列四个候选名里只有 `conjunctive scoring` 是规范术语，
> `gated rubric` / `two-stage assessment` 查无此名、`hurdle model` 有计量学歧义。
> 关键限定：门禁前置的正当性是**关键项不可补偿 + 降成本**，不是「更准」（Yocarini 2018；Ock & Oswald 2018）。
> ③ **Q4 有答案**：EWMA 用于掌握度平滑有直接文献（Pelánek UMAP 2017），但「衰减参数取 FSRS 的 S」
> **无直接文献且量纲不成立**（S 是天数、α 须 ∈(0,1)）；S 亦非半衰期、FSRS 是幂律而 EWMA 是指数。
> 保守替代＝用复习间隔 / elapsed days 或常数 α 作基线。**计划书不写该等号**。
> ④ **两处新发现**：计划书 §2.2 用外语词对（陈述性记忆）实验给可执行编程任务背书，
> 与 P1「不能把程序性技能当陈述性记忆」的批评自相矛盾 → 已降级为「机制启发，非直接实证」；
> Q4 结论**动摇 P1(b) 原话**（非推翻方向），其具体措辞需人拍板后改写。
> ⑤ **不替人拍板**：P1/P2/P3 仍挂待拍板（§10 第 3 节）、TD-002 未销、评审包 §5 与
> `review-packet-manual.md` 第 5 节一字未动、`data-model.md:121-122` 未动（§12.4 要求等人确认）。
> ⑥ **外评开一轮**（判据：文献对应/数学口径/契约措辞，本地裁判全覆盖不到）：
> `peer-brief-round68-a-line.md`（仓外，146 行，3 个可判定命题，匿名 0 命中）；
> 大白话决策卡 `a-line-plain-card.md`（仓外，99 行，含「不拍也能继续」出口与可整段复制的提问）。
> **不等回件、不因此停摆**；发起权与拍板权在人。
> ⑦ 实测：**652 passed**、`--check OK (7974ca6f877d3e6a)`、包 sha `839eb4b66a954ba7` 未变、
> 闸门 `SELF_TEST_PASS=True`、匿名 0 命中、计划书表格结构完好。
> **零代码 / 零测试 / 零判定语义 / 零分数改动；仓内仅 1 项 `M docs/review-response-v1.md`**，
> HEAD 仍 `2af6dfe`，**未 commit / 未 push**。下一步可做且不需拍板：给计划书补 gate→credit 的可判定论证、
> 核证 Q1/Q2/Q5/Q6 的本地可判定部分；Q4(d) 实现按 §51.7 不插队。
> （订正：上一行「仅 1 项」是**全量裁判那一刻**的快照；`HANDOFF.md` 的 round-68 块是在裁判之后追加的，
> 所以收尾时仓内实为 2 项。两处都对，按事实说明，不回改历史正文。）

> **最新状态（round-69，§59，A 线第二步）**：本轮把 §58.4 的文献结论写成计划书里的**可判定论证**，并用
> 仓内可重跑探针回答 Q1/Q2/Q5/Q6 里本地裁判覆盖得到的部分。
> ① **计划书 §4.2 新增第 7 条**：评分分两段的判据是「可补偿性」不是「重要性」；准入 gate 三条（不可补偿 /
> 有明确后果 / 不吞诊断）+ Wheeler (1993) combined model 对应 + 「正当性不来自更准」的限定。**并删掉
> 「顺序筛除降成本」这条依据**——实测本项目断言在算分前已全部跑完，门禁失败不跳过任何计算，该理由在本项目是空话。
> ② **Q2 有答案（不该把 R4 设门禁，三条独立理由，全部实测）**：探针 `scripts/q2_gate_probe.py` 10 条判据全 PASS。
> 理由是「门禁不许带权重（W1 直接 REJECTED）」+「分母 4→3 让 `raw-no-trace` 从 0.50 涨到 0.667」+
> 「**Q2 提示的前提已不成立**：`assertion_evaluator.py:1255` 已有『全称命题不能对空集平凡为真』防护，
> `zero_calls` / `write_only_skip_fetch` 都判 `unscored`，而门禁 `on_unscored` 不拦 ⇒ 设成门禁在最该拦的场景失效」。
> ③ **Q1 有答案**：`raw-no-file` 实测 `ratio=0.0` 与 `credit_pass_weight=2` 并列 + 一条 notes 解释；字段自解释但
> 数字层面会被二次汇总误读。给两个可本地验证的改法（P：改名 `counterfactual_credit`；R：搬到 `diagnostics`），
> **不拍 = 零改动**。
> ④ **Q5 有答案**：(c) 「写死答案跳过工具调用」的真实成本是 **0.5 分 + 无诊断报告**，且报告里没有字段记录
> 「结果全对却零过程证据」。给三条判定标准：D1（`R2 pass` 且 `call_count==0` ⇒ 不计分，**与 P2(b) 冲突，须人拍板**）、
> D2（R2 数值须能由 `result_summary` 复算，即 P3 的 R6）、D3（`write_report.content` 与工具返回值对不上 ⇒ suspicious）。
> ⑤ **Q6 只给证据强度表，不代排序**：4/5/6/7 有可现场跑的夹具证据（最像「一眼就信」），第 3 条（复习项=可执行任务）
> 零实现零数据（最像「一眼就疑」）；第 2 条证据最强但要索取 `spike/README.md` 才看得见。
> ⑥ **新发现 TD-038**：`task_git_hash` 在 schema 里 required + pattern + 描述「没有它断言 id 无法对齐」，
> 但 `grep src/ tests/` **0 命中**——声明无执行者，与 TD-026 同款。它比 R4 更符合 gate 定义（版本不符时分数语义无效），
> 但「判不合格 vs 拒绝出报告」属产品口径，**本轮只登记不实现**。
> ⑦ 实测：**652 passed (116.21s)**、`--check OK (7974ca6f877d3e6a)`、包 sha `839eb4b66a954ba7` **未变**、
> 探针 `VERDICT=PASS checks=10 failed=0`、五夹具分数序 `0/0.25/0.5/1.0` 未变。
> **零产品代码 / 零测试 / 零判定语义 / 零分数改动**；新增只读探针 `scripts/q2_gate_probe.py`。
> 仓内改动 = `M HANDOFF.md` + `M docs/review-response-v1.md` + `?? scripts/q2_gate_probe.py`，
> `.pytest-tmp-r69/` 用 `.git/info/exclude` 本地排除（不动 `.gitignore`）。
> **待人拍板**：P1/P2/P3、P1(b) 措辞、Q1 呈现候选、Q5 的 D1、TD-038 处置。**不拍 = 维持现状 = 零改动。**

> **最新状态（round-70，§60，人拍板后全部落地）**：上一轮挂着的六项**全部关闭**，评审包 §5「待拍板清单」由 3 条降为 **0 条**。
> ① **P1**：采纳外评「掌握度与调度分开记」，但**修正其「衰减取 S」这一步**（S 是天数、系数须在 0–1，量纲不成立），
> 改为「衰减沿用复习间隔这一个时间轴」——既满足外评「只保留一个时间常数」，又不写不成立的等号。`data-model.md` §5.2 原口径作废。
> ② **P2 / Q5-D1**：人拍板「按题目要不要步骤分类」。任务级开关 `submission_requirements.requires_process`，
> dbg-001 设 true；缺过程 ⇒ 不计能力分、不计掌握度、记 `process_fact`、提示补交。**断言状态不改写**——
> 第一版改写导致 14 条测试报红，根因是它把「做对了却没过门槛」的证据一起吞掉（与决策 5 冲突）。
> ③ **P3**：保对账，落 `src/arena/reconciliation.py`（verified / unverifiable / mismatch 三态，出处只认返回值不认 params），
> 7 条测试。**刻意不接进 rubric**（会改 credit 分母 4），登记 TD-039。
> ④ **Q1**：`credit_pass_weight` → `counterfactual_credit`（+`x-previous-name`），CLI / 正则 / 表头同步；现役代码与测试 0 残留。
> ⑤ **TD-038**：取人拍板的第三种——`score.voided` + `void_reason=task_version_mismatch`，报告照出、分数不算、`report_attribution` 全 false、提示重交；
> **与 `disqualified` 不合并**（一个是「这次不算数」，一个是「没交东西」）。缺省不比对是已知宽松口，M1 收紧。
> ⑥ **五夹具分数变更（硬锁被改，已显式登记 §60.7）**：`raw-no-trace` **0.50 → 0**，其余四份不变，新序 `0/0/0.25/0/1.0`。
> 原因是决策 9，不是回归；断言仍判 pass（`counterfactual_credit=2`）。原「单调不减」判据改为「两道门槛 + 正确性排序 + 上下界锚点」。
> ⑦ 实测：**667 passed**、`--check OK (2a776f8871975833)`、评审包重生成、匿名守卫与闸门待收尾复核。
> 待办：TD-039（P3 接入 rubric）、TD-038 宽松口收紧。

> **最新状态（round-71，§61，计划书第二轮硬事实核证）**：方法从「搜到存在」升级为「打开原始页面，指出支持这句话的那一句」。**4 处需改，1 处是方向性引用错误。**
> ① **Ock & Oswald (2018) 引错方向**：原文结论是「补偿式在多数条件下期望效标表现与总体效用更高」，给多重门槛的正当理由是**施测成本**——而成本这条本项目已判定不成立。
> 旧稿把它和 Yocarini 并列成「两者都表明 conjunctive 降低假阳性」，实际那是 Yocarini 的结论。已拆成三条各自归位，并加一句不辩的立场：不主张更准，换的是失败模式的取舍。
> ② **A2E license = MIT**（LICENSE 文件，Copyright (c) 2026 Shanghai AI Lab）：三处「待核」一次性关闭，§八 风险表**删掉一条真实风险**（「license 不明则弃用」作废）。附 B 保留当日判断原样 + 追记关闭。
> ③ **Pelánek 三条**：两条标题/年正确但缺页码与 DOI，已补；「2014 EDM 时间衰减」那条缺篇名，实为 *Application of Time Decay Functions and the Elo System in Student Modeling*, EDM 2014:21-27。
> 顺带给 §4.2 第 8 条划边界：**EMA 做掌握判据有直接文献（NRHM 2018），但「只拿复习间隔当唯一时间轴」是本项目选择、不是文献结论**。
> ④ **PinchBench** 旧稿「三重评分」「Git 版本锁定」两处措辞按官网原文改述（实际是 `automated | llm_judge | hybrid` 三种评分方式）+ 删除无来源的「版本锁定」；**千问 AI Arena** 补上「机器打榜 + 专家盲审」——它和本项目的双评+仲裁是同一思路的工业先例。
> ⑤ **未核项如实列出**（§61.6）：ClawBench 原始页、CubeSandbox 60ms、agentevals v0.9.5/166 star 均为时点数据，未复核。
> ⑥ 判据实跑值：`Ock` 2 行 / `license` 残留 1 行（附 B 历史句 + 追记）/ `三重评分` 1 行（改述说明句）/ 四条著录各自命中。
> **本节初稿把判据写成「4/0/0/齐全」是凭印象预填，实跑后订正为「2/1/1/齐全」，已在 §61.7 留痕。**
> 仓内零代码/零测试改动，评审包 `--check` 仍 OK；待人拍板项：**无**。

> **最新状态（round-72，§62，决策演示台）**：把第 3 节 11 条决策逐条翻成**现场可执行的动作**（`scripts/demo_decision.py`），
> 结果 **PASS 7 / PARTIAL 3 / NOT_EXECUTABLE 1 / FAIL 0**——后两档明确不算通过，单列不并入 PASS。
> ① 三条自我约束：标题从评审包**现读**（不复制正文）、期望值从任务 YAML **现读**（读不到就 raise，不许默认 0）、
> `PARTIAL`/`NOT_EXECUTABLE` 不许混进"全过"。
> ② **自证不是永绿**：5 条变异测试定向破坏实现（反事实得分抹成 0 / 缺过程永不成立 / 作废恒 false /
> `unscored` 计入掌握度 / 给门禁加权重），要求对应演示**必须变红**，`tests/test_demo_decision.py` 21 passed。
> ③ 过程中抓到自己的两个错：**决策 4 读错了 YAML 路径**（`scoring.credit` vs 实际 `scoring.score.credit`）→ 读到 0，
> 5 条判据全红，修法是「读不到就 raise」；**决策 3 用 `review` 正则把 `review_packet.py`（评审包）误判成复习层** →
> 假 FAIL，改判据为无歧义词（fsrs/schedul/spaced/card）+ 内容扫描。中英撞名（评审 vs 复习）是真实陷阱。
> ④ **顺带修掉评审包一处对外事实错误**：包内长期写着 `1 failed, 666 passed`（round-70 最后一次生成时确实有 1 条红，
> 之后修好但没重生成）→ 现为 `688 passed / 688`。**`--check` 只重放记录、不重跑测试，抓不到"记录本身过期"**，
> 这是机制缺口，登记 TD-040。
> ⑤ 评审包新增 §7「每条决策怎么当场验」（含四档定义与变异测试命令），原复现环境改为 §8。
> 待人拍板项：**无**。

> **最新状态（round-73，§63，TD-040 对比试验 + 计划书第三轮核证）**：两条线一起做。
> ① **TD-040 先立案再比方案**。立案方式：`git worktree` 回到 `487ff6f`，跑**当时那一版** `--check`
> → **OK（RC=0）**，而那个包内实测行写着 `1 failed`。**带失败记录的包自检是绿的**，这是立案证据。
> ② 对比试验（`scripts/td040_compare.py`，3 真实样本 + 1 构造，每个样本附"可发布/不可发布"的理由）：
> **A 生成门 4/4**（误报 0 漏报 0）；**B 陈旧度 N=1 只 1/4、N=2/3 只 2/4**。B 的问题不是阈值没调对，
> 而是它测的（提交数）与测试红绿无因果关系——N=1 误报正常发布的包，N≥2 放过真正该拦的。**B 否掉。**
> ③ A 也拦不住「生成后才变红」（它的输入只有包内文本），补 **C：`--check --full`** 重跑 pytest 比对记录。
> 落地：`_test_gate` 单一内核、非全绿时**警告写进正文**（日志会滚掉、文件会外发）、返回码
> `0/1/2/3`（2 不可发布，**判序 2 先于 1**，否则把人引去重生成而重生成出来仍是坏包）。
> ④ **`--full` 第一次跑就报 TEST_DRIFT，是假红**：我一边改测试一边让后台跑，子进程读到编辑到一半的文件。
> 使用约束已写进 manual——**`--full` 必须在工作区静止时跑**。
> ⑤ 顺带修掉一条老用例的耦合：`test_td036_check_fails_on_policy_drift` 原来读 `DEFAULT_OUT`，
> 默认包一变 `--fast` 它就从"测策略漂移"退化成"测包状态"，还与生成流程互相卡住 → 改为自建可发布包。
> ⑥ **计划书第三轮核证又抓到一处方向性错误**：`ClawBench（沙箱 + 多重评分）` 与原文不符——
> `claw-bench.com` 的 V2 环境是**真实网站 + 浏览器扩展、不是沙箱**，评分是单一 Agentic Evaluator；
> 真正可借鉴的是**五层记录 + 内置 trace 查看器**。切口因此更硬：「记录只回放给人看，不回灌学习决策」。
> 另：该名称下**至少四个不同项目**（clawbench.com / claw-bench.com / clawbench.club / clawsbench.com），
> 现在只引 `claw-bench.com` 并注明只引官网自述。agentevals 版本 `v0.9.5 → v0.12.1（2026-11-10）`、
> CubeSandbox「60ms」→ 官方**平均** <60ms（50 并发 avg 67 / P95 90 / P99 137）。
> ⑦ 裁判：**700 passed**；`--check` → 0（`d6f253a6e8fbe6ba`）；`--check --full` → **OK_FULL**
> （包内 0 failed/700，重跑 0 failed/700）。
> 待人拍板项：**无**。剩余待办：TD-039（对账接入 rubric，会改分母）、TD-038 版本必填（M1 接 API 时）。

> **最新状态（round-74，§64，骨架盘点与未完成台账）**：前 73 轮都在答"下一步做什么"，
> 这一轮补上"还差多少"。**新增两份导航文档**：`docs/ROADMAP.md`（五层 × 四档状态：有肉 / 半有肉 / 纸面 / 空，
> 加三层证据、里程碑对齐、4 条风险）与 `docs/BACKLOG.md`（24 条未完成项，每条带「为什么不做 / 做完判据 /
> 验证动作 / 谁推」，末尾附收工清单与已关闭留痕）。两份都**不定义口径**，只导航。
> **盘点发现三处，比文档本身重要**：
> ① 五层里只有评测层有肉——**一次提交内的能力基本齐全，跨提交的能力一件都没有**（分界线是持久化）；
> 端与云 `grep -rl "fastapi\|flask\|sqlite" src/` 全空。
> ② `mapped_skill_ids` 有声明、有测试校验、**`src/` 0 个消费者**，而它正是计划书标为"自研核心"的
> 「错误模式 → 技能缺口映射」；报告里的 `skill_gaps` 走的是另一条路（断言自带 `skill_id`，
> `diagnosis_report.py:449-466`），两条路不等价（映射表能一对多）⇒ 已登记 BACKLOG B-06 / ROADMAP R-2。
> ③ `data-model.md` §0 原则 2「掌握度即 Card 的 D/S/R」与 §5.2（round-70 拍板）直接冲突，
> round-70 的 TD-002 三处对齐漏了 §0 ⇒ 已订正并留痕，另补一句「P 尚无落字段，不得当成已实现」。
> 裁判：**700 passed**；`--check` → 0（`d6f253a6e8fbe6ba` 未变，因三份文档都不在 `_snapshot()` 内）；
> `--check --full` → **OK_FULL**；闸门 `SELF_TEST_PASS=True`；演示台 `PASS 7 / PARTIAL 3 / NOT_EXECUTABLE 1 / FAIL 0`。
> 待人拍板项：**无**（B-22 的口径边界需人拍，但不是本轮新产生的）。

> **最新状态（round-75，§75，M1 持久化落地）**：人回「按照你的想法来」⇒ 按 BACKLOG 开 B-01
> （M1 硬闸门，卡住 M2/M3/M5）。新增 `src/arena/store.py`：7 张表 + 2 索引、外键真开启、建库幂等、
> 三种种子幂等导入、落库/读回/列表。四条设计决定各有理由（`user_id` 不从 `agent_id` 推；
> `detected_patterns` 是派生列；重复 id 抛错不覆盖；`created_at` 锚 `evaluated_at` 而非落库时刻）。
> **两个过程事件已留痕**：① 第一版「只认 fail」的测试是**假锁**——夹具里 `unscored` 的 `error_patterns`
> 已被上游清空，"过滤"与"不过滤"结果相同，变异 M2 报 MISSED；补一条构造锁（pass+blocked 各带模式）才咬住。
> ② 该变异第一次报 `1 error` 是变异串的 `\n` 被 heredoc 写成 `/n`（语法错误），**error ≠ 证据**，
> 判定函数已改成区分 `DETECTED` / `INVALID` / `MISSED`。
> **建表后两条演示判据被列名顶红，改的是判据不是代码**：决策 3 从"源码出现 fsrs 字样即复习层已实现"
> 改为判 `review_items` 的**形态**（kind 四值 + 引用 task/submission + 无卡片正反面），档位
> `NOT_EXECUTABLE → PARTIAL`（有形态、无读写）；决策 8 从"出现 stability 字样即算"收窄为只抓**算式**
> （赋给 alpha/decay 或参与乘除）。三组变异（kind 掺 flashcard / 删 task_id 引用 / 写 `alpha=card.stability*0.1`）
> 证明新判据仍咬得住，rc 均为 1。
> 裁判：**717 passed / 717**；`--check` → 0（**`32bf34c5c2450d60`**）；`--check --full` → **OK_FULL**；
> store 变异 8 组 + 演示变异 3 组全 `DETECTED`、`RESTORED: True`。
> **已知代价**：`test_test_count_matches_repo` 与生成流程自指互锁——第一次生成必记 `1 failed`（RC=2），
> 第二次才收敛；已登记 BACKLOG C-03，收工清单注明"要生成两次"。
> 待人拍板项：**无**。下一步 B-02（黑盒提交 HTTP）。

> **最新状态（round-76，§76，M1 黑盒提交服务落地）**：人回「继续吧」⇒ 按 round-75 结尾承诺做 B-02，
> 并把 B-21（`task_version` 必填）同批收紧。新增 `src/arena/submission_api.py`（标准库 `http.server`，
> **零新依赖**）：3 端点、请求归一化、错误码表、端点 TCP 探测、每请求新建 sqlite 连接。
> **判据订正一处（必须记住）**：B-02 原文要求「端点不可达返回 `blocked`」，**实测不可行**——
> `blocked` 的语义是「前置断言已失败、无法判定」，`_assertion_item():266-270` 强制要求存在真失败的前置，
> 而端点不可达时一条断言都没失败过。`tool_no_response` / `invoke_timeout` 因此在原文口径下是
> **不可达组合**（写进枚举、无任何生产者，且写了也被 builder 拒）。改用 `unscored` + `tool_no_response`
> （与 `artifact_invalid` 同类：平台采集故障），分数贡献同为 0、同样不计掌握度，只改原因归属。
> `docs/diagnosis-report-schema.md` §6 已订正并留痕。
> **架构边界**：`assemble_raw()` 只允许外部给 `submission` / `trace` / `artifacts`；
> `trace_audit` 与 `assertion_results`（除 id 壳）由平台派生——这是「黑盒」一词的实际含义。
> **等价性对拍是本项最硬的锁**：五夹具「HTTP 入口 vs 内部链路」逐字段相等。第一次五份全红，
> 根因不是口径分歧而是**夹具比客户端旧**（写在 TD-038 之前，缺 `task_version`）；对照组补上后全等。
> 裁判：**741 passed / 741**（717 + 24）；`--check` → 0（**`da575b9ab7d18a12`**）；
> `--check --full` → **OK_FULL**（包内 0 failed/741，重跑 0 failed/741）；9 组变异全 `DETECTED`、`RESTORED: True`。
> **C-03 本轮没触发**（一次收敛），但机制没改，仍挂着——别当成已修。
> 待人拍板项：**无**。下一步 B-03（真 trace 采集）或 B-04（judge 双评）。

> **最新状态（round-80，§80，B-07 掌握度 P —— 公式落地，参数仍是假设）**：人回「好的，先做 B-07」。
> `submissions` 表里躺着的历史第一次有代码读它：新增 `src/arena/mastery.py`（452 行）+ `store.user_reports()`。
> **先定 `c_k` 的取法**：不是整份 `score.ratio`，而是按断言 `skill_id` 分组的**权重加权通过率**——
> 否则一份提交压到三个技能时，R2 答错会把 `tool_param_validation` 一起拉低（串味）。
> 决策 7 落成代码：`unscored`/`blocked` **从分子分母一起剔除**，该技能无计入断言时 P **原地不动**。
> 时间轴按**解析后的时刻**排（字符串比较在混合偏移下会判反，而数仍然单调、看不出来）。
> 实测（`python scripts/mastery_walk.py`）：三提交 `1.0000 → 0.7000 → 0.7900` 与手算逐位一致；
> `--skill failure_retry` 时第三次变「无证据 ⇒ 原地不动」。**36 条测试 + 13 组变异全 `DETECTED`**。
> **本轮最值钱的是一条不利发现**：`raw-no-file`（没交产物、`ratio=0`、`disqualified=True`）
> 仍把 `failure_retry` / `tool_param_validation` 的 c 抬到 1.0——因为「门禁只管分数，不管观测」。
> 即**"不交产物但重试规范"能刷高 P**。没擅自改口径：写成现状锁 + 写进 `data-model` §5.2 + 登记 B-07b。
> **B-07 关闭但只算不落库、不衰减**：`user_skill_states` 仍无 P 字段（B-07c），
> 衰减依赖复习间隔（B-08），λ=0.3 与「首次观测播种」都是**假设**（B-07b）。
> `test_decay_is_not_implemented_yet` 是现状锁，B-08 落地时必须**反转**它而不是删掉。
> 待人拍板项：**0 条阻塞**（"门禁失败要不要压低 P"已登记，不拍也能继续）。下一步 B-08 或 B-05。

> **上一状态（round-79，§79，B-04 judge 双评机制 + 17 次真调用标定 —— 做了一半，不标完成）**：
> 密钥到位后人拍板「先用 flash，资金有限」⇒ 开工。**先立三条「不」**（已落 `docs/data-model.md` §5.1 注）：
> judge 不进 `ratio`、不进 `detected_patterns`（字段名就叫 `suspected_patterns`）、不驱动 `fsrs_rating`。
> 它只回答一个问题：换一个观察者重看同一份提交，能不能得出同样的结论。
> 新增 `src/arena/llm_judge.py`（481 行：`JudgeVerdict` 构造即校验 / `double_judge` / `StubJudge` /
> `build_judge_context` 白名单 / `LLMJudge`）与 `src/arena/llm_client.py`（300 行传输层，零新依赖）。
> **82 条测试全离线、31 组变异全 `DETECTED`**（其中 4 组首跑漏网：`agreement` 属性从没被断言过、
> `match="至少"` 匹配到了错的报错来源、`field(repr=False)` 被手写 `__repr__` 覆盖成死代码）。
> **真跑 17 次调用 / 4 批**，数据在 `docs/judge-agreement-distribution.json`。最值钱的三条：
> ① `max_tokens=1024` 被思考吃光（输出里 **87% 是 reasoning token**）⇒ judge 默认值提到 4096；
> 这层报错若只写「choices 为空」，排查会一路走到网络上去。
> ② `raw-no-trace`（答案对、过程空）是唯一分歧样本，**耗时 12–20 秒 vs 其他 1.7–5.4 秒**——
> 耗时比一致率更早报警；只评 2 次会漏掉低概率漂移。
> ③ **我一度把偶发漂移判成系统性歧义**（"决策 7 在 judge 侧的空白位"，听上去很漂亮），
> 复测三次全给 3 档把它推翻。花那 3 次调用是这一轮最划算的开销。
> **B-04 标 `[~]` 不标 `[x]`**：未接进 `build_report`/`submission_api`、无仲裁队列、无阈值
> （`store.py` 的两列与 `save_submission` 参数早通，只是没人调用）⇒ 拆出 **B-04b** / **B-04c**（依赖 B-05）。
> 待重生成评审包（自指测试红，包内仍是 776，实测 858）。
> 待人拍板项：**无**。下一步 B-05（任务库 1→10，解锁 B-04c 标定）或 B-07（掌握度 P）。

> **上一状态（round-78，§78，B-06 映射表接进代码 —— 接上了，同时把「自研核心」自己拆了）**：人回「重来，先做 B-06」。
> 开工先解一处冲突：Q10=A 拍板过「不改 `build_report()` 按 `spec.skill_id` 的归因」，TD-001 未销项。
> 唯一能同时满足的实现是**兜底**——断言写了 `skill_id` 就只认它，词表一次都不查；没写才查，查不到直接报错。
> 新增 `load_pattern_skills()` / `_attribute()` / `_split_weight()`，`build_report()` 加关键字参数
> `pattern_skills=`；`skill_gaps[]` 增可选字段 `attributed_by`（`assertion` / `mapping` / `assertion+mapping`）；
> 新增不变量 **I12**：归因守恒（每条 `fail` 恰好落在 `skill_gaps` 或 `unattributed_patterns` 之一）
> 且 `Σ weight_lost ≤ total_weight` 有硬校验（防止 1 分的失败被记成 2 分）。
> **本轮最重要的一条是坏消息**：§64 写过的反证条件被自己触发了——本任务四条断言全写了 `skill_id`，
> 于是五份夹具的 `skill_gaps` 与改动前**逐例相同**，`attributed_by` 全是 `assertion`，映射表一次都没被查过。
> 按事先写好的处置：计划书 §4.1「映射表＝自研核心」**降级为「兜底归因 + 词表集中管理」**，
> 因为没有任何一例证明它比 `skill_id` 说了更多。要让映射表真参与，须先推翻 Q10=A 的边界 ⇒ 拆出 **B-06b**（需人拍板）。
> 裁判：**776 passed / 776**（770 + 6，`test_b06_*`）；`--check` → 0（**`bd82caa5f38a8742`**）；
> `--check --full` → **OK_FULL**（包内 0 failed/776，重跑 0 failed/776）；演示台 `--all` 无 FAIL；5 组变异全 `DETECTED`。
> **生成评审包时撞了 C-03 自指互锁，并订正一处旧结论**：第一次生成时包内还写着 770 → 自指测试红 → 包被标不可发布
> （`571de430`）；重生成变绿 → `bd82caa5`；第三次生成 sha 不变才收敛。**两次不够，是三次**。
> 待人拍板项：**无**。下一步 B-04（judge 双评，需模型端点与配额）或 B-07（掌握度 P，依赖 B-01 历史）。

> **上一状态（round-77，§77，B-03 任务环境可执行化 + 参考 Agent 真跑）**：人回「继续」⇒ 做 B-03。
> 开工先核「YAML 写了什么、代码谁执行」：`fault_injection` 与 `tools[].mock.pagination`
> 在 `src/` 里**零消费者**——YAML 写「offset=10 首次会 500」，从来没有任何东西真的失败过；
> R3 能满分只是因为有人往夹具里抄了一行 `status: error`。
> 新增 `src/arena/mock_tools.py`（由 YAML 驱动的 mock 工具服务：分页语义 + 故障注入 + `reset()`；
> 三种未实现形态构造期报错，**不假装支持**）与 `src/arena/reference_agent.py`
> （确定性参考 Agent：真 HTTP、真重试、真写产物；四种 `flaw` 形态用于验证判定层）。
> **真跑实测**：`final_status=completed`、`call_count=4`、`schema_valid=True`、`ratio=1.0`，
> 算出 `{valid_count:17, total_amount:1837.99}` 与 `test_inputs.expected` **完全一致**（首次对撞手写期望值）。
> **四形态矩阵证明判定层分得开**：`wrong_filter` 只打 R2（0.50）、`extra_param` 只打 R4（0.75）、
> `no_retry` 打 R1+R5+R3（0.00，R2 `blocked`）——分数与失败位置逐一对得上
> （`total_weight = R2(2)+R3(1)+R4(1)`）。
> **一处误告订正**：P3 对账器用完美夹具跑出 `mismatch`「疑似编造」，因为两个答案值都是**聚合推导值**，
> 工具返回值里不可能逐字出现 `1837.99`。加 `derived_fields`：推导值缺出处 ⇒ `unverifiable`
> （`derived_not_replayable`），只有非推导字段缺出处才是 `mismatch`；不传时行为不变（7 条旧锁全绿）。
> 这原本不影响分数（对账器刻意未接 rubric），但 TD-039 计划正是接进去——按原样接，每个诚实提交都会被指控编造。
> **我自己写错一处**：测试断言「字段集 == `item_fields`」，实际 `success` 不带 `http_status`/`error`；
> 顺此查证平台侧**只**校验 `call_index`/`status`/`params` 类型，条件必填归 R3 执行器（已登记 TD-026 / B-22）。
> 裁判：**770 passed / 770**（741 + 13 + 12 + 4）；`--check` → 0（**`02bf120fcae8b287`**）；
> `--check --full` → **OK_FULL**（包内 0 failed/770，重跑 0 failed/770）；8 组变异全 `DETECTED`。
> **边界**：参考 Agent **不是 LLM**（步骤写死），trace 仍**自报**（Agent 可改 `call_index` 藏重试）——
> 所以不能声称"trace 不可伪造"；平台侧抓包与 LLM Agent 拆为 **B-03b**。
> 待人拍板项：**无**。下一步 B-04（judge 双评，需模型端点）或 B-06（映射表接进 `skill_gaps`，R-2）。

**匿名纪律（对 AI 同样适用）**：本文档与仓库内任何文件不含真实姓名/校名；请勿在 AI 对话中粘贴含真名/校名的材料；API key 与 PAT 绝不进入任何对话。

---

## 1. 项目一句话定位

Agent 开发学习领域的**自适应诊断与训练闭环**——学习者提交 Agent，系统对其执行轨迹做错误诊断（不只打分），把错误模式映射到技能缺口，按遗忘规律调度**可执行的任务变体**作为复习项，并按"掌握度 + 遗忘风险 + 错误热度 + 项目截止"生成训练日程。

参赛：第二十届全国大学生软件创新大赛，AI+学习方向；2027-01-15 18:00 初赛截止，**初赛只交项目计划书**（盲审、匿名）；演示平台须基于 Android（决赛提供 OPPO 设备）。

## 2. 当前状态（截至 2026-11-07）

- [x] 三轮选题演进定稿：ICPC OJ / Agent 学习 OJ 双域评估 → 确定首发 Agent 域，算法域为决赛扩展展望
- [x] 计划书 v0.4（吸收两轮外部评审，技术选型全部联网核实，采纳记录见计划书附 B）
- [x] 开发协作规范 v1（小步迭代 + 版本管理方案）
- [x] GitHub 私有仓库 `agent-arena` 建立，main 已推送，仓库级匿名 git 身份已配置
  （**可见性已独立核实**：2026-11-12 登录态页面 `Private` 徽标 + 匿名 API 404；
  仅指当前状态，历史公开性与 fork 状态未核实，见 `docs/review-response-v1.md` §50.3）
- [x] **M-1 第 4 步 A2E 侧 spike 完成 → 结论：A2E 不能作主判定层**（`spike/README.md`，三级证据：代码 / 运行 / 语义）
- [ ] agentevals 侧对称检查（M-1 第 5 步）
- [ ] 数据模型 v0 定稿（七实体字段，`docs/data-model.md`）
- [ ] 学生技术盘点回填计划书第七节【占位】

## 3. 已定决策清单（不再重议，新提议须走第 6 节采纳机制）

1. **首发域 = Agent 开发学习**；算法题域写成决赛扩展，不做。
2. **黑盒调用式提交**：学习者 Agent 部署为公开服务，平台只调用 + 评分 + 收 trace，不做代码沙箱；最小 trace schema 为提交格式的一部分。
3. **评测层 = 自研断言层（必做）+ 可选旁证画像层**：A2E 已实测证伪（26 指标全是聚合分 / LLM judge，
   无任务级布尔断言；`self_correction_rate` 不区分真假重试；见 `spike/README.md`）。
   计划书里"评测底座二选一"的表述须改。agentevals 待对称检查后再定要不要当旁证。
4. **复习项 = 可执行任务**（变体 / 故障注入调试题 / 限时练习 / 错题重放），FSRS（py-fsrs）只管"何时复习哪项技能"，不当卡片用。
5. **推荐先规则后模型**：MVP 用规则（错误模式 → 技能缺口 → 变体任务），不上深度知识追踪；pyBKT 作升级接口。
6. **MVP 规模锁死**：3 技能 × 每技能 2–3 变体，任务库上限 10 个；日程只做**建议排期**（四因子：掌握度/遗忘风险/错误热度/项目截止），不做全自动日历。
7. **交付形态**：Web（开发提交）+ Android（学习伴侣入口）；Streamlit 仅内部工作台，不进交付物。
8. **工程全部由 2 名学生承担**；学长为外部设计评审（技能图谱定义、错误分类学合理性、诊断规则逻辑、评测效度），不承担实现。
9. **能调不造**：py-fsrs、评测底座、Streamlit 全用现成；自研只押"错误诊断 → 技能推荐 → 可执行复习"链路。

## 4. 待拍板问题（计划书附 A 原样，逐条给意见即可）

1. 最小 trace schema 该包含哪些字段？黑盒模式下"错误恢复/重试策略"能否靠自愿上报的 trace 可靠评测？（若采 agentevals，schema 直接对齐 OTel GenAI 约定，是否够用？）
2. 错误分类学 v1 的粒度：多细的"错误模式"才既可判定又有教学价值？
3. 变体任务模板化的质量下限：模板生成的变体会不会被学习者看出套路？
4. 四因子排程的优先级怎么排？（我们暂定：截止 > 遗忘风险 > 错误热度 > 掌握度）
5. M0"一好一坏两个 Agent 跑分"之外还需要什么对照？reference-agent 当"好"基线是否合适？
6. 评测底座 spike 的判据：除集成成本外，还应比哪些维度？（我们暂定：trace 捕获完整度、断言表达力、license、社区活跃度）

## 5. 本机环境坑（生成/运行代码时注意）

- Windows 环境：路径统一正斜杠、脚本编码 UTF-8、不写含中文路径的 .bat。
- 本机 git 推送链路已修好（Watt Toolkit 加速器 MITM → 根证书已并入 git 信任包；凭据助手 wincred），但**若在学长机器上协作，勿复用本文档第 5 节的修复步骤**，那是本机特有环境问题。
- 依赖一律 pin 版本写进 `requirements.txt`；新库先核 license 记入开源边界表再装。

---

## 6. 设计准则（给 AI 的说明——生成任何内容前必读）

本节说明本项目的设计准则。学长侧 AI 生成的设计文档、代码、任务定义**必须遵守本节**，产出以文件形式提交进仓库（走 PR），不留在聊天记录里。

### 6.1 字段与命名规范（保持与现有资产一致）

- **实体类名 PascalCase，五个固定**：`User` / `Task` / `Skill` / `ErrorPattern` / `ReviewItem`。唯一真相源是 `docs/data-model.md`，字段变更视为破坏性变更，须单独一步并更新该文件。
- **Python 字段/函数/变量一律 snake_case**，常量 UPPER_SNAKE（技术栈：Python 3.11+ / FastAPI / SQLite，见协作规范一节）。
- **错误模式标签沿用既有词表**：`参数幻觉`、`工具选择错误`、`循环不收敛`、`未检查返回值`、`命名不一致`、`目录缺失`、`日期陷阱` 等。新增标签须先提交进分类学词表，再使用；禁止 AI 自创同义标签（如把"参数幻觉"写成"参数编造"）。
- **任务 YAML 字段**在 M-1 第 2 步定稿前未冻结；定稿前 AI 生成的内容用 snake_case 占位，并在文件头标注"字段待 data-model v0 定稿校对"。
- **commit 规范**：type 英文（`feat`/`fix`/`docs`/`test`/`chore`）+ 描述中文，如 `docs(model): 数据模型 v0 定稿`；分支名 `feat/<模块>-<内容>`、`spike/<试验>`、`fix/<问题>`。
- **文档语言中文**，技术术语首次出现附英文原文（如"知识追踪（Knowledge Tracing）"）。

### 6.2 分块细分的设计准则（按模块拆分、逐块细化）

- **任何设计/代码产出按模块分块**：任务层 / 评测层 / 状态层 / 复习层 / 排程层，五层边界见计划书 4.1 架构图；一次只细化一个模块，不产出横跨多模块的大文档或大段代码。
- **步长纪律**：一步 = 一个可独立验证的最小单元，单次改动 ≤ 半天工作量、限一个模块内；跨模块改动拆成多步串行。
- **每块开工先写"完成判据"**（可判定，如"输入 X 输出 Y"），做完先验证再进下一块；验证用 pytest 落在 `tests/`，不丢一次性脚本。
- **文档结构对齐此准则**：数据模型按实体分块（每实体一张表）、计划书按章节分块；AI 新增文档沿用"一文件一主题、块内自包含"的组织方式。

### 6.3 建议采纳机制（AI 的建议如何被采纳和落实）

- **建议 ≠ 决策**。学长侧 AI（或任何外部评审）提出的建议，统一走以下流程：
  1. **登记**：建议原文 + 来源，记入计划书"附 B：外部评审意见采纳记录"（或在 PR 中开一条讨论）。
  2. **核证**：涉及工具/库/数据的事实性主张，必须联网核实（名称、license、活跃度）后才可采纳；查无此物的一律不写入任何文档（先例：某建议中的 "Sidegent" 已核实不存在并删除）。
  3. **处置**：每条建议标注五类处置之一——`采纳` / `部分采纳`（写明取舍理由）/ `修正采纳`（写明修正点）/ `挂条件采纳`（写明放行条件）/ `不采纳`（写明理由）。
  4. **落仓**：被采纳的内容落到 `docs/` 对应文件并 commit，此后仓库版本即唯一真相；聊天记录中的方案不作数。
  5. **人拍板**：建模定义（如"稳定性事件"的判定）、评分维度、分类粒度等判断题，最终由学生决定、学长评审确认；AI 提供方案与依据，不拥有决定权。
- **AI 自评不作证据**："这样应该没问题"不算完成；采纳任何建议前先问"怎么验证"。
