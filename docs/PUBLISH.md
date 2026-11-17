# 发布到 GitHub（操作指南）

当前仓库状态：已 `git init`、已有本地提交、**尚未配置远程仓库**，且本机未安装 `gh` 命令行。
所以发布分两步：先在 GitHub 网页建一个**空仓库**，再用下面的 git 命令把本地代码推上去。

---

## 第 0 步：发布前安全检查（重要）

```bash
cd /d/neuro-memory

# 1) 确认没有真实 Key 混进被跟踪的文件（应无输出，或只有测试占位 sk-...abcd）
git grep -nE 'sk-[A-Za-z0-9]{20,}' -- ':!tests/*' || echo "OK: 无真实 Key"

# 2) 确认数据库 / data / .env 都没被跟踪（应无输出）
git ls-files | grep -E '(^|/)(data|backup|backups)/|\.db$|(^|\.env$)' || echo "OK: 无敏感文件被跟踪"

# 3) 确认收款码图片会被正常纳入版本控制（要让别人看到并转账）
git check-ignore web/donate/wechat.png && echo "警告: 收款码被忽略了!" || echo "OK: 收款码可随仓库发布"
```

> 收款码（`web/donate/*.png`）**故意不忽略**：它会随仓库发布，别人 clone 后能在「支持」页看到。

> 你的 API Key 只在本机 `data/*.db`（DPAPI 加密），`data/` 与 `*.db` 均已 gitignore，
> 不会随仓库发布。Web 里填的 Key、个人记忆、trace 同理。

---

## 第 1 步：在 GitHub 网页创建空仓库

1. 打开 https://github.com/new
2. Repository name 填 `recollect`（或你想要的名字）
3. Description 填：`Long-term conversational memory with attribution, forgetting and reconsolidation — MCP / HTTP / SDK`
4. 选 **Public**
5. **不要**勾选 "Add a README"、"Add .gitignore"、"Choose a license"
   （本地都已有，勾了会冲突）
6. 点 **Create repository**
7. 复制页面给出的仓库地址，形如 `https://github.com/<你的用户名>/recollect.git`

---

## 第 2 步：本地关联并推送

把下面命令里的 `<你的用户名>` 和 `<仓库名>` 换成实际的：

```bash
cd /d/neuro-memory

# 关联远程（首次；若已存在用 git remote set-url origin <url>）
git remote add origin https://github.com/<你的用户名>/<仓库名>.git

# 确认当前分支名（通常是 master 或 main）
git branch --show-current

# 推送并把本地分支与远程同名分支关联（-u 之后可直接 git push）
git push -u origin HEAD
```

如果 GitHub 要求认证：HTTPS 方式会弹出登录，或需要 Personal Access Token（PAT）。
生成 PAT：GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)，
勾选 `repo` 权限；推送时用户名填 GitHub 用户名，密码填 PAT。
（更省事的是安装 GitHub Desktop 或配置 SSH key，但上面的 HTTPS+PAT 最直接。）

---

## 第 3 步：验证发布成功

1. 刷新 GitHub 仓库页面，应能看到 README、LICENSE、代码目录。
2. 检查文件列表里**没有** `data/`、`*.db`、`.env`（这些是隐私，绝不能上传）；
   但**应该有** `web/donate/` 下的收款码图片（这是给别人转账用的，要保留）。
3. 本地随便找台干净机器（或 `git clone` 到临时目录）跑一遍：
   ```bash
   git clone https://github.com/<你的用户名>/<仓库名>.git /tmp/recollect-check
   cd /tmp/recollect-check
   pip install -r requirements.txt
   python -m pytest tests/unit -q    # 应全绿，且不需要 data/
   ```

---

## 后续日常发布

```bash
git add -A
git commit -m "说明这次改了什么"
git push            # 第一次带 -u 之后，直接 push 即可
```

---

## 可选：换掉示例域名 / 仓库名

- 若仓库名不是 `recollect`，MCP 配置示例里的 `recollect` 只是连接名，可任意，无需改代码。
- README 里的产品名已统一为 **Recollect**；Python 包名仍是 `neuromem`（内部实现，见 Roadmap）。
