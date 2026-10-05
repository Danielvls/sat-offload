# sat-offload — STK Walker-Delta 卫星网络生成

在 STK 里生成 **Walker Delta 星座**,给**每个轨道面单独配色**,算出**同轨/异轨 ISL 链路**,并提供可视化与切片数据导出。

代码参考 `sat-net/src/stk/stk_manager.py` 改写,所有 STK 语法都在本机 STK 12.2 上实测过。

两个入口,共用同一套构建流程:

| 入口 | 用途 |
| --- | --- |
| [`main.py`](main.py) | **可视化**:在 STK GUI 里跑,建完保留场景供查看 |
| [`save_slices.py`](save_slices.py) | **出数据**:批量导出切片 JSON + manifest |

---

## 快速开始

所有参数都来自 [`config.yaml`](config.yaml),两个入口都只接受 `--config` 一个参数。

```bash
./.venv/Scripts/python.exe main.py                              # 建完留在 STK 里看
./.venv/Scripts/python.exe save_slices.py                       # 导出切片数据
./.venv/Scripts/python.exe main.py --config other.yaml          # 换一套配置
```

自检(不需要 STK 许可证):

```bash
./.venv/Scripts/python.exe tests/test_topology.py    # 链路几何
./.venv/Scripts/python.exe tests/test_settings.py    # 配置加载
```

---

## 目录结构

| 路径 | 作用 |
| --- | --- |
| [config.yaml](config.yaml) | **运行配置,唯一需要改的文件** |
| [src/settings.py](src/settings.py) | 配置的加载与校验 |
| [src/config.py](src/config.py) | `ScenarioConfig`、`ConstellationConfig`、STK 时间解析 |
| [src/topology.py](src/topology.py) | ISL 连接关系 —— 纯函数,不依赖 STK |
| [src/stk/stk_manager.py](src/stk/stk_manager.py) | 场景、星座、配色、access、可视化、导出 |
| [src/pipeline.py](src/pipeline.py) | 两个入口共用的构建流程 |
| [src/utils/timegrid.py](src/utils/timegrid.py) | 定步长时间网格(替代 pandas) |
| [src/utils/palette.py](src/utils/palette.py) | 逐平面配色 |
| [src/utils/progress.py](src/utils/progress.py) | tqdm 进度条 |
| [main.py](main.py) | 可视化入口 |
| [save_slices.py](save_slices.py) | 数据导出入口 |

---

## 配置文件

```yaml
scenario:
  name: sat-offload
  start: "1 Oct 2026 00:00:00.000"   # UTCG
  stop: "1 Oct 2026 01:00:00.000"
  step: 300.0                        # 切片间隔(秒)

constellation:
  planes: 20                         # P
  sats_per_plane: 10                 # S   -> T = 200
  phasing: 45                        # F
  inclination: 53.0                  # 度
  altitude: 550.0                    # 圆轨道高度 km,也可改用 semi_major_axis
  eccentricity: 0.0
  star: false                        # true = Walker Star(RAAN 铺 180°)
  inter_plane_offset: auto           # 跨面邻居的面内序号偏移,auto = 按几何取最近邻

output:
  directory: data/slices
  overwrite: false                   # 目录非空时须显式置 true 才会覆盖

run:
  mode: desktop                      # desktop = STK GUI | engine = 无头
  visible: true                      # 仅 desktop;false 则隐藏窗口
  colors: true                       # 逐平面配色(需 desktop)
  with_lla: false                    # 每个节点附带经纬度
  progress: true                     # tqdm 进度条
  log_level: INFO

visualization:                       # 仅 main.py 生效
  enabled: true                      # 建完保留 STK 场景供查看
  links: true                        # 画出 ISL 链路线(随 access 通断动态显示)
  link_width: 2                      # 链路线宽
```

### 校验是严格的

