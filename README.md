# Learning Workbench / All-in-One 学习工作台

状态：MVP 垂直切片已可运行，已进入真实课程体验迭代。

## 演示视频

https://github.com/user-attachments/assets/304a9605-798c-42ce-8d7d-aa1232f99abf

[下载 42 秒产品演示](https://github.com/baigao417/learning-workbench/releases/download/demo-v5/learning-workbench-demo-v5.mp4)

竖屏双语演示：课前简介与思维导图、同步逐字稿、截图与时间锚点、课后复习、AI 今日作业。使用公有领域教学片与演示数据，不包含个人课程或笔记。

## 使命

把一门真实课程从素材进入，一直连接到理解、记录、训练、复盘和知识沉淀，减少播放器、笔记、截图、AI 对话和知识库之间的上下文搬运。

## 核心闭环

```text
导入课程/材料
→ 逐字稿和时间戳
→ 预览、线性笔记、思维导图
→ 播放、阅读、修改、批注、记录灵感
→ 时间戳和截图
→ 当天训练任务
→ 训练结果
→ 知识重组和关系更新
→ 下一次更精准的学习计划
```

## 第一版边界

第一版只服务一门真实课程，优先验证完整链路，不建设泛化平台。

包含：本地课程导入、逐字稿/时间戳、OpenCC 简体中文显示层、本机模型生成的课次标题与内容简介、来源可追溯且带时间锚点的 XMind 式图形思维导图、自定义本地播放器、HEVC 自动生成 H.264 本机兼容缓存、逐字稿随播放自动高亮和推进、播放器与笔记联动、时间锚点、备注/批注、备注回看、跨课搜索、当天训练任务、训练结果、摩擦记录，以及工作台内的意见反馈。

暂不包含：多学科通用知识图谱、多平台同步、社交和排行榜、复杂推荐、大规模外部 MCP 接入、自动判定掌握、全量课程批处理和未经验证的自动跳课。

## 设计原则

遵循“闭环产品设计”与“主任务优先”两条原则：工具服务于真实学习，不让工具建设替代学习本身。

## 当前下一步

第一轮试验用一门真实的本地视频课程完成，具体目标、验收和交付门禁见 `docs/02-pilot-contract.md`。

已完成一条最小可用学习链路、层级结构视图、跨课搜索/备注回看和意见反馈切片。下一步先收集真实使用反馈；未形成更充分的真实学习证据前，不扩大到多领域或复杂自动化。

## 本地启动

最简单的方式（Windows）：先把课程文件夹路径写进 `.local/start-source.txt`（只有一行，例如 `D:\courses\python-basics`；`.local/` 不进入 Git），然后双击项目根目录的 `start.cmd`。它会自动登记该课程（如果还没有登记）、启动本地服务，并打开浏览器工作台。关闭启动窗口即可停止服务；启动失败时窗口会保留具体错误。

也可以在项目根目录执行：

```powershell
python -m learning_workbench start --source '<课程目录>'
```

切换课程时，可以给启动脚本传入目录：

```powershell
.\start.cmd -Source 'D:\courses\another-course'
```

如果 `8765` 端口已被占用，一键启动会自动选择一个可用端口；需要排错或手动控制时，再使用下面的底层命令。

首次准备课程清单：

```powershell
python -m learning_workbench ingest --source '<课程目录>' --output '.local/state/manifest.json'
```

启动工作区（manifest 不存在时也可以自动登记）：

```powershell
python -m learning_workbench serve --source '<课程目录>' --state '.local/state' --port 8765
```

打开 `http://127.0.0.1:8765`。工作台会先检查本地视频编码；浏览器不能直接播放的 HEVC 视频会在首次打开时由本机 ffmpeg 转为 H.264，并缓存到 `.local/state/media/`。原视频保持不变，媒体和缓存都不会进入 GitHub。

## 单课学习工作区

- 左侧课程目录可收起，窄屏时以抽屉打开；选择课次后保留上次课次和视图状态。
- 每节课右侧有“生成”按钮：工作台会为该课启动一个受控的 Codex CLI 后台任务，只补齐缺失的逐字稿和思维导图；目录会持续显示等待、运行、失败或完成状态，失败后可原位重试。
- 默认学习现场将本地视频与同步逐字稿并排，课次学习文档可独立收起。
- 工作台使用低饱和蓝灰阅读主题；中等桌面宽度展开目录时，视频与逐字稿改为上下布局，手机保留“今日练习”入口。
- 每课播放位置、倍速与静音保存在当前浏览器；重新打开时恢复位置并等待点击播放。播放完毕后下次从头开始，不据观看进度判定掌握。
- 导图全屏提供明确的退出按钮，进入和退出时重新适应窗口。
- “结构浏览”提供宽画布、适应窗口、缩放、拖拽平移、节点聚焦和全屏查看。
- 每节课对应一份本地 Markdown 学习文档，输入后自动保存到 `.local/state/note-documents.json`，采用统一的所见即所得编辑区，点击正文即可编辑，无需切换阅读模式。支持标题、加粗、斜体、列表、引用、时间标签和截图。
- `Alt+A` 只把可点击的当前视频时间写入文档；`Alt+S` 截取当前帧并把图片与回看时间写入文档。快捷键按物理按键识别；编辑器内也可用 `Ctrl+Enter` 插入时间、`Ctrl+Shift+Enter` 截图。统一编辑器中移除截图不会立即删除本机图片，因此自动保存后仍可撤销恢复；未引用图片暂时保留，不修改原视频。
- 学习文档是时间、截图和个人笔记的唯一可见记录，不再展示独立的旧记录流。
- 使用反馈只保留课程目录里的“给工作台提意见”，可在待处理与已完成之间切换、恢复或经确认后删除，不在学习文档旁重复提供“记录摩擦”。
- 快捷入口：`Alt+N` 打开文档，`Alt+A` 插入当前时间，`Alt+S` 截图并插入，`Alt+C` 展开/收起课程目录，`Alt+M` / `Alt+L` 切换结构/学习视图。

本地转录一节课并生成来源锚定的预览/线性笔记/结构视图：

```powershell
python scripts/transcribe.py --manifest '.local/state/manifest.json' --lesson-id '<lesson-id>' --model large-v3 --language zh --device cpu --compute-type int8
```

`.local/` 中的 manifest、转录和运行记录均为本地生成物，不进入 GitHub。

默认逐字稿使用本机 `faster-whisper large-v3` 中文质量档：VAD、beam search、通用中文提示词（专业课程可用 `--initial-prompt` / `--hotwords` 传入领域术语），并关闭长文本 previous-text conditioning 以减少错误级联。旧稿重转时会先备份到 `.local/state/transcripts/versions/<lesson-id>/`，再原子替换；原始视频保持不变。

重转已有课次（会保留旧稿版本）：

```powershell
python scripts/transcribe.py --manifest '.local/state/manifest.json' --lesson-id '<lesson-id>' --force --model large-v3 --language zh --device cpu --compute-type int8
```

把旧 `base` 稿批量升级到当前质量档，并支持中断后继续：

```powershell
python scripts/transcribe.py --manifest '.local/state/manifest.json' --all --upgrade --model large-v3 --language zh --device cpu --compute-type int8
```

`faster-whisper` 是本地可选依赖；环境准备时安装项目的 ASR extra：

```powershell
python -m pip install -e ".[asr]"
```

目录中的“生成”按钮要求本机 `codex` 命令可用。浏览器不会传入命令或自由提示词；服务端只接受当前 manifest 中的课次 ID 和固定生成动作，并在 Codex CLI 返回后再次核验本地生成文件。

为已完成转录的课次生成合适标题、内容简介和语义思维导图（使用本机 Ollama，不上传课程内容）：

```powershell
python scripts/generate_introductions.py --state .local/state --model qwen3:4b
```

## 统一笔记编辑器

- 直接点击笔记输入，截图和时间标签保持可见；格式栏支持正文/标题/引用、加粗、斜体和列表。
- Ctrl+Z 撤销、Ctrl+Shift+Z 或 Ctrl+Y 重做，历史按课次隔离，刷新或切课后重新开始（最多 100 个编辑状态）。
- 粘贴按纯文本处理；旧 Markdown 继续保存在本机，未编辑时不会自动改写旧内容。
- 旧版 `annotations.json` 中的笔记、时间锚点和学习摩擦会接续显示在同一文档的“早期学习记录”中，也能搜索。读取不改写文件；编辑保存后记录已接续的条目，防止重复或删除后再次出现，原始记录文件仍保留。
- 切课会等到最新一版内容保存成功；等待期间继续输入的文字也会保存。保存失败则保留当前课次和未保存内容。
- 输入法组合期间不保存半成品；切课会等待组合完成。尚未保存时离开页面会显示浏览器提示。
- 这是面向学习笔记的轻量统一编辑器，不包含 Notion 数据库、协作或复杂块拖拽。

## 今日作业与笔记来源

- “今日练习”通过 Codex CLI 的只读通道，根据本课带时间的逐字稿、简介、导图和已保存的个人笔记布置回忆、应用、输出各一项作业；等待期间显示生成状态，失败可重试，同课当天只生成一组，不回落到固定课程模板。
- 作业保留 AI 来源、生成时间和视频锚点，完成后仍可填写结果；旧训练记录继续显示。
- 学习文档读时区分个人文字、自动插入的时间/截图、早期接续记录和带 AI 标题的内容。“只看我写的”是只读复习视图，不迁移或改写已保存的 Markdown；要编辑请切回“全部”。
- 页头课程名默认取课程文件夹名；也可以在 `.local/state/manifest.json` 中设置 `course_title` 自定义。

## 隐私

课程视频、逐字稿、笔记、截图和训练记录保存在本机 `.local/` 或你指定的课程目录，不随仓库发布。AI 相关功能（材料生成、今日作业）会将所需的课程文本和个人笔记交给本机已配置的 Codex CLI 处理；本机启动不等于完全离线，相关文本可能发送到该 CLI 配置的模型服务。请先确认其配置和数据处理方式，再对个人或受版权保护的材料使用 AI 功能。导图与简介也可以使用本机 Ollama。

## 许可证

[MIT](LICENSE)
