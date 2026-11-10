# PR 评论失败排查

## 结论

本轮所有 PR 汇报均使用 Codex 的 GitHub 集成工具 `mcp__codex_apps__github_add_comment_to_issue`，没有使用 `gh pr comment`。读取 PR 评论和仓库元数据正常，但创建评论固定失败：

```text
GitHub API error 403: {"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/issues/comments#create-an-issue-comment","status":403}
```

同一 worktree 的 `git push origin perf/emoji-render` 成功，说明网络和仓库推送凭据正常。`github_get_repo` 返回的仓库元数据权限为 `admin=true, maintain=true, push=true`，因此失败点是当前 Codex GitHub integration 的 issue/PR comment 写入能力或连接身份，不是仓库本身的 push 权限。

## gh 检查

按审查要求运行 `gh auth status` 和 `gh api repos/zhixin626/DanmakuRender --jq .permissions`，两条命令均返回 PowerShell 的“`gh` 不是内部或外部命令/可运行程序”错误；本机没有安装 GitHub CLI，因此无法用 `gh` 验证登录状态，也无法执行 `gh pr comment` 的短评论和 `--body-file` 长评论对照。

自动审查还拒绝了包含 `gh pr comment` 测试写入的组合命令，理由是会向公开 PR 发布测试评论并外传本地路径；没有绕过该拒绝，也没有发布测试评论。

## 后续汇报方式

阶段 2 和后续阶段的完整六项汇报会写入 `perf/results/<阶段>.md` 并随提交推送，保证 Claude 可以从提交中审查。用户随后安装并登录 GitHub CLI；在新 shell 中用绝对路径 `C:\\Program Files\\GitHub CLI\\gh.exe` 成功完成 `gh auth status`、`gh pr view`、短评论和 `--body-file` 长评论测试，评论 ID 为 6757996738 和 6757997197。后续汇报采用“先写 md，再用 gh pr comment --body-file 发送”。
