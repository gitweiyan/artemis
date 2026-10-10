# 分支管理与 PR 策略

> 本文档落地 2026-10-09 复盘（`reports/复盘-20261009-搜狐视频搜索大鹏.html`）的改进项 **I1**。
> 机制、实证与排查片段见 skill `writing-run-retrospectives` 的 `knowledge/branch-base-regression.md`；
> 本文档只做仓库内的**约定 + 执行清单**。

## 1. 命名规范（自本文档起生效，存量分支保留原名）

```
<type>/<mmddhhmm>-<topic>
```

| 段 | 含义 |
|---|---|
| `type` | `fix` / `test` / `integration` / `chore` / `docs` / `feat` |
| `mmddhhmm` | **分支创建时间**（本地时区），月日时分各 2 位，共 8 位，例 `10091051` |
| `topic` | 小写连字符短语，一条工作线一个主题，例 `helper-token-provider` |

示例：`fix/10091051-helper-token-provider`、`test/10091711-e2e-battery-level-case`、`docs/10101204-branch-plan`

**为什么要带创建时间**

1. 同一主题会在不同批次重复出现——本轮就产生了 6 条同族分支，靠名字判断"谁先创建、该基于谁"曾经判断错误，代价是一次运行整场降级（复盘 P7/R1）；
2. `git branch --list 'fix/*' | sort` 直接得到时间序，无需逐个 `git log`；
3. 与复盘报告的"时间 + 事件"口径可直接互引。

**创建时间怎么取**（git 不存储分支创建时间）：

```bash
git reflog show --date=format:'%m%d%H%M' --format='%gd %ad' <branch> | tail -1
```

reflog 记录的是**本地 ref 出现**的时刻：本地新建分支即创建时间；对从远端拉下来的分支则是拉取时间（下表已按此标注依据）。

## 2. 基线纪律（硬性，违反即可能"跑的不是修好的代码"）

1. **基线显式命名，并按内容核对**：`git show <base>:<path> | grep -c <marker>`；不要相信"当前 HEAD 最新"。跨分支工作时，先选覆盖最全的基线（用内容判断，不用分支名或日期）。
2. **跨分支工作显式整合**：`git merge --no-edit <other>`（或 cherry-pick），不要假设"它应该在"。
3. **派生或合并后立刻查静默回退**：`git diff --stat <含修复的分支> HEAD -- <paths>`；出现删除即为缺陷。
4. **重要运行前断言代码身份**：分支、HEAD、dirty 文件清单，以及每个必须在场的修复标记（一个 `grep -c` 一个）。
5. **同一次会话内提交修复后，不得切换到基于更旧提交的分支而不复核。**

可直接粘贴的完整检查：

```bash
BASE=<覆盖最全的分支>
git show $BASE:artemis/agents/flash/summarizer.py | grep -c get_llm_for_model   # 必须 > 0
git checkout -b <type>/<mmddhhmm>-<topic> $BASE
git merge --no-edit <持有其余工作的分支>
git diff --stat <持有其余工作的分支> HEAD -- artemis/ tests/                     # 为空 = 无回退
printf 'branch=%s head=%s dirty=%s\n' "$(git branch --show-current)" \
       "$(git rev-parse --short HEAD)" "$(git status --porcelain | tr '\n' ' ')"
```

## 3. 当前分支表（存量分支，保留原名；右列为按新规范的等价名）

| 分支 | tip | 创建时间 | 依据 | 相对自身基线的增量 | 按新规范 |
|---|---|---|---|---|---|
| `fix/p0-tool-call-protocol` | f76bf0f | 09281729 | reflog 首条 | 8 提交 / 21 文件 | `fix/09281729-p0-tool-call-protocol` |
| `fix/lightweight-judge-provider` | 40f5f4f | 09281729 | reflog 首条 | 4 提交 / 10 文件 | `fix/09281729-lightweight-judge-provider` |
| `integration/p0-lightweight-judge` | 4a0667b | 09281729 | reflog 首条（本地整合） | 前两条的本地整合 | `integration/09281729-p0-lightweight-judge` |
| `fix/helper-token-provider` | 4cd590a | 10091051 | reflog 首条 | 3 提交 / 11 文件 | `fix/10091051-helper-token-provider` |
| `fix/provider-aware-lenses` | 72e0c5d | 10091400 | reflog 首条 | 1 提交 / 8 文件 | `fix/10091400-provider-aware-lenses` |
| `fix/helper-probe-deadline` | f7e2844 | 10091400 | reflog 首条 | 1 提交 / 2 文件 | `fix/10091400-helper-probe-deadline` |
| `test/e2e-battery-level-case` | e022a01 | 10091711 | reflog 首条 | 1 提交 / 2 文件 | `test/10091711-e2e-battery-level-case` |
| `integration/helper-lens-e2e` | 8c0aa4d | 10091743 | reflog 首条（本地整合） | 上四条的整合 | `integration/10091743-helper-lens-e2e` |