未知的配置段或键会**直接报错**,不会静默忽略。因为把 `sats_per_plane` 打成 `sats_per_plan` 这类错误,旧行为是拿默认值建出一个完全不同的星座、然后一路跑完——这种错最难发现。

```
$ python save_slices.py --config typo.yaml
config error: typo.yaml: unknown key(s) in [constellation]: planez.
Known keys: altitude, eccentricity, inclination, phasing, planes,
sats_per_plane, semi_major_axis, star
```

`mode` 只认 `desktop`/`engine`,`log_level` 只认四级,数字/布尔类型不符同样拦截。

---

## 星座生成

Walker `i: T/P/F` —— `T` 颗卫星、`P` 个轨道面、`F` 为相位因子。设每面 `S = T/P` 颗,第 `k` 个面(0 基)第 `j` 颗:

```
RAAN(k) = k · 360/P          Delta
RAAN(k) = k · 180/P          Star

u(k, j) = j · 360/S + (k+1) · F · 360/T
```

式中的 `(k+1)` 复刻了参考实现里 `true_anomalies += (360/T)*F` 的累加写法。它是整个星座的**整体旋转**(所有卫星的升交点角距平移同一个 `F·360/T`),不改变任何两颗卫星之间的相对关系,保留它是为了让生成结果与 `sat-net` 逐颗对齐。

---

## ISL 拓扑

两类链路,与参考实现 `get_sat_access` 一致:

- **同轨(intra)** —— 每个轨道面内的环:`Sat{p}_{n} ↔ Sat{p}_{n+1}`,末位绕回首位。
- **异轨(inter)** —— 相邻轨道面:`Sat{p}_{n} ↔ Sat{p+1}_{n+offset}`,末面绕回第 1 个面时用 `offset + F`。

### 跨面偏移 `offset` 是算出来的,不是 0

参考实现把跨面链路写死成「相邻面、面内序号相同」(`offset = 0`)。**这只有在相位步进很小时才成立。**

跨一个面,升交点角距推进 `φ = F·360/T`,而面内间隔是 `Δu = 360/S`。同序号的两颗星相差 `φ`,所以只有当 `φ` 明显小于 `Δu` 时它才是最近邻。`offset` 的取值:

```
offset ≈ -φ / Δu = -F/P      (取整)
```

`F` 小的时候 `offset ≈ 0`,参考配置 `1584/72/11` 就是这种情况。但把 `F` 调大就崩了 —— 例如 `200/20/45`,`φ = 81°` 而 `Δu` 只有 `36°`,同序号两颗星实测相距 **9955 km**,远超 550 km 高度下 5411 km 的视线极限,**一条异轨链路都建不出来**(这正是当初 `20×10` 跑出来异轨为空的原因)。

而且 `offset` 不能用相位残差来选:`200/20/45` 下相位规则给出 `-2`(残差 9°),但实测弦长最短的是 `-3`(2270 km vs 2664 km)—— 因为 18° 的升交点角距差也在贡献距离。所以实现直接扫一遍候选偏移,**按一圈轨道上的平均弦长取最小**,那才是真正的最近邻。

结果(`inter_plane_offset: auto`,默认):

| T/P/F | φ | 自动 offset | 跨面最远弦长 | 修复前(offset 0) |
| --- | --- | --- | --- | --- |
| 1584/72/11 | 2.50° | **0**(与参考一致,无变化) | 822 km | 822 km |
| 1584/72/45 | 10.23° | **−1** | 613 km | 1668 km |
| 200/20/45 | 81.0° | **−3** | 2590 km | 9955 km(不可见) |
| 100/10/1 | 3.60° | **−1** | 3625 km | — |

也可以把 `offset` 钉死成整数,但要知道自己在干什么。

**回绕**同样跟着变:末面到首面覆盖的相位推进 = 一次前向跨面 **加上** 整圈 RAAN,后者恰好等于 `F` 个面内间隔:

```
∑_面 φ = P·F·360/T = F·(360/S)
```

