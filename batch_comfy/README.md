# ComfyUI 素材批量处理（批处理）

一个文件夹的素材 + 你的提示词 → 自动一条条丢进 ComfyUI 跑完，结果按素材名落到输出目录。

不装任何 Python 包（纯标准库），不占用 ComfyUI 界面的队列。

---

## 一、三条路线，先选一条

| 路线 | 适合场景 | 提示词 | 要不要写代码 |
|---|---|---|---|
| **A. 本脚本（推荐）** | 每个素材想用**不同**提示词 / 要断点续跑 / 要留记录 | 每个素材一条 | 复制改一行命令 |
| **B. KJNodes 文件夹节点** | 所有素材**同一条**提示词，一次性吃进去 | 只有一条 | 零代码，界面里连一下 |
| **C. ComfyUI 队列批次数** | 同一条提示词、只跑 N 张 | 只有一条 | 零代码 |

B 的节点你已装好：`KJNodes/misc → Load Images From Folder KJ`（填绝对路径，输出整批 IMAGE），视频用 `Load Videos From Folder`（同样填绝对路径）。
缺点是整批一起进显存，几十张容易爆显存；且没法一张一个提示词。

---

## 二、快速开始（3 步）

### 1. 打开 ComfyUI，把工作流导出成 API 格式

界面右上 **工作流 → 导出 (API)**，存到 `batch_comfy/workflows/` 下，比如 `my_flow.api.json`。

> 必须是 API 格式。界面格式（带 `nodes`/`links` 的那种）本脚本会直接报错提醒你重导。

已经给你备好两个现成的（基于你本地的 z-image + qwen_3_4b + ae.safetensors）：

- `workflows/z-image-t2i.api.json` — 文生图
- `workflows/z-image-i2i.api.json` — 图生图（素材图 → VAEEncode → 采样，`denoise` 控制改动幅度）

### 2. 先体检：看脚本能不能认出节点

```bash
cd batch_comfy
python batch_runner.py --workflow workflows/z-image-i2i.api.json --inspect
```

正常输出长这样（自动标出哪个节点吃什么）：

```
节点映射：
  20   LoadImage        ← 素材图
  5    CLIPTextEncode   ← 正向提示词
  6    CLIPTextEncode   ← 反向提示词
  2    KSampler         ← 种子
  10   SaveImage        ← 输出前缀
```

### 3. 开跑

**方式一：一个文件夹 + 一条提示词**

```bash
python batch_runner.py --workflow workflows/z-image-i2i.api.json ^
  --folder "D:/素材/线稿" ^
  --prompt "线稿上色，日系动画赛璐璐风格，柔和顺光，浅景深" ^
  --out "D:/输出/上色_第一批"
```

**方式二：每个素材自己的提示词（CSV）**

复制 `tasks.example.csv`，按行填：

```csv
source,prompt,negative,seed,denoise,prefix
D:/素材/线稿/lh_001.png,线稿上色，日系赛璐璐,模糊，低质量，水印,123456,0.55,lh001_color
D:/素材/线稿/lh_002.png,线稿上色，90年代复古动画色调,模糊，低质量,  ,0.65,lh002_color
```

```bash
python batch_runner.py --workflow workflows/z-image-i2i.api.json ^
  --tasks tasks.csv --out "D:/输出/批量"
```

**方式三：提示词写在素材旁边**

`lh_001.png` 旁边放 `lh_001.txt`，然后：

```bash
python batch_runner.py --workflow workflows/z-image-i2i.api.json ^
  --folder "D:/素材/线稿" --prompt-from-txt --out "D:/输出/批量"
```

想先看会发生什么而不真跑：加 `--dry-run`。

---

## 三、CSV 列说明

| 列 | 必填 | 说明 |
|---|---|---|
| `source` | 图生图/视频必填 | 素材绝对路径。列名也可写 `素材`/`文件`/`路径` |
| `prompt` | 是 | 正向提示词。也可写 `提示词` |
| `negative` | 否 | 反向提示词。也可写 `反向` |
| `seed` | 否 | 留空=每次随机。填了可复现 |
| `denoise` | 否 | 重绘幅度（图生图重点），0.45~0.75 常用 |
| `steps` `cfg` `width` `height` | 否 | 覆盖工作流默认值 |
| `prefix` | 否 | 输出文件名前缀，留空用素材名 |

列名大小写、中英文都认。多出来的列可以用 `--map 节点ID.输入=列名` 强行接到任意节点上。

---

## 四、换成你自己的（复杂）工作流

脚本不认死节点 ID，而是按类型自动找：

- `LoadImage` → 接素材图；`LoadVideo*` / `VHS_LoadVideo*` → 接素材视频
- 顺着采样器的 `positive` / `negative` 往上游找文本节点（支持 `KSampler`、`KSamplerAdvanced`、`SamplerCustomAdvanced` + `CFGGuider`、Flux 的 `BasicGuider`）
- `Empty*Latent*` → 宽度/高度
- `SaveImage` / `VHS_VideoCombine` / `SaveAnimated*` → 输出前缀

自动认错的时候，两个手动开关兜底：

```bash
--map 12.text=prompt        # 把 CSV 的 prompt 列塞进 12 号节点的 text
--set 2.denoise=0.75        # 固定改某个值
```

---

## 五、跑视频

```bash
python batch_runner.py --workflow workflows/my_h3.api.json ^
  --folder "D:/素材/视频" --kind video ^
  --prompt "..." --out "D:/输出/视频批量" --timeout 3600
```