依赖关系（`→` 表示基于）：

```
main (351ca84)
├── fix/p0-tool-call-protocol ─┐
├── fix/lightweight-judge-provider ─┴→ integration/p0-lightweight-judge
│                                        └→ fix/helper-token-provider
│                                             ├→ fix/provider-aware-lenses → test/e2e-battery-level-case
│                                             └→ fix/helper-probe-deadline
│                                                  └→ integration/helper-lens-e2e（全部整合）
```

## 4. PR 划分与提交顺序

按"一事一 PR"拆成 6 个，顺序即依赖顺序；空集表示该 PR 与前面的 PR 无文件重叠。

| # | 分支 | 内容 | 基线 | 文件 | 评审要点 |
|---|---|---|---|---|---|
| 1 | `fix/p0-tool-call-protocol` | tool-call 协议修复（8 提交） | main | 21 | 独立，先提 |
| 2 | `fix/lightweight-judge-provider` | judge provider + 节点名硬校验（4 提交） | main | 10 | 与 1 并行 |
| 3 | `fix/helper-token-provider` | 令牌 ContentProvider / 组件名规范化 / root uid / 协议 3 / APK 1.3.1 | 1+2 合入后的 main | 11 | **含二进制 APK + helper_manifest.json**：需说明"源码与产物一致"的证据（sha256、`aapt2 dump badging`、`dexdump`） |
| 4 | `fix/provider-aware-lenses` | provider 感知 lens（I1） | 3 之后 | 8 | 与 3 无文件重叠 |
| 5 | `fix/helper-probe-deadline` | 探测短截止 + "慢≠不存在"（I4a） | 3 之后 | 2 | 与 3 改同一文件的**不同区域**；`git merge-tree` 干跑无冲突，但语义上仍建议 3 先 |
| 6 | `test/e2e-battery-level-case` | 真机电池用例 + `make test-case-battery` | 4 之后 | 2 | 纯测试；`-m e2e` 才会被收集 |

**冲突面实测**：四条工作线的增量两两不相交；唯一重叠是 PR-3 ∩ PR-5 = `artemis/runtime/helper_manager.py` 与 `tests/unit/runtime/test_helper_manager.py`（同文件不同区域，反向顺序也不冲突）。

## 5. 整合分支的定位

`integration/helper-lens-e2e`（现 8c0aa4d）是上述 6 个 PR 的**并集**，因此**不作为 PR**（不可评审）。
它的用途只有一个：**设备验证与全量回归的唯一头部**——以后"跑一次真机/全量"都用它，避免再次出现"跑的不是想跑的那份代码"。
纪律第 4 条的代码身份断言在它上面执行。

## 6. 合并后的清理顺序（现在不要删，PR 头就是这些分支）

1. PR-1、PR-2 合入 → 删 `integration/p0-lightweight-judge`，把 PR-3 rebase 到 main；
2. PR-3 合入 → PR-5 rebase 到 main（`merge-tree` 已验证无冲突）；
3. PR-4 合入 → PR-6 rebase 到 main；
4. 全部合入后，`integration/helper-lens-e2e` 可删除，或保留为发布验证分支。

## 7. 存量分支改名（可选，PR 打开前做才安全）

PR 尚未创建，现在改名代价最低；一旦有 PR 依赖旧名字，改名会导致 PR 失联。

```bash
# 本地
for pair in \
  "fix/p0-tool-call-protocol:fix/09281729-p0-tool-call-protocol" \
  "fix/lightweight-judge-provider:fix/09281729-lightweight-judge-provider" \
  "integration/p0-lightweight-judge:integration/09281729-p0-lightweight-judge" \
  "fix/helper-token-provider:fix/10091051-helper-token-provider" \
  "fix/provider-aware-lenses:fix/10091400-provider-aware-lenses" \
  "fix/helper-probe-deadline:fix/10091400-helper-probe-deadline" \
  "test/e2e-battery-level-case:test/10091711-e2e-battery-level-case" \
  "integration/helper-lens-e2e:integration/10091743-helper-lens-e2e"; do
  old="${pair%%:*}"; new="${pair##*:}"
  git branch -m "$old" "$new"
  git push fork "$new"
  git push fork --delete "$old"
done
```

## 8. 遗留决策（与本策略相关，尚未决定）

1. **`config/artemis.jsonc` 未提交**：本地档位为 `openai/deepseek-flash`，而仓库里提交的是 `google` 档位。任何从干净 checkout 运行的人都会退回 google 档位——这是"代码身份不一致"在配置层的同族问题。要么提交该改动，要么把它文档化到 `.env.example` 并保留本地覆盖。
2. **PR-3 的二进制产物**：若上游不接受提交 APK，建议拆成"源码提交"+"产物由构建脚本产生"两个提交，避免评审把注意力放在二进制上。