所以回绕偏移是 `offset + F`,两者残差相同 —— 对**任意** `T/P/F` 都成立。

每对卫星通过 STK access(`AER Data/Default`,取 `Time` + `Range`)采样,样本按最近邻落到时间切片上。超出视线的链路该切片不产生边,所以图是时变的:550 km 高度下链路最远约

```
2·√((R+h)² − R²) ≈ 5400 km
```

---

## 可视化(main.py)

`visualization.enabled: true` 时,构建结束后 **不关闭 STK**,场景直接留在窗口里,可以转视角、拖时间轴看链路通断。

`visualization.links: true` 会打开每条 access 的图形开关,把星间链路画在 2D/3D 窗口里:

```python
access.Graphics.Inherit = False      # 必须先清 Inherit,否则 STK 报只读
access.Graphics.LineVisible = True
access.Graphics.LineWidth = 3
```

STK Desktop 是用 `userControl=True` 启动的,按官方语义这会把应用交还给用户 —— Python 进程退出后 STK 继续存活。(顺带一提:STK 的进程名是 **`AgUiApplication.exe`**,不是 `STK12.exe`,用 `tasklist` 找的时候别找错。)

画线循环包在 `BeginUpdate()/EndUpdate()` 里批处理。不包的话 STK 每次写属性都要重绘一遍:192 条链路实测 **32 s → 3 s**,快约 10 倍。满规模 3168 条链路约 50 s。

注意 GUI 模式整体比无头慢很多。同样 96 星 / 192 条链路 / 3 切片:

| 阶段 | engine(无头) | desktop(GUI) |
| --- | --- | --- |
| 计算 access | < 1 s | 39–48 s |
| 逐平面配色 | 跳过 | 13–32 s |
| 画出链路线 | 跳过 | 3 s(批处理后) |

要看图就别嫌慢;纯粹出数据就设 `mode: engine`。

---

## 输出

`save_slices.py` 写入 `output.directory`(默认 `data/slices`):

```
data/slices/
    manifest.json      本次运行的描述:场景、星座参数、链路统计
    slice00000.json    t = start
    slice00001.json    t = start + step
    ...
```

文件名零填充,所以在文件浏览器里天然按时间排序。每个切片是 NetworkX node-link JSON(`edges="edges"`):

- 每颗卫星在每个切片里都是节点(孤立的保持 0 度),带 `plane`、`index`;
- 该切片可见的每条 ISL 是一条边,带 `range`(km)和 `kind`(`intra`/`inter`);
- 时间戳在 `graph.graph["time"]`。