要点：
- 工作流的加载节点用 `VHS Load Video`，导出 API 后脚本会自动把素材路径塞进它的 `video` 输入
- 输出节点用 `VHS Video Combine`，脚本会通过 `/view` 把成片下回来（mp4/webm 都行）
- 视频慢，`--timeout` 给足（默认 1800 秒）

---

## 六、常用参数

```
--limit 5            先跑 5 条试水
--retry 2            失败重试 2 次
--skip-done          断点续跑（默认开，靠输出目录里的 _state.json）
--no-skip-done       全部重跑
--recursive          连子文件夹一起扫
--server http://127.0.0.1:8000    ComfyUI Desktop 如果是 8000 端口
--comfy-root /path/to/ComfyUI  配合 --local-copy 手动指定 input 所在目录
--dry-run            只解析不提交，先看会跑哪些、参数对不对
```

---

## 七、踩坑提醒

1. **连不上** —— 先确认 ComfyUI 界面已启动；Desktop 版默认端口可能是 8000，脚本会自动探 8188 → 8000 → 8189，都不行再 `--server` 指定。
2. **素材怎么进去的** —— 走 ComfyUI 的 `/upload/image` 接口上传到 `input/_batch/`，所以**不用管你是 Desktop 版还是便携版**，也不用填路径。素材文件名会自动加 8 位哈希后缀，中文名不会冲突。
3. **提示词节点没认出来** —— 用 `--map`，`--inspect` 会先列出所有节点 ID。
4. **显存不够** —— 脚本是**串行**跑，一条完了再下一条，不会叠显存。素材图太大先在工作流里加缩放节点。
5. **想重跑已完成的** —— 删掉输出目录里的 `_state.json` 对应条目，或直接加 `--no-skip-done`。
6. **`--local-copy`** —— 服务器在另一台机器/远端时用不上，本机想跳过 HTTP 上传、直接拷文件时才加。Desktop 版的 input 目录脚本会自己从配置里读。

---

## 八、小显存提速参考

以 z-image（768×1024）为例，量化精度和步数对速度影响最大：

| 配置 | 相对耗时 | 画质 |
|---|---|---|
| bf16 默认 + 50 步 | 基准 ×3 | 最好 |
| **fp8_e4m3fn_fast + 16 步（推荐默认）** | **基准** | 接近原版 |
| fp8_e4m3fn_fast + 8 步 | 快约 30% | 细节偏软，试水够用 |

要点：`weight_dtype` 用 `fp8_e4m3fn_fast` 后模型体积减半，第二条起不再整块重载；批量时**头一张慢（含加载），后面每张都快**。CSV 里用 `steps` 列可逐条覆盖。

---

## 九、API 并行生图（不占显存，速度快）

### 1. 填配置

打开 `batch_comfy\api_config.json`，填三行：

```json
{
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-xxxxxx",
  "model": "gpt-image-1",
  "type": "auto"
}
```

`type` 留 `auto` 会自动判断协议，支持两类：

| 协议 | 适用 | 自动识别依据 |
|---|---|---|
| `openai` | OpenAI、绝大多数中转站 | base_url 含 `/v1`、模型名非 gemini |
| `gemini` | Google Gemini 图像、nano-banana | base_url 含 generativelanguage、模型名 gemini |

### 2. 先体检

菜单选「4 → 1」，或命令行：

```bash
python api_batch.py --check --out "D:/输出/API测试"
```

会真的生成一张测试图，能出图就说明配置完全正确。

### 3. 批量跑

菜单选「4 → 3」（并发 5）。命令行：

```bash
python api_batch.py --tasks "任务表.csv" ^
  --out "D:/输出/API批量" --workers 8
```

**任务表和本地 ComfyUI 共用同一份**，不用另外准备。

### 4. 参数说明

```
--workers 8        并发数，默认 4。中转站限流就降到 3，官方 key 可以开到 10 以上
--limit 3          只跑前 3 条
--edit             把 source 列当参考图（图生图，需接口支持 /images/edits）
--no-skip-done     不跳过已完成的
```

### 5. 尺寸自动适配

gpt-image 系列只认 `1024x1024` / `1536x1024` / `1024x1536` 三种。脚本会自动把你写的任意尺寸映射到最接近的一种，不会因为尺寸 400 报错。想强制指定就在配置里填 `supported_sizes`。

### 6. 成本提醒

按张计费。先用 `--limit 3` 确认效果，再全量跑。便宜模型先试水，效果满意再换高端型号。

---

## 文件清单

```
batch_comfy/
├─ batch_runner.py                     本地 ComfyUI 执行器
├─ api_batch.py                        API 并行生图（OpenAI / Gemini 协议）
├─ api_config.json                     ← API 配置，填 base_url / api_key / model
├─ menu.py                             菜单逻辑（bat 只负责拉起它）
├─ make_tasks.py                       扫描素材文件夹生成任务表
├─ tasks.example.csv                   CSV 模板
├─ workflows/
│  ├─ z-image-t2i.api.json             文生图（fp8 + 16步）
│  └─ z-image-i2i.api.json             图生图（fp8 + 16步）
├─ 开始批处理.bat                     总菜单（文生图/图生图/视频/API）
├─ 文生图批量.bat                     直达文生图
├─ 图生图批量.bat                     直达图生图
└─ run_folder.bat / run_csv.bat       旧入口，等同图生图
```

> 所有 .bat 都是纯 ASCII + CRLF，只负责调 `menu.py`。中文菜单和路径全在 Python 里，避免 cmd 编码问题。
