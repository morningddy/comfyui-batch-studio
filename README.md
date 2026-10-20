# ComfyUI Batch Studio · 批量生图与视频工具箱

一套面向 **ComfyUI** 的批量生产力工具：把「一堆素材 + 一堆提示词」变成「一堆成品」。
无需安装任何 Python 包（纯标准库），支持本地 ComfyUI 与云端 API 两条通道，双击 `.bat` 即可运行。

> 适合：批量线稿上色、批量风格转换、批量抽卡、批量分镜生图、批量视频处理。

---

## 为什么用它

ComfyUI 界面适合调一张图，不适合跑一百张。手动重复「载入图 → 改提示词 → 排队 → 保存」极其枯燥，而 ComfyUI 自带的文件夹批量节点又有两个硬伤：所有素材共用一条提示词、整批加载容易爆显存。

本工具解决这两个问题：**每张素材可以有自己的提示词、自己的种子、自己的重绘幅度**，并且串行执行不乱占显存，中断可续跑。

---

## 四条通道

| 通道 | 入口 | 适合场景 | 是否需素材 |
|---|---|---|---|
| **文生图** | `文生图批量.bat` | 纯提示词批量产出 | 否 |
| **图生图** | `图生图批量.bat` | 素材逐条改风格 | 是 |
| **API 并行** | `开始批处理.bat → 4` | 追求速度、不想占显存 | 否（也支持参考图） |
| **视频批量** | `开始批处理.bat → 3` | 视频逐条处理 | 是 |

---

## 快速开始

### 1. 克隆并准备目录

```bash
git clone https://github.com/morningddy/comfyui-batch-studio.git
cd comfyui-batch-studio/batch_comfy
```

确保 ComfyUI 已启动（默认端口 8188，脚本会自动探测 8188 / 8000 / 8189）。

### 2. 准备素材

把图片放进 `批量素材/01_图片素材/`，视频放进 `批量素材/02_视频素材/`。

### 3. 生成任务表

双击 `批量素材/生成任务表.bat`（或点主菜单里的「重新生成任务表」）。

脚本会扫描素材目录，生成 `任务表.csv`，每行一个素材，路径自动填好。

### 4. 填提示词

用 Excel / WPS 打开 `任务表.csv`，给每行写 `prompt`：

```csv
source,prompt,negative,seed,denoise,prefix
D:/素材/lh_001.png,线稿上色 日系动画赛璐璐 柔和顺光,模糊 低质量,,0.55,lh001
D:/素材/lh_002.png,水墨淡彩 宣纸质感 留白构图,模糊 低质量,,0.5,lh002
```

### 5. 开跑

双击 `开始批处理.bat`，按菜单选。

---

## 任务表字段

| 列 | 必填 | 说明 |
|---|---|---|
| `source` | 图生图/视频必填 | 素材绝对路径。也认 `素材`/`文件`/`路径` |
| `prompt` | 是 | 正向提示词。也认 `提示词` |
| `negative` | 否 | 反向提示词。也认 `反向` |
| `seed` | 否 | 留空=随机；填固定值可复现 |
| `denoise` | 否 | 重绘幅度，图生图核心参数 |
| `steps` `cfg` `width` `height` | 否 | 覆盖工作流默认值 |
| `prefix` | 否 | 输出文件名，留空用素材名 |

列名大小写、中英文都识别。CSV 支持 UTF-8 与 GBK（WPS 默认保存的 ANSI 编码）自动识别。

---

## denoise 取值参考

图生图的效果几乎全靠这个参数：

| denoise | 效果 |
|---|---|
| 0.35 ~ 0.45 | 轻微修饰，原图特征基本保留 |
| **0.55 ~ 0.65** | 换画风但保留构图与主体（推荐起点） |
| 0.70 ~ 0.80 | 只借构图，内容大幅改动 |
| > 0.85 | 几乎等于重画，原图失去约束 |

---

## 换成自己的工作流

工具不认死节点 ID，而是按**节点类型**自动定位注入点：

- `LoadImage` / `LoadVideo*` / `VHS_LoadVideo*` → 接素材
- 顺着采样器的 `positive` / `negative` 往上游找文本编码节点
- `Empty*Latent*` → 宽高
- `SaveImage` / `VHS_VideoCombine` / `SaveAnimated*` → 输出前缀

兼容 `KSampler`、`KSamplerAdvanced`、`SamplerCustomAdvanced + CFGGuider`、Flux 的 `BasicGuider`。

**做法**：在 ComfyUI 里调好你的工作流 → `工作流 → 导出 (API)` → 存进 `workflows/` → 用 `--inspect` 检查识别结果。