`run.with_lla: true` 额外给每个节点附上 `lat`/`lon`(取自 `LLA State/TrueOfDateRotating`)。`manifest.json`([写入位置](src/stk/stk_manager.py#L402))记录星座参数和逐切片边数,所以一份导出是自描述的。

目录非空时 `save_slices.py` 会拒绝写入,除非 `output.overwrite: true`,避免两次不同参数的运行悄悄混在一起。

---

## STK 语法要点

以下三条是实测踩出来的,扩展代码时值得知道。

**1. STK Engine 完全不能碰图形。** `noGraphics=True` 时任何读写 `sat.Graphics` 或 `sat.VO` 都会抛:

```
STKRuntimeError: NoGraphics property is set to true, Graphics are not available.
```

access 计算不受影响。所以 `apply_plane_colors()` / `show_links()` 在 engine 模式下返回 `False` 并告警,而不是崩掉,让无头运行仍能产出网络。

**2. 轨道系统必须写全名。** `sat.VO.OrbitSystems.Add("Inertial")` 报 `One or more arguments are invalid`,必须写 `"CentralBody/Earth Inertial System"`(见 `DEFAULT_ORBIT_SYSTEM`)。

**3. 设 3D 颜色前要先清 `Inherit`。** 轨道系统元素默认 `Inherit = True`,直接赋 `Color` 会报 `Cannot modify read-only attribute`:

```python
element = sat.VO.OrbitSystems.Add(DEFAULT_ORBIT_SYSTEM)
element.Inherit = False          # 必须在前
element.Color = Colors.FromRGB(r, g, b)
element.IsVisible = True
```

**与参考项目的其他差异:**

- **去掉了 pandas。** `TimeGrid` 取代 `pandas.date_range` + `approx_time`。进度条用 tqdm,样式([src/utils/progress.py](src/utils/progress.py))与参考项目一致:绿色、30 列。
- **时间戳是纳秒。** STK 数据提供器返回 `1 Oct 2026 00:00:00.000000000`,而 `datetime.strptime` 的 `%f` 最多吃 6 位 —— `parse_stk_time` 会先把小数部分截到微秒。
- **`edges="edges"`。** NetworkX 3.7 改了参考项目里 `nx.node_link_data(graph, edges="links")` 的写法。
- **`ShutDown()`。** 这是 Desktop/Engine 两个应用对象自己的生命周期方法;直接调底层 COM 的 `Terminate()`/`Quit()` 会让 Python 包装对象指向已释放的 COM 对象(退出时访问违例)。

---

## 环境

本机解释器是 `./.venv/Scripts/python.exe`(Python 3.13)。`agi.stk12` 不在 PyPI,需从 STK 安装目录装:

```bash
./.venv/Scripts/python.exe -m pip install \
    "E:/STK12/STK12/bin/AgPythonAPI/agi.stk12-12.2.0-py3-none-any.whl"
```

其余依赖见 [requirements.txt](requirements.txt):`networkx`、`tqdm`、`pyyaml`。

---

## 更新历史

### v1.4 — 修复跨面链路选错邻居

`20/10/45` 下异轨链路一条都建不出来,根因是跨面邻居被写死成「面内序号相同」。

- `offset ≈ -F/P`,不再是常数 0;实现改为**扫一遍候选偏移、按一圈轨道的平均弦长取最小**
- 新增 `constellation.inter_plane_offset`:`auto`(默认)或钉死整数
- 回绕偏移相应变为 `offset + F`(原为 `F`,只在 `offset=0` 时正确)
- `build_links()` 前置几何检查:同轨/异轨弦长若超过视线极限,**在跑 access 之前**就告警,不必等几分钟才发现零边
- `inter_plane_offset` 写入 `manifest.json`
- 修:`test_shipped_config_loads` 写死了 72/22,改配置就误报 —— 改为只校验结构

修改文件:[config.yaml](config.yaml)、[src/config.py](src/config.py)、[src/topology.py](src/topology.py)、[src/settings.py](src/settings.py)、[src/stk/stk_manager.py](src/stk/stk_manager.py)、[tests/test_topology.py](tests/test_topology.py)、[tests/test_settings.py](tests/test_settings.py)、`README.md`

### v1.3 — 可视化

在 STK 里能直接看网络了。

- 新增 `[visualization]` 配置段:`enabled`(建完保留 STK 场景)、`links`(画 ISL 链路线)、`link_width`
- `STKManager.show_links()`:给已算好的每条 access 打开 `Graphics.LineVisible`
- `STKManager` 新增 `keep_access` 参数与 `access_list`,`close()` 新增 `shutdown` 开关
- `main.py` 改为可视化入口;`save_slices.py` 仍为数据入口,忽略 `[visualization]`
- 画线循环加 `BeginUpdate()/EndUpdate()` 批处理,192 条链路从 32 s 降到 3 s

修改文件:`config.yaml`、[src/settings.py](src/settings.py)、[src/stk/stk_manager.py](src/stk/stk_manager.py)、[src/pipeline.py](src/pipeline.py)、[main.py](main.py)、[tests/test_settings.py](tests/test_settings.py)、`README.md`

### v1.2 — YAML 配置文件

参数从命令行搬进配置文件。

- 新增 `config.yaml` 与 [src/settings.py](src/settings.py)(加载 + 严格校验)
- **删除 `src/cli.py`**,两个入口改为只接受 `--config`
- `main.py` 去掉 `--out`(出数据交给 `save_slices.py`)
- 未知段/未知键/类型错误一律报错退出(exit 2)

修改文件:`config.yaml`(新增)、[src/settings.py](src/settings.py)(新增)、[src/cli.py](src/cli.py)(删除)、[main.py](main.py)、[save_slices.py](save_slices.py)、[tests/test_settings.py](tests/test_settings.py)(新增)、`requirements.txt`、`README.md`

### v1.1 — tqdm 进度条 + 切片保存入口

- 新增 [src/utils/progress.py](src/utils/progress.py),四条进度条(建星座 / 算 access / 读 LLA / 存切片)
- 新增 [save_slices.py](save_slices.py) 与 [src/pipeline.py](src/pipeline.py)(两个入口共用流程)
- 切片按 `slice00000.json` 零填充命名,并新增 `manifest.json`
- 修:`set_postfix_str()` 默认 `refresh=True`,会绕过 `mininterval` 导致每轮重画(192 条链路刷 192 行)
- 修:进度条上 "linked" 计数把「样本数」当成了「链路数」;日志把节点数标成了边数

修改文件:[src/utils/progress.py](src/utils/progress.py)(新增)、[save_slices.py](save_slices.py)(新增)、[src/pipeline.py](src/pipeline.py)(新增)、[src/cli.py](src/cli.py)(新增,后于 v1.2 删除)、[src/stk/stk_manager.py](src/stk/stk_manager.py)、[main.py](main.py)、`requirements.txt`

### v1.0 — 初始版本

- Walker Delta 星座生成(T/P/F 参数化,支持 Star)
- 逐平面配色,2D `Graphics.Attributes.Color` + 3D 轨道线颜色双写;调色板按黄金比序列走色相,相邻面差异最大
- ISL 链路计算:同轨环 + 异轨(含末面回绕偏移 `F`)
- 每切片一个 NetworkX 网络图,可导出 JSON
- 时间网格用标准库实现,不依赖 pandas

新增文件:`main.py`、[src/config.py](src/config.py)、[src/topology.py](src/topology.py)、[src/pipeline.py](src/pipeline.py)、[src/stk/stk_manager.py](src/stk/stk_manager.py)、[src/utils/timegrid.py](src/utils/timegrid.py)、[src/utils/palette.py](src/utils/palette.py)、[tests/test_topology.py](tests/test_topology.py)、`requirements.txt`、`.gitignore`

---

### 实测记录

| 版本 | 验证项 | 结果 |
| --- | --- | --- |
| v1.0 | 拓扑单测 | 8/8,含末面回绕的几何断言 |
| v1.0 | 满规模 1584/72/11 | 1584 同轨 + 1584 异轨全通,距离 665–1972 km,44 s |
| v1.0 | 稀疏构型物理自洽 | 96 星下仅 40/96 异轨可见,距离上限 5464 km ≈ 视线极限 |
| v1.1 | 1584 星 3 切片导出 | 3 × (1584 节点 / 3168 边),45 s,1.6 MB |
| v1.2 | 配置单测 | 11/11 |
| v1.3 | 可视化端到端 | 从独立进程读回:`LineVisible=True`、`Inherit=False`、`LineWidth=3`,场景与配色均持久化 |
| v1.3 | 画线批处理优化 | 192 条链路 32 s → 3 s |
| v1.3 | 配置单测 | 14/14 |
| v1.4 | 满规模 1584/72/11 回归 | offset 仍为 0,3168/3168 链路、每切片 3168 边 —— 与修复前完全一致,无回归 |
| v1.4 | 20/10/45 修复验证 | 修复前异轨 0 条;修复后 **400/400** 可见(200 同轨 + 200 异轨) |
| v1.4 | 单测 | 拓扑 13/13、配置 15/15 |
