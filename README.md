# cc-switch-thinking-filter

解决 CC Switch 本地代理把上游返回的**空 thinking 块**写入 Codex 会话历史、下次请求回传时被上游网关以 HTTP 400「thinking 长度不足」拒绝的问题。

> 问题详情与根因分析见 [farion1231/cc-switch issue #6260](https://github.com/farion1231/cc-switch/issues/6260)

## 背景

- CC Switch 本地代理（Anthropic 格式供应商，如 deepseek-v4-flash 系模型）流式返回时，某些轮次（尤其工具调用步骤）会下发 `{"type":"thinking","thinking":""}` 的空 thinking 块；
- 代理将其以 `ccswitch-anthropic-thinking-v1:<base64>` 形式写入 Codex 会话历史；
- 下次请求回传时，上游网关校验 thinking 最小长度，空 thinking 直接 400，且每次重试都因相同历史载荷失败，只能新开会话规避。

## 方案

在 Codex 与 CC Switch 之间加一层透明过滤代理：

```
Codex (15722) -> filter_proxy.py -> CC Switch (15721) -> 上游
```

- **请求侧**：剥离历史中"空/超短 thinking"的 reasoning 项（含 message content 内嵌项，兜底）；
- **响应侧**：SSE/JSON 输出中丢弃空 thinking reasoning（空 thinking 永不进入 Codex 历史）；
- 正常长度的思考内容原样保留。

## 文件

| 文件 | 说明 |
|---|---|
| `filter_proxy.py` | 过滤代理（纯 Python 标准库，无依赖） |
| `filter_watchdog.ps1` | 看门狗：代理不在线/崩溃自动拉起 + cc-switch 重启/切换供应商时自动钉回 base_url + 上游端口自适应 + 单实例锁（常驻） |
| `pin-codex-filter.ps1` | 按当前激活供应商钉住 base_url（基元律动类 -> 15722；其他 -> 15721） |
| `install-autostart.ps1` | 一键安装：复制脚本到 `%USERPROFILE%\cc-switch-fix` + 写启动文件夹 vbs + 启动看门狗 |
| `start-codex.ps1` | 手动启动器：确保代理在跑 + 重钉配置 + 启动 codex |
| `clean_empty_thinking.py` | 应急清洁：清除历史中已存在的空 thinking 记录 |
| `test_filter.py` | 离线测试（单测 + mock 上游集成测试） |

## 安装

```powershell
# 1. 一键安装自启动（复制脚本 + 登录自启 + 启动看门狗）
powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1

# 2. 钉住 base_url（基元律动类供应商 -> 15722；其他 -> 15721）
powershell -ExecutionPolicy Bypass -File .\pin-codex-filter.ps1

# 3. 重启 Codex
```

## 使用

- 正常启动 Codex 即可（代理开机自启、崩溃自愈、base_url 自动钉回）。
- 手动启动：`powershell -File .\start-codex.ps1`
- 查看日志：`%USERPROFILE%\cc-switch-fix\filter_proxy.log`
- 应急清洁（遇到 400 时）：`python clean_empty_thinking.py %USERPROFILE%\.codex\sessions`

## 自愈能力

`filter_watchdog.ps1` 常驻巡检（约 5 秒一轮）：

- **过滤器保活**：15722 端口不在线/崩溃时自动拉起 `filter_proxy.py`；
- **base_url 自动钉回**：cc-switch 重启或切换供应商会重写 `config.toml` 的 base_url，看门狗检测到 cc-switch 进程 PID 变化后立即执行 `pin-codex-filter.ps1`，并每轮轮询兜底，基元律动类供应商自动钉回 15722，无需手动干预；
- **上游端口自适应**：从 `cc-switch.db` 读取 codex 代理端口，端口变化后用新端口重启过滤器；
- **单实例锁**：原子锁文件（`filter_watchdog.lock`）保证只有一个看门狗实例。

## 参数

```text
python filter_proxy.py --listen 127.0.0.1:15722 --upstream 127.0.0.1:15721 --min-len 4 [--strip-all]
```

## 按供应商隔离

`pin-codex-filter.ps1` 读取 CC Switch 的 `currentProviderCodex`：

- 基元律动（Anthropic 格式，存在空 thinking 问题）-> base_url 钉到 15722（走过滤代理）；
- 其他供应商 -> base_url 保持 15721（直连 CC Switch，不经过滤代理）；
- 切换供应商后重启 Codex 生效；登录时自动执行。

注意：若在 CC Switch 中"删除后重建"供应商会生成新 UUID，需同步更新 pin 脚本中的供应商 ID（默认 ID：`c001b8e8-3ffb-416a-9495-ae6d5669e36f`）。

## 卸载

- 删除启动文件夹中的 `CCSwitchFilterProxy.vbs` / `CCSwitchCodexPin.vbs`；
- 停止 `filter_watchdog` 进程；
- 恢复 `~/.codex/config.toml` 的 base_url 指向 15721（或删除 pin 相关配置）。

## 许可证

MIT
