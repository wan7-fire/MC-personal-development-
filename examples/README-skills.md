# ZXCode 示例 Skill

把 `skills/` 下的四个子目录复制到项目 `.zxcode/skills/`
（或用户级 `~/.zxcode/skills/`），启动 ZXCode 后即可验证：

- `hello/`：共享模式 + 工具白名单
- `audit/`：隔离模式 + `history: none` + 四字段摘要回流
- `pkgdemo/`：目录型 Skill，自带 `tools/count` 工具
- `broken/`：故意写坏的 frontmatter，验证跳过与日志

只复制子目录，不要复制本 README 到 `.zxcode/skills/`。

PowerShell 复制示例（在项目根目录执行）：

```powershell
Copy-Item -Recurse examples\skills\hello   .zxcode\skills\hello
Copy-Item -Recurse examples\skills\audit   .zxcode\skills\audit
Copy-Item -Recurse examples\skills\pkgdemo .zxcode\skills\pkgdemo
Copy-Item -Recurse examples\skills\broken  .zxcode\skills\broken
```
