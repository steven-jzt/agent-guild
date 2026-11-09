# Codex 访问方式（中国大陆）

## 结论

**OpenAI 官方目前不向中国大陆提供 ChatGPT / API 注册。**  
官方支持列表里没有中国大陆；从不受支持地区注册会直接报“unsupported country”。

来源：

- [OpenAI Help：为什么国家/地区不受支持](https://help.openai.com/zh-hans-cn/articles/8983035-why-cant-i-sign-up-due-to-unsupported-country)
- [OpenAI Supported Countries](https://platform.openai.com/docs/supported-countries)

不建议用 VPN、虚拟海外手机号、伪造地址绕过地区限制：违反服务条款，账号可能被封。

**但 Codex CLI 可以不依赖 OpenAI 账号**：它支持 OpenAI Responses 协议，国内模型厂商可以直接接。

---

## 方案 A：DeepSeek 官方 Codex 接入（本机已实测）

官方文档：[Integrate with Codex](https://api-docs.deepseek.com/quick_start/agent_integrations/codex)

```powershell
# 1. 安装 Codex CLI
npm install -g @openai/codex

# 2. 用隔离的 CODEX_HOME，避免污染全局
$env:CODEX_HOME="C:\path\to\codex-deepseek-home"
New-Item -ItemType Directory -Force -Path $env:CODEX_HOME | Out-Null

# 3. 设置 DeepSeek key
$env:DEEPSEEK_API_KEY="sk-..."

# 4. 跑官方 setup 脚本，菜单选 1（deepseek-flash）
irm https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.ps1 | iex

# 5. 验证
codex exec --json --skip-git-repo-check --sandbox danger-full-access "Reply with exactly: OK"
```

本机结果：

```json
{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}
{"type":"turn.completed","usage":{"input_tokens":9241,"cached_input_tokens":0,"output_tokens":11}}
```

已用这条路径跑通一次真实 Codex V1 端到端（DeepSeek provider，`candidate=merged`、`accepted`）。

---

## 方案 B：智谱 GLM Coding Plan（国内可用，未实测）

官方文档：[Codex 中使用 GLM Coding Plan](https://docs.bigmodel.cn/cn/coding-plan/tool/codex.md)

智谱同样支持 OpenAI Responses 协议：

- Base URL：`https://open.bigmodel.cn/api/v1`
- 模型：`glm-5.3` / `glm-5-turbo`
- `wire_api = "responses"`

步骤：

1. 在 [智谱开放平台](https://open.bigmodel.cn) 注册；
2. 开通 GLM Coding Plan，创建 **编程套餐专用 API Key**（个人/团队/标准 API 的 key 不通用）；
3. 创建隔离的 `CODEX_HOME`，从官方文档复制 `models.json`；
4. 写 `config.toml`：

```toml
model_provider = "ZAI"
model = "glm-5.3"
model_reasoning_effort = "max"
model_catalog_json = "C:/path/to/codex-zhipu-home/models.json"

[model_providers.ZAI]
name = "ZAI"
base_url = "https://open.bigmodel.cn/api/v1"
experimental_bearer_token = "<Your API Key>"
wire_api = "responses"
```

5. 验证：

```powershell
$env:CODEX_HOME="C:\path\to\codex-zhipu-home"
codex exec --json --skip-git-repo-check --sandbox danger-full-access "Reply with exactly: OK"
```

注意：本轮没有智谱 key，未实测；配置来自官方文档。

---

## 方案 C：本地开源模型

```powershell
codex exec --oss --local-provider ollama "..."
codex exec --oss --local-provider lmstudio "..."
```

需要本机安装 Ollama / LM Studio，并拉一个代码模型。  
不需要任何账号，但能力取决于本地模型。

---

## 方案 D：真实在支持地区

如果本人实际在新加坡/日本/美国等支持地区，且有当地手机号/支付方式：

1. 在当地访问 `chatgpt.com` / `platform.openai.com`；
2. 邮箱注册；
3. 当地手机验证；
4. 需要 API 的再创建 key；
5. 回本机 `codex login` 或 `codex login --with-api-key`。

不要用虚假地区/号码绕过。

---

## 方案 E：试点先跳过 Codex

DSH 已是真实 agent，已跑通 5 个 V1 任务（3 成功 / 2 拦截）。  
Codex 不是试点阻塞项。

---

## 一个已排除的手工配置

手工把 Codex 指向 DeepSeek 的 chat 接口：

```toml
wire_api = "chat"
```

Codex 0.167.0 已不支持 `wire_api = "chat"`，要求 `responses`。  
**但用 DeepSeek 官方 setup 脚本（方案 A）可以正常工作**，因为它写入了 `models.json` 和 `wire_api = "responses"`。

---

## 对试点的建议

- **Codex 用方案 A（DeepSeek）**：已真实跑通，不依赖 OpenAI；
- 智谱（方案 B）作为备选；有 key 我可以按同一 `CODEX_HOME` 模式接；
- Electron 仍需普通桌面会话验证；
- 真实 cost：usage 已有，正在按官方价目表计算。
