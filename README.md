# GPX Point Studio

[简体中文](#简体中文) · [English](#english)

## 简体中文

一个本地运行的 GPX 地图 PNG 序列工具，带网页编辑和命令行导出。每个原始 GPS 点位输出一张 PNG，保留顺序及重复点，不进行 FPS 插值。

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

- 点击导入或将一个 `.gpx` 文件拖到页面任意位置（最大 30 MB）；自动显示全部轨迹点，支持多个轨迹段及路线点。
- 点击轨迹点、点击地图选最近点，或拖动定位标记沿轨迹选点；原始坐标不会改变。
- 时间轴首尾控制裁剪范围，蓝色游标控制当前点；左右箭头逐点移动。
- 时间轴支持 1–32 倍缩放：使用加减按钮或滚轮，以鼠标位置为中心缩放；使用水平滚动条、触控板或 Shift + 滚轮平移。“显示完整时间轴”恢复全览。聚焦时间轴后可用 `+`、`-`、`0` 操作。
- 选择默认 OSM、CARTO Dark 或 Light 样式；支持地图缩放等级 1–19、宽高各 128–4096 像素。PNG 预览与导出共用同一后端渲染器。
- 按 GPX 实际时间播放，支持 0.5–64 倍速。时间缺失或非递增时按点位顺序播放，1× 每秒一个点。倍速仅影响预览。
- 默认导出整个 GPX，可选时间轴裁剪范围。后端异步渲染并显示进度，完成后下载 ZIP：`frame_000001.png` 起的序列及包含坐标、时间和导出设置的 `manifest.json`。

### API key 与本地配置

`.env.example` 是可提交的空值模板；真实 key 写入项目根目录的 `.env`：

```dotenv
API_KEY_CARTO=
```

也可在网页右侧展开“地图 API key 设置”，输入并保存或清除 CARTO key。后端将配置原子写入权限为 `0600` 的 `.env`，立即生效。网页只显示是否配置，不返回已保存的 key；地图瓦片经本机后端代理，key 不会出现在浏览器瓦片 URL、导出 ZIP 或 Git 提交中。启动时进程环境中的 `API_KEY_CARTO` 优先于 `.env`；网页保存会更新当前进程和 `.env`。

默认 OSM 底图不需要 key。CARTO Dark/Light 未配置时使用免 key URL，配置后携带 key 请求。保存不代表服务商已验证 key 有效性；已有瓦片缓存可直接复用。

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

需要联网加载 Leaflet 和地图瓦片，PNG 保留来源署名。瓦片位于 `.tile-cache/`，ZIP 位于 `.exports/`，停止服务后可删除。轨迹仅保存在本机内存中，重启后须重新导入。一次只运行一个导出任务；瓦片失败会重试并报告错误。

## English

A local GPX-to-PNG sequence tool with a web editor and command-line export. Every original GPS point produces exactly one PNG, preserving order and duplicate points without FPS interpolation.

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

- Import one `.gpx` file using the picker or drop it anywhere on the page (maximum 30 MB). The map fits all points and preserves separate track segments and routes.
- Select a point directly, click the map to select its nearest point, or drag the location marker along the track. Original coordinates remain unchanged.
- Trim with the timeline's start/end handles and seek with the blue playhead. Arrow buttons step through original points.
- Zoom the timeline from 1× to 32× using its buttons or the mouse wheel. Wheel zoom anchors at the pointer. Pan with the horizontal scrollbar, a trackpad, or Shift + wheel. Reset with “显示完整时间轴”. When the timeline is focused, `+`, `-`, and `0` provide keyboard controls.
- Choose OpenStreetMap, CARTO Dark, or CARTO Light; configure map zoom 1–19 and output width/height of 128–4096 pixels. Preview and export use the same backend renderer.
- Preview playback follows recorded GPX time at 0.5–64× speed. Missing or non-monotonic timestamps fall back to point order at one point per second at 1×. Playback speed never changes exported point counts.
- Export the entire GPX by default or the trimmed range. The backend renders asynchronously with progress and ZIP download. Archives contain `frame_000001.png` onward and a `manifest.json` with coordinates, timestamps, and settings.

### API keys and local configuration

Copy the empty `.env.example` template to `.env` and set `API_KEY_CARTO`, or expand “地图 API key 设置” in the sidebar to save/clear a CARTO key. Updates are written atomically to a local `.env` with `0600` permissions and apply immediately. Existing keys are never returned to the browser. The backend proxies map tiles so the key stays out of browser tile URLs, export archives, and Git commits.

At startup an inherited `API_KEY_CARTO` overrides `.env`. Saving through the UI updates the current process and `.env`. OpenStreetMap needs no key; CARTO styles use keyless URLs when no key is set and append the configured key otherwise. Saving does not validate a key with the provider, and cached tiles can be reused.

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

Leaflet and map tiles require internet access. PNGs include map attribution. Tiles are cached under `.tile-cache/`; ZIPs remain under `.exports/`. These folders can be deleted after stopping the server. Tracks are held in local server memory and must be reimported after restart. One export runs at a time; tile failures are retried and reported.