```bash
python batch_runner.py --workflow workflows/your_flow.api.json --inspect
```

输出示例：

```
节点映射：
  20   LoadImage        ← 素材图
  5    CLIPTextEncode   ← 正向提示词
  6    CLIPTextEncode   ← 反向提示词
  2    KSampler         ← 种子
  10   SaveImage        ← 输出前缀
```

自动识别不准时两个手动开关兜底：

```bash
--map 12.text=prompt        # 把 CSV 的 prompt 列塞进 12 号节点的 text
--set 2.denoise=0.75        # 固定修改某个值
```

---

## API 并行通道

不想占显存、或者本机跑太慢时用这条。并发执行，速度取决于服务商限流而不是你的硬件。

### 配置

```bash
cp batch_comfy/api_config.example.json batch_comfy/api_config.json
```

填入三项：

```json
{
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-your-key",
  "model": "gpt-image-1"
}
```

`api_config.json` 已在 `.gitignore` 中，不会被提交。

### 支持协议

| 协议 | 适用 | 自动识别 |
|---|---|---|
| `openai` | OpenAI、多数中转站 | base_url 含 `/v1`、模型名非 gemini |
| `gemini` | Gemini 图像、nano-banana | base_url 含 generativelanguage、模型名 gemini |

`type` 填 `auto` 自动判断，也可强制指定。

### 使用

```bash
# 先验证配置能否连通（会生成一张测试图）
python api_batch.py --check --out ./api_test

# 并发 8 跑全量
python api_batch.py --tasks 任务表.csv --out ./out --workers 8

# 图生图（source 列当参考图，需接口支持 /images/edits）
python api_batch.py --tasks 任务表.csv --out ./out --edit
```

**尺寸自动适配**：gpt-image 系列只接受 `1024x1024` / `1536x1024` / `1024x1536`，脚本会把任意尺寸映射到最接近的一种，避免 400 报错。

---

## 命令行参考

### batch_runner.py（本地 ComfyUI）

```bash
# 文件夹 + 统一提示词
python batch_runner.py --workflow workflows/z-image-i2i.api.json \
  --folder "D:/素材" --prompt "线稿上色" --out "D:/输出"

# CSV 逐条（每个素材独立提示词）
python batch_runner.py --workflow workflows/z-image-i2i.api.json \
  --tasks tasks.csv --out "D:/输出"

# 提示词写在素材旁的同名 .txt
python batch_runner.py --workflow ... --folder "D:/素材" --prompt-from-txt --out "D:/输出"

# 视频
python batch_runner.py --workflow workflows/video.api.json \
  --folder "D:/视频" --kind video --out "D:/输出" --timeout 3600
```

常用参数：

```
--limit 5            只跑前 5 条
--retry 2            失败重试次数
--skip-done          断点续跑（默认开启）
--no-skip-done       全部重跑
--recursive          递归子文件夹
--dry-run            只解析不提交
--server URL         手动指定 ComfyUI 地址
--workers N          并发数（仅 API 通道）
```

---

## 工作原理

1. 读 API 格式工作流 JSON，按节点类型定位注入点
2. 素材经 `/upload/image` 上传到 ComfyUI 的 input 目录（带哈希后缀防重名）
3. 逐条改写提示词、种子、尺寸、denoise、输出前缀后提交 `/prompt`
4. 轮询 `/history/{id}` 等待完成，从 `/view` 下载结果并用 `prefix` 重命名
5. 每条的完成状态写入 `_state.json`，支持断点续跑

API 通道流程相同，只是把「提交到 ComfyUI」换成「调用图像接口并解码 base64 / 下载 URL」。

---

## 常见问题

**连不上 ComfyUI**
先确认界面已启动。Desktop 版端口可能是 8000，脚本自动探测，也可 `--server http://127.0.0.1:8000` 指定。

**任务表提示编码错误**
用 WPS 另存为 UTF-8 CSV。脚本已兼容 GBK，若仍失败多半是文件被占用。

**提示词节点没认出来**
用 `--inspect` 看节点 ID，然后 `--map 节点ID.text=prompt`。

**.bat 双击闪退**
用命令行运行 `python menu.py` 看具体报错。

**想重跑某几条**
改那几行的提示词或 denoise，或删掉 `_state.json` 里对应条目。

---

## 附带的两个示例工作流

`workflows/` 下两个基于 z-image 的 SVG 格式工作流可直接使用（需自行准备模型）：

- `z-image-t2i.api.json` — 文生图
- `z-image-i2i.api.json` — 图生图（VAEEncode 接入素材）

用其它模型时替换其中的 `unet_name`、`clip_name`、`vae_name` 即可。

---

## 许可

MIT
