# 分支管理与 fork 工作流

> 本文档落地 2026-10-09 复盘（`reports/复盘-20261009-搜狐视频搜索大鹏.html`，本地留存、未入库）的改进项 **I1**。
> 目标远端是**个人 fork**（`git@github.com:gitweiyan/artemis.git`），**不向上游 `google/artemis` 提 PR**，
> 因此工作流是"整合分支 + 标签"，不是"一事一 PR"。
> 机制、实证与排查片段见 skill `writing-run-retrospectives` 的 `knowledge/branch-base-regression.md`。

## 1. 命名规范

```
<type>/<mmddhhmm>-<topic>
```

| 段 | 含义 |
|---|---|
| `type` | `fix` / `test` / `integration` / `chore` / `docs` / `feat` |
| `mmddhhmm` | **分支创建时间**（本地时区），月日时分各 2 位，共 8 位，例 `10091051` |
| `topic` | 小写连字符短语，一条工作线一个主题，例 `helper-token-provider`、`all-fixes` |

示例：`fix/10091051-helper-token-provider`、`test/10091711-e2e-battery-level-case`、`integration/10101205-all-fixes`

**为什么要带创建时间**

1. 同一主题会在不同批次重复出现——2026-10-09 一天内产生了 6 条同族分支，靠名字判断"谁先创建、该基于谁"曾判断错误，代价是一次真机运行整场降级（复盘 P7/R1：记忆链路 9 次 + 视觉摘要 8 次）；
2. `git branch --list 'fix/*' | sort` 直接得到时间序，无需逐个 `git log`；
3. 与复盘报告"时间 + 事件"的口径可直接互引。

**创建时间怎么取**（git 不存储分支创建时间）：

```bash
git reflog show --date=format:'%m%d%H%M' --format='%gd %ad' <branch> | tail -1
```

reflog 记录的是**本地 ref 出现**的时刻：本地新建即创建时间；从远端拉下来的分支则是拉取时间。

## 2. 基线纪律（硬性）

1. **基线显式命名，并按内容核对**：`git show <base>:<path> | grep -c <marker>`；不要相信"当前 HEAD 最新"。跨分支工作时先选覆盖最全的基线（用**内容**判断，不用分支名或日期）。
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

## 3. 最终分支形态（fork 只保留两条长期分支）

| 分支 | 角色 | 说明 |
|---|---|---|
| `main` | **上游镜像**、fork 默认分支 | 与 `origin/main` 一致；后续用 `git fetch origin && git merge origin/main` 同步，冲突面最小 |
| `integration/10101205-all-fixes` | **唯一的"当前状态"分支** | 全部工作线的并集；设备验证与全量回归都用它 |

日常只需要关心一个工作分支；`main` 只作为上游镜像与同步基线。

**工作线标签**（被删分支的内容由这些标签保全，可随时 diff 或重建分支）

| 标签 | 提交 | 工作线 | 状态 |
|---|---|---|---|
| `09281729-p0-tool-call-protocol` | f76bf0f | P0 tool-call 协议修复（8 提交） | 已并入 |
| `09281729-lightweight-judge-provider` | 40f5f4f | judge provider + 节点名硬校验（4 提交） | 已并入 |
| `09281729-p0-lightweight-judge` | 4a0667b | 前两条的本地整合 | 已并入 |
| `10091051-helper-token-provider` | 4cd590a | 令牌 ContentProvider / 组件名规范化 / root uid / 协议 3 / APK 1.3.1 | 已并入 |
| `10091400-provider-aware-lenses` | 72e0c5d | provider 感知 lens（I1） | 已并入 |
| `10091400-helper-probe-deadline` | f7e2844 | 探测短截止 + "慢≠不存在"（I4a） | 已并入 |
| `10091711-e2e-battery-level-case` | e022a01 | 真机电池用例 + `make test-case-battery` | 已并入 |

## 4. 日常工作流（fork 版，无 PR）

**开始一条新工作线**

```bash
git checkout integration/10101205-all-fixes && git pull --ff-only fork integration/10101205-all-fixes
git checkout -b <type>/<mmddhhmm>-<topic>            # 从当前状态派生，创建时间即此刻
```

**收尾并入整合分支**

```bash
git checkout integration/10101205-all-fixes
git merge --no-edit <type>/<mmddhhmm>-<topic>                        # 步骤 2：显式整合
git diff --stat <type>/<mmddhhmm>-<topic> HEAD -- artemis/ tests/    # 步骤 3：查静默回退（应为空）
git tag <mmddhhmm>-<topic> <type>/<mmddhhmm>-<topic>                 # 步骤 4：先打标签
git push fork integration/10101205-all-fixes --tags
git push fork --delete <type>/<mmddhhmm>-<topic> && git branch -D <type>/<mmddhhmm>-<topic>
```

**每次跑真机 / 全量回归前**：执行第 2 节第 4 条的代码身份断言，并确认所在分支是 `integration/10101205-all-fixes`。

## 5. 本次执行记录（2026-10-10）

前置事实：`integration/helper-lens-e2e`(64510f9) = `main` + 24 提交 / 51 文件，**9 条本地分支全部是它的祖先**（逐条 `git merge-base --is-ancestor` 验证）——即内容层面的"合并"已经完成，本次只做定名、清理与推送。

实际执行：

```bash
git ls-remote --heads fork > /tmp/fork-before.txt     # 回滚保险：记录 10 条远端分支 SHA
# 打第 3 节表中的 7 个标签
git checkout -b integration/10101205-all-fixes integration/helper-lens-e2e
# 改写本文档为 fork 工作流 → 提交 → 推送整合分支与标签
# 删除以下 9 条已被整合分支包含的分支（本地 + fork）：
#   fix/p0-tool-call-protocol, fix/lightweight-judge-provider, integration/p0-lightweight-judge,
#   fix/helper-token-provider, fix/provider-aware-lenses, fix/helper-probe-deadline,
#   test/e2e-battery-level-case, docs/10101205-branch-plan, integration/helper-lens-e2e
```

回滚任意一条被删分支：

```bash
git push fork <tag>:refs/heads/<原分支名>
# 例：git push fork 10091400-provider-aware-lenses:refs/heads/fix/provider-aware-lenses
```

## 6. 遗留与注意

1. **`config/artemis.jsonc` 与 `reports/` 有意不入库**（2026-10-10 决定），后果需明确：
   - fork 的整合分支里 `config/artemis.jsonc` 仍是上游的 `google` 档位——**从 fork 克隆后需自备 `.env`（Google key）或本地改成 `openai` 兼容端点**，否则 flash 与记忆/摘要链路会因缺凭据降级；
   - 两份复盘报告与证据 JSON 只存在于本机 `reports/`（HTML 92K、evidence 约 700K），仓库读者看不到；本文档与 skill 中的引用指向本机路径。
2. **整合分支含二进制 APK**（`packages/artemis-accessibility-helper/ArtemisAccessibilityHelper.apk` 与 `helper_manifest.json`）：改 helper 源码后必须重编译并提交产物，否则"源码与产物不一致"——与第 2 节的代码身份纪律同源。
3. **`reports/` 未加入 `.gitignore`**：如需彻底避免误提交可加一行 `reports/`；目前保持未忽略，便于你按需 `git add reports/*.html`。
4. 若将来确实要向上游提 PR，用第 3 节的标签重建分支即可（`git branch <name> <tag>`）；第 1、2 节仍然适用。
