---
name: create-tag
description: "为当前仓库推荐版本号并创建 Git 发布 Tag。用于打版本标签、选择下一个版本号或创建并推送 Tag 的请求，根据提交变化给出建议，确认后创建并区分本地与远端操作。"
---

# 创建 Git Tag

按本仓库约定创建版本标签。仅要求“创建 Tag”时只创建本地标签；推送需用户明确要求。提交代码、修改版本文件和手工创建 Release 不包含在打标签操作中。

## 1. 确定输入

- `$target`：用户指定的提交或引用。“当前提交”表示 `HEAD`；目标不明确时，展示当前分支与提交摘要并询问。
- `$tag`：用户确认的完整标签名。未指定时，先按第 3 步主动推荐版本号并说明依据，等待确认后再创建，不直接要求用户自行选定版本。
- `$remote`：计划推送时使用的已确认远端名称，不默认选择 `origin`。
- `$message`：标签注释，使用用户说明或已核实的公开变更摘要；没有说明时使用 `"Release $tag"`。

默认创建 annotated Tag；用户指定轻量或签名标签时遵循其要求，签名失败时停止，不降级标签类型。已明确的输入和操作范围不重复确认。

## 2. 检查仓库与发布规则

在仓库根目录执行下列 PowerShell 命令。逐条检查原生命令的 `$LASTEXITCODE`，仅预期退出码可继续；不要只依赖 `$ErrorActionPreference`。

```powershell
$commit = git rev-parse --verify --end-of-options "${target}^{commit}"
if ($LASTEXITCODE -ne 0) { throw '目标引用未解析为提交' }

git status --short --branch
git log -1 --format='%H %s' "$commit"
git tag --list --sort=-version:refname
git remote
```

后续推荐、创建和验证均使用固定的 `$commit`，避免用户确认期间引用变化导致标签指错提交。

保留所有未提交改动。Tag 只指向提交快照，不包含工作区和暂存区改动；是否需要纳入这些改动不明确时先询问，不自动 commit、stash、reset 或切换分支。

