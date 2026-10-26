# astrbot_plugin_sing

让 AstrBot 里的角色**用她自己的声音唱歌**：说一句"想听你唱歌"，它翻唱整首，先发 mp3 文件，再发一段副歌语音条。

## 效果

```
你: 想听你唱歌
她: 想听啥歌呀

你: 雨爱
她: 等我一下
    （2~4 分钟）
她: [文件: 雨爱_原版.mp3]        ← 先给原版
她: 整首也给你
她: [文件: 雨爱_她唱的.mp3]      ← 整首翻唱
她: [语音 45 秒]                  ← 副歌高潮段
```

台词不是写死的，由模型按角色人设现生成（失败才回落到默认短句池）。

## 它由三部分组成

| 部分 | 作用 |
|---|---|
| 插件本体 | 识别触发词、问歌名、调后端、发文件/语音 |
| `backend/cover.py` | 搜歌 → 下载 → 分离人声 → RVC 换嗓 → 混音 → 截副歌 |
| RVC 环境 | 需要单独一个 venv（torch + fairseq + rvc-python + demucs） |

## 安装

### 1. 插件

把整个目录放进 `AstrBot/data/plugins/`，重启 AstrBot。

### 2. RVC 环境

强烈建议单独建 venv（Python 3.10~3.12）：

```bash
python -m venv rvcenv
rvcenv\Scripts\pip install -r backend/requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

> Windows 上 `fairseq` 没有官方轮子（官方只到 cp38 / mac / linux）。
> 可以找第三方编译版，例如 `gdiaz384/fairseq` 的 release 里有 cp310/cp311/cp312 的 win_amd64 轮子。

还需要 **hubert 和 rmvpe** 两个预训练文件，放到 `rvc-python` 包的 `base_model/` 里：
`hubert_base.pt`、`rmvpe.pt`、`rmvpe.onnx`（hf-mirror.com 上 `Daswer123/RVC_Base` 可以下）。

### 3. 放 RVC 模型

把训练好的 `.pth` 和 `.index` 放到纯英文路径（faiss 读不了中文路径），然后在插件配置里填：

- `rvc_python` → 那个 venv 的 python.exe
- `rvc_model` / `rvc_index` → 模型路径
- `ffmpeg_path` → ffmpeg 路径（没有的话装 `imageio-ffmpeg`）

### 4. 音源（可选）

QQ 音乐 VIP 的歌需要 cookie。在 `backend/` 下建 `qqmusic.json`：

```json
{"uin": "你的QQ号", "qm_keyst": "从 y.qq.com 复制的值"}
```

**这个文件不要提交到仓库**（已在 .gitignore 里）。没有它也能用，只是只能走网易云免费源。

## 配置项

| 键 | 说明 |
|---|---|
| `rvc_python` | RVC 环境的 python.exe |
| `rvc_model` / `rvc_index` | RVC 模型与索引 |
| `ffmpeg_path` | ffmpeg |
| `cache_dir` | 结果缓存目录（同一首歌第二次秒回） |
| `clip_seconds` | 副歌长度，默认 45 |
| `f0up_key` | 升降调，男声原唱可以 +2 |
| `index_rate` | 相似度 0~1，默认 0.75 |
| `protect` | 清辅音保护，默认 0.33 |

## 搜歌逻辑

QQ 音乐和网易云**同时搜**，按"歌名是否完整出现在查询里 + 歌手是否对上"打分，分高的先试。下载后四道校验：

1. 无结果
2. 无播放地址（VIP/版权限制）
3. 文件 < 300 KB（版权提示页）
4. 时长 < 期望的 60%（30 秒试听片段）

任何一道不过就换下一个候选。

## 已知限制

- **副歌是粗估的**：从第 55 秒开始截（短歌按比例），不是智能识别人声高潮
- **整首换嗓很慢**：4 分钟的歌大约 2~3 分钟
- **男声原唱容易失真**：音域差太多时调 `f0up_key`，或换女声原唱
- 需要 GPU；显存不够时 RVC 会失败

## License

MIT
