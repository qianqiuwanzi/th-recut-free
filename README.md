# th-recut-free · 口播视频本地体检

[![GitHub stars](https://img.shields.io/github/stars/qianqiuwanzi/th-recut-free?style=flat-square&label=stars)](https://github.com/qianqiuwanzi/th-recut-free)
[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-cool.qianshi%2Fth--recut--mcp-4F7DFC?style=flat-square&logo=modelcontextprotocol)](https://registry.modelcontextprotocol.io/v0/servers?search=th-recut)
[![Glama](https://img.shields.io/badge/Glama-rated%20B%20%2F%20responding-4F7DFC?style=flat-square)](https://glama.ai/mcp/connectors/cool.qianshi/th-recut-mcp)
[![License](https://img.shields.io/badge/license-CC%20BY--NC%204.0-e5484d?style=flat-square)](./LICENSE)
[![线上体验页](https://img.shields.io/badge/%E7%BA%BF%E4%B8%8A%E4%BD%93%E9%AA%8C%E9%A1%B5-qianqiuwanzi.github.io-4F7DFC?style=flat-square)](https://qianqiuwanzi.github.io/th-recut-free/)

> 你录完口播视频，最烦的是不是：脸跑到画面边缘、字幕断句怪、卡片挡脸、音画不同步？
> 这个开源工具帮你在**本地**把这些坑一次性查出来——视频素材不出本机，永久免费。

**👉 不想自己搭渲染环境？直接[云端出片](https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting)（前 3 条免费、不水印）** ｜
**在线体验页** → <https://qianqiuwanzi.github.io/th-recut-free/>

| 我想… | 点这里 |
|---|---|
| 先看看效果、别装环境 | [线上体验页](https://qianqiuwanzi.github.io/th-recut-free/) |
| 下载离线跑 | [Releases v1.0.0](https://github.com/qianqiuwanzi/th-recut-free/releases/tag/v1.0.0) |
| 直接出片（不想自搭渲染） | [SkillHub · 前 3 条免费](https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting) |
| 接进 Claude Code / Cursor | [MCP 端点](#mcp)（见下方配置） |
| 商用 / 企业批量 | [联系我们](#联系) |

> ⭐ 如果这套体检脚本帮你省了时间，给个 star——它会让我知道该往哪加功能。

## 它解决什么

口播视频的"排版包装"里，最重复、最吃人力的不是"剪"，是**排版前的质检**：
人脸有没有越界、字幕断得合不合理、卡片会不会挡脸、音画对不对得上。
`th-recut-free` 把这几件事做成可本地运行的脚本，跑完给你一份清单。

## 适用谁

- 口播创作者 / 知识博主，日更或周更，自己剪或外包剪都行
- 想先"免费看质量"再决定要不要花钱出片的人

## 环境要求

- Python 3.8+
- [ffmpeg](https://ffmpeg.org/)（需在 PATH 中，脚本经它解码音视频）
- 可选依赖：`pip install -r requirements.txt`（numpy / opencv / jieba，按脚本需要安装；
  缺依赖时相关脚本会给出提示，其余脚本不受影响）

## 快速开始

```bash
# 一次性体检（推荐入口）
python scripts/doctor.py --video your.mp4
python scripts/doctor.py --video your.mp4 --src raw.mp4   # 有原始素材时附带增益/时序对比

# 或按需单独跑
python scripts/detect_faces.py --video your.mp4                       # 人脸禁区 / 显示画幅探测
python scripts/audio_qc.py --out your.mp4 --src raw.mp4               # 音频验收（削波/增益/时序）
python scripts/split_sentences.py --tokens tokens.json --out seg.json # 智能断句
```

不需要账号、不需要联网、视频不出本机。

## 检查项清单

| 脚本 | 检查什么 | 关键输入 |
|---|---|---|
| `doctor.py` | 一次性体检汇总（构图 + 音频） | `--video` |
| `detect_faces.py` | 人脸禁区 / 显示画幅安全区探测 | `--video` |
| `check_card_layout.py` | 卡片矩形是否遮挡人脸禁区 | 禁区 zones + 卡片 JSON |
| `check_pip.py` / `check_pip_audio.py` / `check_pip_coverage.py` | PIP 落位 / 声音 / 覆盖验收 | 成片 + 计划 JSON |
| `audio_qc.py` | 削波 / 增益 / 时序 | 成片 `--out` + 原始 `--src` |
| `audio_band_check.py` | 音频频段分布检查 | 音频 + 参考 |
| `check_audio_caption_sync.py` | 音频与字幕同步 | 音频 + 字幕 JSON |
| `split_sentences.py` | 智能断句（词边界不切断） | ChatCut tokens JSON |
| `clean_fillers.py` | 气口 / 口头禅清理建议 | 文本 + 时间轴 |
| `make_pip_source.py` | 人脸中心正方形裁切源生成 | 源视频 |
| `measure_zoom.py` | 缩放测量 | 序列帧 |

每个脚本 `--help` 可看完整参数；输出为 JSON 或人读报告，含 `verdict: PASS/FAIL`。

## 输出什么（样例）

`doctor.py` 跑完会得到一张表，类似：

| 检查项 | 结果 | 说明 |
|---|---|---|
| 人脸禁区 / 显示画幅 | ✅ PASS | 探测到 2 个显示画幅安全区 |
| 音频验收（削波/增益/时序） | ✅ PASS | 无削波 |

## 免费 vs 付费

| | 本地体检（本仓库，免费） | 云端出片（SkillHub Pay Skill） |
|---|---|---|
| 人脸禁区 / 断句 / 构图 / 音画 | ✅ 查出来 | ✅ 查出来并自动避让布点 |
| 产出"编辑剧本" | ✅ 预览 | ✅ 预览 + 渲染成片 |
| 出片渲染 | ❌ 需自备剪辑工具 | ✅ 前 3 条免费，之后 ¥2.90/条 |

### 👉 一键直达云端出片

<p align="center">
  <a href="https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting">
    <img src="https://img.shields.io/badge/%E2%9C%85%20%E7%9B%B4%E6%8E%A5%E5%87%BA%E7%89%87-skillhub.cn-4F7DFC?style=for-the-badge" alt="直达云端出片">
  </a>
</p>

**云端出片（Pay Skill）** → <https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting>

- 装上就能用：SkillHub 上「口播视频自动剪辑」，评分 4.6，创作者为**千视科技（深圳）有限公司**
- **前 3 条完整出片免费、不水印**；超出 **¥2.90/条**（微信支付，费用直接付给创作者）
- 和本仓库的关系：本仓库是**免费的本地体检层**，云端是**渲染层**。体检结果直接喂给云端出片，不用重跑
- 不想装 SkillHub？用下面的 **MCP 端点**直接接进 Claude Code / Cursor / Cline

> 两条付费入口同源（同一个收款主体、同一个计费口径），额度也共享。

## 云端 MCP（给开发者 / 智能体）

除了在 SkillHub 对话里用，云端出片能力也开放为标准 **MCP（Model Context Protocol）** 服务，
任何支持自定义 MCP 的客户端（Claude Code / Cursor / Cline / Glama 等）都能直接调用：

- **端点**：`https://www.qianshi.cool/thapi/mcp`（Streamable HTTP + JSON-RPC 2.0）
- **鉴权**：`Authorization: Bearer <平台颁发的 API Key>`
- **3 个工具**：`generate_recut_plan`（生成安全排版出片计划）→ `render_recut_video`（提交渲染）
  → `get_recut_job`（轮询拿成片）

连接示例（Claude Code / Cursor 的 `.mcp.json`）：

```json
{
  "mcpServers": {
    "th-recut": {
      "type": "http",
      "url": "https://www.qianshi.cool/thapi/mcp",
      "headers": { "Authorization": "Bearer ${TH_RECUT_API_KEY}" }
    }
  }
}
```

> MCP 接入需平台颁发的 API Key（按次计费，前 3 条免费），与 SkillHub 付费链路同源。

## FAQ

**Q：视频会上传吗？**
不会。所有脚本只读你本地文件，经本机 ffmpeg 解码，无任何网络请求。

**Q：为什么某些脚本要 numpy / opencv / jieba？**
频谱与画面分析用 numpy / opencv，中文分词用 jieba。都是可选依赖，
装了功能完整，不装则相关脚本不可用，其余脚本照常。

**Q：能直接帮我剪好/出片吗？**
本仓库只做"体检"和"预览"。要成片，点这里直接用云端出片：
<https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting>（前 3 条免费），
或自备剪辑工具按体检报告手工处理。

**Q：可以商用吗？**
本仓库以 CC BY-NC 4.0 发布，禁止商用。商用请走云端出片付费链路
（<https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting>）或联系作者
（105920869@qq.com / 千视科技（深圳）有限公司）。

## 联系

| 场景 | 渠道 |
|---|---|
| 个人使用 / 试用 | 直接用，无需联系 |
| 商用 / 批量出片 | [SkillHub 云端出片](https://skillhub.cn/skills/org-teo57mtr/talking-video-autocutting)（¥2.90/条，前 3 条免费） |
| 企业定制 / 私有部署 / 批量授权 | 105920869@qq.com（千视科技（深圳）有限公司） |
| 功能建议 / 报错 | [开 Issue](https://github.com/qianqiuwanzi/th-recut-free/issues/new) |

## 合规与边界

- 本仓库仅含本地质检脚本，**不含**任何服务端 / 计费 / 渲染组件。
- 渲染依赖你自备的剪辑工具（如 ChatCut），本工具不代管任何第三方账号凭证。
- 许可：CC BY-NC 4.0（禁止商用，如需商用请联系作者）。
