# GPX Point Studio

[简体中文](#简体中文) · [English](#english)

## 简体中文

一个本地运行的 GPX 地图 PNG 序列工具，带网页编辑和命令行导出。网页按 GPX 实际时间插值生成连续 PNG 序列；命令行保留每个原始 GPS 点位一张 PNG 的导出方式。

### 快速开始

需要 Python 3.10 或更高版本。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python render_gpx_png_sequence.py
```

默认自动打开 <http://127.0.0.1:8765>。`--port 9000` 可修改端口，`--no-browser` 可关闭自动打开。Windows 可使用 `.venv\Scripts\python` 和 `.venv\Scripts\pip`。服务仅监听本机。

### 网页功能

- 点击导入或将一个 `.gpx` 文件拖到页面任意位置（不设文件大小上限）；自动显示全部轨迹点，支持多个轨迹段及路线点。
- 点击轨迹点、点击地图选最近点，或拖动定位标记沿轨迹选点；原始坐标不会改变。
- 时间轴起点、终点、当前时间及刻度下方显示录制当地时间：优先使用 GPX 时间戳记录的 UTC 偏移（`Z` 表示 UTC），没有偏移时使用浏览器时区。无时区时间戳也按浏览器时区解析。
- 时间轴首尾控制裁剪范围，蓝色游标控制当前点；左右箭头逐点移动。
- 时间轴支持 1–32 倍缩放：使用加减按钮或滚轮，以鼠标位置为中心缩放；使用水平滚动条、触控板或 Shift + 滚轮平移。“显示完整时间轴”恢复全览。聚焦时间轴后可用 `+`、`-`、`0` 操作。
- 选择默认 OSM、CARTO Dark 或 Light 样式；支持地图缩放等级 1–19、宽高各 128–4096 像素。PNG 预览与导出共用同一后端渲染器。
- 时间轴用橙色标出记录空白：阈值为同段正采样间隔中位数的两倍且至少 60 秒；间隔达到阈值或轨迹段断开时标记为空白。导出设置中的“拟合轨迹”默认关闭，空白期间停留于前一点并断开轨迹线；开启后按时间线性补齐空白（包括段间空白），同步影响地图播放、PNG 预览和导出。空白时长仍保留。
- 线条显示开关、颜色和粗细，以及当前位置圆点的显示开关、直径、填充色、描边颜色和粗细可直接配置，同步应用于地图、PNG 预览和导出。
- 按 GPX 实际时间播放，播放倍率可选 10、20、50、100、200、500、1000、2000×；导入及调整播放范围时，自动选择能在 10 分钟内播完的最低档（尽量接近 10 分钟），超过上限时用 2000×。可手动切换，界面显示预计播放时长；在同一轨迹段的相邻点之间按实际时间线性插值，位置连续移动（空白及断段是否连接由“拟合轨迹”控制）。时间缺失或非递增时按点位顺序播放，1× 每秒一个点。播放倍率独立于导出倍率。
- 默认导出整个 GPX，可选时间轴裁剪范围。导出倍率支持 1–100× 滑条和数值输入（默认 10×），帧率可选 25、30、50、60（默认 30）。预计时长为原始时长 ÷ 导出倍率，PNG 张数为预计时长 × 帧率向上取整（最少一帧）。首尾均匀采样覆盖选中时段；序列实际时长有不足一帧的取整误差。例如 5 小时 / 5× = 1 小时，30 fps 输出 108,000 张 PNG。后端异步渲染，进度显示百分比、完成/总帧数、已用/预计剩余时间和 ZIP 打包阶段，完成后下载 ZIP：`frame_000001.png` 起的序列及包含坐标、时间和导出设置的 `manifest.json`。

### API key 与本地配置

`.env.example` 是可提交的空值模板；真实 key 写入项目根目录的 `.env`：

```dotenv
API_KEY_CARTO=
```

也可在网页右侧展开“地图 API key 设置”，输入并保存或清除 CARTO key。后端将配置原子写入权限为 `0600` 的 `.env`，立即生效。网页只显示是否配置，不返回已保存的 key；地图瓦片经本机后端代理，key 不会出现在浏览器瓦片 URL、导出 ZIP 或 Git 提交中。启动时进程环境中的 `API_KEY_CARTO` 优先于 `.env`；网页保存会更新当前进程和 `.env`。

默认 OSM 底图不需要 key。CARTO Dark/Light 必须配置 key。地图全览、PNG 预览和导出统一通过后端使用同一个 key；导出固定使用任务启动时的 key，避免任务中途切换。保存不代表服务商已验证 key 有效性。

`.gitignore` 排除 `.env`、其他本地环境文件、虚拟环境、瓦片缓存、导出和本地 GPX/FIT 文件，仅保留 `.env.example` 模板。

### 命令行导出

```sh
.venv/bin/python render_gpx_png_sequence.py track.gpx png_output --zoom 15 --size 1920x1080 --map-style dark
```

输出目录必须为空。命令行仅处理 GPX，已移除 FPS、倍速、插值及 XML 布局参数。`trim_gpx.py` 是保留的独立 GPX 裁剪工具，历史 XML 布局仅作为参考保留。

### 验证与运行数据

```sh
.venv/bin/python -m unittest -v test_renderer
```

需要联网加载 Leaflet 和地图瓦片，PNG 保留来源署名。有效瓦片位于 `.tile-cache/v2/`，按样式和 key 的摘要隔离；旧版缓存不再读取，避免复用缺少 key 时生成的水印瓦片。上下两张地图共用同一套内存 LRU 和磁盘缓存（相同样式、key、缩放等级和瓦片坐标可直接命中），重复并发请求只下载一次。导出和 CLI 中连续帧坐标、画面设置相同时直接复用上一张已编码 PNG，仍输出每一帧文件以保留时长；进度和 manifest 显示实际渲染/复用帧数。右侧缓存面板每两秒显示有效缓存张数、磁盘用量、内存/磁盘命中、下载数、活动请求和错误。ZIP 位于 `.exports/`，停止服务后可删除。轨迹仅保存在本机内存中，重启后须重新导入。一次只运行一个导出任务；瓦片失败会重试并报告错误。

## English

A local GPX-to-PNG sequence tool with a web editor and command-line export. The web editor interpolates recorded GPX time into a continuous PNG sequence; the CLI retains one PNG per original point.

### Quick start

Requires Python 3.10 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python render_gpx_png_sequence.py
```

The app opens <http://127.0.0.1:8765> automatically. Use `--port 9000` to change the port or `--no-browser` to suppress opening a browser. On Windows use `.venv\Scripts\python` and `.venv\Scripts\pip`. The server binds only to loopback.

### Web editor

- Import one `.gpx` file using the picker or drop it anywhere on the page (no application-defined file size limit). The map fits all points and preserves separate track segments and routes.
- Select a point directly, click the map to select its nearest point, or drag the location marker along the track. Original coordinates remain unchanged.
- A second row below timeline endpoints, the current time, and ruler ticks shows recording time in the GPX timestamp’s UTC offset (`Z` means UTC), falling back to the browser timezone when absent. Naive timestamps are parsed using the browser timezone as well.
- Trim with the timeline's start/end handles and seek with the blue playhead. Arrow buttons step through original points.
- Zoom the timeline from 1× to 32× using its buttons or the mouse wheel. Wheel zoom anchors at the pointer. Pan with the horizontal scrollbar, a trackpad, or Shift + wheel. Reset with “显示完整时间轴”. When the timeline is focused, `+`, `-`, and `0` provide keyboard controls.
- Choose OpenStreetMap, CARTO Dark, or CARTO Light; configure map zoom 1–19 and output width/height of 128–4096 pixels. Preview and export use the same backend renderer.
- Recording gaps are highlighted in orange on the timeline. The threshold is twice the median positive in-segment sampling interval, with a minimum of 60 seconds. Intervals meeting that threshold and recording-segment breaks are gaps. “拟合轨迹” is off by default: position holds at the earlier point and the route line breaks during gaps. Enable it to linearly interpolate missing positions by time, including segment gaps, consistently in map playback, PNG preview and export. Gaps still count toward duration.
- Configure line visibility, width and color, plus current-point visibility, diameter, fill color, outline width and color. The overview, PNG preview and export share these settings.
- Preview playback follows recorded GPX time at 10, 20, 50, 100, 200, 500, 1000, or 2000× speed. Importing or trimming automatically selects the lowest available rate that finishes within ten minutes, capped at 2000×. Manual selection and estimated playback duration remain available, with continuous linear interpolation between timestamps within each track segment. Gap and segment interpolation is controlled by “拟合轨迹”. Missing or non-monotonic timestamps fall back to point order at one point per second at 1×. Playback speed is independent of export speed.
- Export the entire GPX by default or the trimmed range. Export speed has a 1–100× slider and number input (default 10×); choose 25, 30, 50, or 60 fps (default 30). Estimated duration is source duration / export speed. Frame count is rounded up from estimated duration × fps, with a minimum of one frame. Sampling uniformly covers the selected interval including both endpoints; rounding affects duration by less than one frame. A five-hour GPX at 5× and 30 fps produces 108,000 frames for a one-hour sequence. The backend reports percentage, completed/total frames, elapsed time, estimated time remaining, and ZIP packing progress. Archives contain `frame_000001.png` onward and a `manifest.json` with coordinates, timestamps, and settings.

### API keys and local configuration

Copy the empty `.env.example` template to `.env` and set `API_KEY_CARTO`, or expand “地图 API key 设置” in the sidebar to save/clear a CARTO key. Updates are written atomically to a local `.env` with `0600` permissions and apply immediately. Existing keys are never returned to the browser. The backend proxies map tiles so the key stays out of browser tile URLs, export archives, and Git commits.

At startup an inherited `API_KEY_CARTO` overrides `.env`. Saving through the UI updates the current process and `.env`. OpenStreetMap needs no key; CARTO Dark/Light require a configured key. The overview, PNG preview, and export share the same backend credentials. Each export freezes its starting key until completion. Saving does not validate the key with the provider. Caches are partitioned by style and a credential digest; legacy tiles are ignored to avoid reusing API-key watermark images. The overview and PNG renderer share memory and disk caches for identical style, credentials, zoom and tile coordinates. Concurrent requests for the same tile download only once. Export and CLI reuse the previous encoded PNG for consecutive identical coordinates with fixed visual settings. Every sequence filename is still written to preserve duration. Progress and the manifest report rendered/reused frame counts. The sidebar reports valid cached tile counts, disk usage, memory/disk hits, downloads, active requests, and errors every two seconds.

Git ignores local environment files, virtual environments, tile caches, generated exports, and local GPX/FIT files. Only the empty `.env.example` template is tracked.

### Command-line export

```sh
.venv/bin/python render_gpx_png_sequence.py track.gpx png_output --zoom 15 --size 1920x1080 --map-style dark
```

The output directory must be empty. The CLI accepts GPX only; FPS, speed, interpolation, and XML-layout options have been removed. `trim_gpx.py` remains a standalone GPX trimming utility. The historical XML layout is retained as reference.

### Tests and runtime data

```sh
.venv/bin/python -m unittest -v test_renderer
```

Leaflet and map tiles require internet access. PNGs include map attribution. Tiles are cached under `.tile-cache/v2/`; ZIPs remain under `.exports/`. These folders can be deleted after stopping the server. Tracks are held in local server memory and must be reimported after restart. One export runs at a time; tile failures are retried and reported.