读取 [版本解析逻辑](../../../scripts/release_metadata.py)、[CI 工作流](../../../.github/workflows/ci.yml) 和 [发布约定](../../../docs/release-mirror.md#3-发布与补传)，以当前仓库内容为准：

- 正式标签沿用 `vMAJOR.MINOR.PATCH[.REVISION][+BUILD]`，数字段范围为 0—65534。
- `+BUILD` 不参与数字版本排序；仅改变构建标识不会产生更高的更新版本。需要发布新版本时，比较 `parse_version` 的结果，不按字符串判断新旧。
- 当前 CI 监听所有 Tag push，预发布或非版本标签会在正式版本校验阶段失败。遇到此类需求，先说明差异并确认用途，不自行修改标签名或工作流。
- 推送正式标签会在构建成功后自动创建 GitHub Release，再同步 R2。计划推送时先说明这一效果，并按 [镜像配置](../../../docs/release-mirror.md#2-配置-github-actions) 核对必需配置；只检查凭据是否配置，不读取或输出密钥值。缺项或未核实的配置先报告，等待用户决定。
- 发布前查看目标提交已有的 CI 结果；需要本地验证时遵循 [开发与交付](../../../docs/development.md#2-构建与测试)。只创建本地 Tag 不要求重新执行完整构建。

## 3. 推荐版本号

用户已指定完整标签名时，保留其选择并进入校验；未指定或明确请求建议时，先推荐再等待确认。仅请求推荐时，到给出建议为止。

1. 用 `git tag --merged "$commit"` 找出目标提交可达的标签，过滤掉 `parse_version` 不接受的名称，以解析后的数字元组选择最高版本作为 `$baseTag`，不用创建时间、字符串排序或其他分支的最高标签代替基线。浅克隆、标签信息不完整或版本线不明确时先说明并询问。计划推送且已确认远端时，用 `git ls-remote --tags $remote` 核对已用版本；只查本地时明确建议范围。
2. 有基线时，阅读基线到目标提交的提交正文和实际差异，只考虑已提交的变化。提交前缀、`!`、`BREAKING CHANGE` 是线索，最终以功能和兼容性变化为依据：

   ```powershell
   git log --format='%h %s%n%b' "${baseTag}..${commit}"
   git diff --stat "$baseTag" "$commit"
   ```

   按需查看关键文件的 `git diff`。没有正式标签时，读取项目已有版本配置并加上 `v` 前缀作为初始候选；配置也未定义时可建议 `v0.1.0`，明确这是初始版本建议，直接进入候选校验，不按下表递增。
3. 优先沿用仓库既有版本约定；没有更具体约定时，按下表提出一个推荐值。混合变更取所需的最高升级级别，升级某段后将更低段归零。

   | 已核实的变化 | 推荐升级 |
   | --- | --- |
   | 向后兼容的修复、性能改进或需要发布的内部调整 | PATCH 加 1 |
   | 向后兼容的新功能 | MINOR 加 1 |
   | 稳定版的破坏性接口、配置或行为变更 | MAJOR 加 1 |

   `0.x` 的破坏性变更和四段版本沿用已有约定；约定不清时先确认，不擅自转为 `1.0.0` 或改变版本段数。没有新增提交时报告已有版本；只有文档、测试等非交付变化时，先说明没有明显的新版本发布需求，用户仍要求新版本时再给出 PATCH 建议。
4. 推荐值须通过现有版本解析和 Git 名称校验，数字段保持在 0—65534。检查所有已知标签，避免同名和相同数字版本冲突；`+BUILD` 不算版本升级。候选已被占用或与其他发布线冲突时说明原因并确认，不机械循环递增。
5. 展示“推荐版本、基线标签、目标 SHA、变更分类及依据、查询范围”，请求用户确认推荐版本或给出其他版本。推荐不代表创建或推送；用户确认后继续，用户另有版本选择时尊重其选择并校验。

## 4. 校验版本、提交与重名

复用项目的版本解析函数，不另写一套版本正则。使用第 2 步固定的 `$commit`，不要在确认推荐后重新解析移动中的分支引用。

```powershell
git check-ref-format "refs/tags/$tag"
if ($LASTEXITCODE -ne 0) { throw 'Tag 名称不合法' }

python -c "import sys; from scripts.release_metadata import parse_version; print(parse_version(sys.argv[1]))" "$tag"
if ($LASTEXITCODE -ne 0) { throw 'Tag 不符合正式版本格式' }

git show-ref --verify --quiet "refs/tags/$tag"
```

最后一条命令退出码 `0` 表示本地同名标签已存在，`1` 表示不存在，其他值表示检查失败。已有标签解引用后的提交与 `$commit` 相同，且类型、注释和签名满足请求时，复用并报告，不重复创建；不一致则停止并询问，不删除、移动或覆盖标签。

计划推送时，在创建前检查远端重名，并在实际推送前重新检查：

```powershell
git ls-remote --exit-code --tags $remote "refs/tags/$tag" "refs/tags/${tag}^{}"
```

退出码 `0` 表示远端存在同名标签，`2` 表示不存在，其他值表示网络、认证或查询失败，不当成“标签不存在”。远端已有同名标签时，核对其解引用提交（轻量标签直接使用引用行）：与目标相同则报告远端标签已存在，不重复推送；不同则停止并询问。仅本地创建且未检查远端时，在结果中注明这一范围。

## 5. 创建并验证本地标签

本地不存在同名标签时，展示标签名、完整 SHA、提交摘要和操作范围，再按已确认的请求执行。以下命令用于默认的 annotated Tag：

```powershell
git tag -a -m "$message" -- "$tag" "$commit"
if ($LASTEXITCODE -ne 0) { throw '创建 Tag 失败' }

$tagCommit = git rev-parse --verify "refs/tags/${tag}^{commit}"
if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $commit) { throw 'Tag 目标验证失败' }

git for-each-ref --format='%(refname:short) %(objecttype) %(objectname)' "refs/tags/$tag"
if ($LASTEXITCODE -ne 0) { throw '读取 Tag 信息失败' }
```

确认 annotated Tag 的对象类型为 `tag`；签名标签还需验证签名。验证失败时保留现场并报告，不自动删除后重建。用户未要求推送时到此结束。

## 6. 按明确请求推送

确认远端与发布效果已说明、用户已明确要求推送后，只推送本次标签。禁用自动跟随其他标签，不使用 `--tags`、`--force`，不附带分支推送。

```powershell
git push --no-follow-tags $remote "refs/tags/${tag}:refs/tags/${tag}"
if ($LASTEXITCODE -ne 0) { throw 'Tag 推送失败，请先复查远端状态' }

git ls-remote --exit-code --tags $remote "refs/tags/$tag" "refs/tags/${tag}^{}"
if ($LASTEXITCODE -ne 0) { throw '远端 Tag 状态待确认' }
```

核对远端标签对象 ID 与本地 `git rev-parse "refs/tags/$tag"` 一致，且解引用后的提交为 `$commit`。推送或查询中断后先重新查询远端，确认状态再决定后续操作，不盲目重试或强推。

## 7. 报告结果

简要列出标签名、类型、完整 commit SHA，以及本地创建、远端推送各自的状态。只有实际查询到 CI、GitHub Release 或 R2 的结果时才报告对应状态；推送成功不等于发布完成。用户要求等待发布时再跟进工作流，不自动重跑任务。

R2 镜像失败时保留现有 Tag 和 GitHub Release，按 [发布与补传](../../../docs/release-mirror.md#3-发布与补传) 说明后续操作；不以重建标签或覆盖产物处理失败。
