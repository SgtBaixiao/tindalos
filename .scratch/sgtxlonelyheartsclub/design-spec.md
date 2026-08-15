# The AI Coding Dictionary 设计系统规格（SgtXLonelyHeartsClub 复刻依据）

> 提取来源：`D:\Agent Workspace\Web src` 快照（`The AI Coding Dictionary.html` + `_files/` 3 个 CSS）。
> 提取方式：Explore agent（2026-08-16）。站点本质：Three.js r184 3D 知识图谱，69 术语 / 7 分组，CSS-modules + Tailwind 工具类。视觉语言 =「纸面 + 墨色」极简风：暖灰纸底、近黑墨字、hairline 边框、圆角胶囊按钮、`cubic-bezier(.16,1,.3,1)` 弹性缓动贯穿。

## 色彩系统

### 核心令牌（`152fe9pmyq_69.css` 的 `:root`）

| 变量 | 值 | 用途 |
|---|---|---|
| `--color-primary` | `#eaeae8` | 页面主背景（纸色）、tooltip 文字色、选中反色 |
| `--color-secondary` | `#1a1a19` | 主文字（墨色）、tooltip 背景、::selection 背景 |
| `--color-contrast` | `#1a1a19` | 焦点 outline |
| `--color-paper` | `#eaeae8` | 语义别名 = 纸色背景 |
| `--color-ink` | `#1a1a19` | 语义别名 = 墨色文字 |
| `--color-blue` `#0070f3` / `green` `#0f8` / `red` `#e30613` / `purple` `#7928ca` / `pink` `#ff0080` | 状态色，主 UI 少用 |

`[data-theme=light]` 重声明 primary/secondary/contrast 同值。

### 分区（section）动态色（`17j-zb_w85yvb.css`）

```css
@property --section-ink  { initial-value:#1a1a19 }
@property --section-paper{ initial-value:#f2f2f0 }
:root{ --section-ink:#1a1a19; --section-paper:#f2f2f0; transition:--section-ink .45s, --section-paper .45s }
```
「按分组切换主题色」机制：JS 改写 `--section-ink/--section-paper` 带 `.45s` 过渡。面板内再映射 `--accent:#0a0a0a; --ink:var(--section-ink); --paper:var(--section-paper)`。

### 衍生表面色

| 变量 | 回退 | 定义 |
|---|---|---|
| `--surface` | `#e2e2e0` | `color-mix(in srgb, var(--color-secondary) 4%, var(--color-primary))` |
| `--surface-2` | `#d9d9d7` | `color-mix(in srgb, var(--color-secondary) 8%, var(--color-primary))` |
| `--line` | `#1a1a1924` | `color-mix(in srgb, var(--color-secondary) 14%, transparent)` |
| `--line-strong` | `#1a1a1947` | `color-mix(in srgb, var(--color-secondary) 28%, transparent)` |
| `--hair` | — | `color-mix(in oklch, var(--ink) 16%, transparent)`（1px 分隔线/边框基础色） |

### 组件硬编码色

- Loading 屏：底 `#eaeae8`、字 `#1a1a19`；进度轨道 `#1a1a191f`、填充 `#1a1a19`；credit 字 `#1a1a1961`
- Orbit 提示：字 `#1a1a19b3`、底 `#eaeae8d1`、框 `#1a1a1924`、圆点 `#1a1a1966`；`backdrop-filter: blur(6px)`
- 节点详情面板：默认 `--paper:#ececec`、`--ink:#171717`；节点强调色 `--node-accent`（内联）；气泡 提问边框 `--hair` / 回答背景 `--ink` 文字反色；阴影 `0 -20px 44px -30px #0000008c`
- 信息弹窗：`--paper:#e3e3e3`、`--ink:#171717`；遮罩 `#1111116b` + blur(3px)；阴影 `0 30px 80px -40px #0009`
- Canvas 晕影：`radial-gradient(130% 80%, #0000 24%, #07050680 68%, #070506f0 100%), linear-gradient(90deg, #070506f2 0%, #070506e0 26%, #0705068c 44%, #0000 62%)`（左暗右亮）
- Tooltip：底 `#1a1a19`、字 `#eaeae8`、阴影 `0 2px 8px #00000026`
- 右键菜单：字 `#1a1a19`、底 `#fcfcfb`、框 `#1a1a1921`；阴影 `0 22px 52px -22px #0000006b, 0 4px 12px -6px #0003`
- `::selection` 反色（墨底纸字）；`:focus-visible` 环 `2px solid var(--color-contrast)` offset 2px；`theme-color` meta `#eaeae8`、`color-scheme: light`

## 字体

- **正文 sans**：`"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif`
- **等宽 mono**：`@font-face "mono"`（ServerMono_Regular，度量对齐 ascent 65.19% / descent 14.49% / size-adjust 138.07%），栈：`mono, "mono Fallback", ui-monospace, SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace`；用于 credit/代码块/logo "AI" 两字母
- 字号阶梯（clamp 响应式）：面板大标题 `clamp(2.4rem,4.4vw,3.9rem)`；loading 标题 `clamp(1.7rem,5.2vw,3.1rem)`；def `clamp(1rem,1.15vw,1.12rem)`；正文 `.9rem`；kicker/section `.6rem` 大写；credit mono `clamp(.5rem,1.4vw,.62rem)`
- 字重：400 正文 / 500 按钮 / 600 partner / 700 标题
- 行高：`.94` 大标题、`1.4` 气泡、`1.5` def、`1.6` prose
- 字距：大标题 `-.035em`、section `.18em`、kicker `.2em`；数字 `font-feature-settings:"tnum"`

## 间距布局

- **设计网格**：移动 4 列 / 桌面 12 列（`@media(min-width:800px)`）；`--gap:calc((16*100/1440)*1vw)`；`--header-height:98px`（桌面）
- 面板 pad-x `clamp(1.8rem,2.8vw,3rem)`；弹窗 `clamp(1.6rem,3vw,2.2rem)`
- 圆角：胶囊 `999px`、圆形按钮 `50%`、sheet 顶部 `1.25rem`、弹窗 `1rem`、气泡 `.85rem`、右键菜单 `12px`、代码键 `.3rem`、进度轨道 `2px`
- 阴影见色彩节；关键：sheet `0 -20px 44px -30px #0000008c`、弹窗 `0 30px 80px -40px #0009`
- 关键尺寸：**节点面板宽 `33.3333vw` 高 `100dvh` 固定右侧**，打开时 canvas 左移 `-16.66vw`（面板一半）；移动 sheet `90dvh` 底部抽屉；圆形控件 `2.4rem`；正文 `34ch` 最大宽、气泡 `86%`、搜索展开 `min(360px,80vw)`、弹窗 `min(92vw,30rem)`、tooltip `280px`、右键菜单 `224px`

## 动画全集

缓动变量：`--reveal-ease: cubic-bezier(.16,1,.3,1)`（核心）、抽屉 `cubic-bezier(.32,.72,0,1)`、坠落 `cubic-bezier(.7,0,.84,0)`、`--ease-out-expo: cubic-bezier(.19,1,.22,1)`；时长 `.2s/.4s/.8s`

### A. loading-screen（4 个 keyframes）

1. **wordRise**（逐词升起）：`0%{transform:translateY(115%)} to{translateY(0)}`；`.wordInner` `.8s var(--reveal-ease) both`，延迟 `calc(var(--i,0)*90ms + .12s)`
2. **wordFall**（隐藏坠落）：`0%{translateY(0)} to{translateY(-115%)}`；`[data-hiding=true]` `.5s cubic-bezier(.7,0,.84,0) both`，延迟 `calc(var(--i)*50ms)`
3. **barIn**：`opacity 0→1`；`.bar` `.5s` 延迟 `.45s`
4. **creditIn**：`opacity 0→1; translateY(6px→0)`；`.credit` `.6s var(--reveal-ease) .18s`

### B. node-detail（9 个 keyframes）

5. **slideEnter**：`0%{translateX(calc(var(--dir,0)*(100%+var(--pad-x))))} to{0}`；`.slide[data-phase=enter]` `.52s var(--reveal-ease) both`
6. **slideExit**：反向；`.slide[data-phase=exit]` 同配置
7. **revealFade**：`opacity 0→1; translateY(11px→0)`；`.meta/.def/.aliases/.block/.pager` `.66s var(--reveal-ease) both`，延迟 `calc(var(--i,0)*52ms + var(--reveal-base))`（`--reveal-base:.26s`）
8. **ruleGrow**：`to{scaleX(1)}`；`.rule` `.7s var(--reveal-ease) both`，延迟 base+52ms*i+40ms，`transform-origin:0`
9. **titleReveal**：`0%{opacity:0;translateY(.5em)}`；`.titleInner` `.72s var(--reveal-ease) both`，延迟 base+40ms，`transform-origin:0 100%`
10. **keyHintIn**：`translate(-50%,-120%)→(-50%,-150%)`；`.keyHint` `.5s var(--reveal-ease)`
11. **pagerRoll**：`translateY(1.3em→0)`；`.pagerNameRoll` `.42s var(--reveal-ease) both`
12. **pagerRollDown**：`translateY(-1.3em→0)`；`[data-dir=prev]` 复用 `.42s`
13. **pagerBarIn**：`opacity 0→1; translateY(100%→0)`；移动端 `.pager` `.5s cubic-bezier(.32,.72,0,1)`

### C. orbit-hint

14. **hintIn**：`opacity 0→1; translate(-50%,8px→-50%)`；`.6s var(--reveal-ease) both`

### 非 keyframes 过渡（同样重要）

| 元素 | 过渡 |
|---|---|
| 面板开合 `.panel` | `transform .62s var(--reveal-ease), opacity .4s ease`（translate(100%)↔0）|
| canvas 移位 | `transform .62s var(--reveal-ease)`（data-shift 时 translate(-16.66vw)）|
| sheet 抽屉 | `transform .5s cubic-bezier(.32,.72,0,1)` |
| loading 根淡出 | `opacity .6s var(--reveal-ease)`（data-hiding→0）|
| 搜索框展开 `.field` | `width .46s var(--reveal-ease), padding .46s, color .2s, border-color .25s, background .25s, box-shadow .3s`（2.4rem↔min(360px,80vw)）|
| 胶囊文字上滚 | `transform .4s var(--reveal-ease)`（hover translateY(-100%)，外层 overflow:hidden）|
| 翻页箭头/元信息 | `transform .4s var(--reveal-ease)` |
| 右键菜单 | `transform .14s ease-out, opacity .14s ease-out`（scale(.96)↔1）|
| 信息弹窗 | `opacity .42s ease, transform .46s var(--reveal-ease)`（translate(-50%,-44%) scale(.96)↔居中）|
| 无障降级 | `prefers-reduced-motion:reduce` 全部压到 `.01ms` 并 `animation:none` |

## 标志性动效

### (a) Loading 逐词坠落/升起

`.word` = `display:inline-block; overflow:hidden; padding:0 .04em .06em; vertical-align:bottom`（外层裁剪视窗）；`.wordInner` 初始 `translateY(115%)` 藏于裁剪区下。升起：`wordRise .8s`，逐词 90ms 间隔（The=.12s → Dictionary=.39s），弹性弹出。坠落：`data-hiding=true` 时 `wordFall .5s cubic-bezier(.7,0,.84,0)`，50ms 间隔从上方飞出 + 根淡出 `.6s`。

### (b) 节点详情面板分层揭示

打开：面板 `.62s var(--reveal-ease)` 滑入 + `opacity .4s`；canvas 同步 `-16.66vw` 让位。内容层序：标题 `titleReveal .72s`（.3s 延迟）→ 分隔线 `ruleGrow .7s` scaleX 展开 → 各内容块 `revealFade .66s` 按 52ms stagger。翻页器 `pagerRoll .42s`：next 从下 `translateY(1.3em)` 上滚、prev 从上 `translateY(-1.3em)` 下滚（外层 overflow:hidden + 1.3em 高）。内容页切换 slideEnter/slideExit `.52s` 按 `--dir` ±1 整页横向位移。

### (c) Orbit 轨道提示

固定底部居中，`hintIn .6s` 从 `translate(-50%,8px)` 浮起淡入；圆点 + 文字（"拖拽旋转 · 滚轮缩放"类）；blur(6px) 半透明胶囊。

## 页面结构

### 根

```
<html lang data-theme="light" style="--vw;--dvh;--svh;--lvh;--scrollbar-width:10px;--section-ink:#1a1a19;--section-paper:#f2f2f0">
```

### body 内 DOM 顺序

1. Skip link（无障碍）
2. **Loading 屏** `.loading-screen-module__OT_xeW__root`（fixed inset-0 覆盖层）：h1.title > 4× span.word > span.wordInner(--i 0-3) + div.bar > span.fill(width:30%) + div.credit
3. **主体验容器** `.experience-module__Kq5lfa__root`（`--term-count:69`）：
   - `.canvas`（`data-shift=false data-detent=rest`）→ Three.js `<canvas data-camera-controls-version=3.1.2>`（3D 知识图谱，唯一主内容区）
   - `.vignette`（固定晕影遮罩，pointer-events:none）
   - `.atlas-search`（固定左上 top:28 left:32）→ `.field`（iconBtn 放大镜 + input#atlas-search）→ `.hint`；胶囊，聚焦展开 min(360px,80vw)
   - `.node-detail .panel`（`data-open=false aria-hidden style="--node-accent"`）→ 移动端 `.sheet` 底部抽屉变体
   - `.aihero-badge`（固定左下，logo "AI" mono 字重 600）
   - `.info-modal .trigger`（固定右上 28px 圆形 "i" 按钮 aria-haspopup=dialog）
   - `.color-toggle`（固定右下 right:calc(32px+3rem) 圆形调色板）aria-pressed
   - `.sound-toggle`（固定右下 right:32px 圆形声音按钮）
4. `.sr-only` SEO 文章（完整 69 条术语字典，7 分组）
5. next-route-announcer / Vercel Analytics / 扩展影子 DOM（非站点内容）

### 术语分组（69 条 → 节点图骨架，复刻时替换为 SgtXLonelyHeartsClub 栏目）

The Model(16) / Sessions·Context Windows·Turns(8) / Tools & Environment(10) / Failure Modes(9) / Handoffs(9) / Memory and Steering(6) / Patterns of Work(11)

## 状态钩子（data-* 全集）

`data-theme` / `data-shift`（面板让位）/ `data-detent`（rest/expanded）/ `data-open` / `data-active` / `data-hiding`（loading）/ `data-show` / `data-role`(q/a 气泡) / `data-dir`(prev/next) / `data-phase`(enter/exit) / `data-copied` / `data-hide` / `data-expanded` / `data-swiping` / `data-highlighted` / `data-side`(tooltip 方位)
内联变量钩子：`--i`（stagger 序号）、`--node-accent`、`--term-count`、`--section-ink/--section-paper`

## 复刻要点速记

1. 全局纸墨双色 `#eaeae8`/`#1a1a19`，分隔线一律 `--hair`（ink 16% transparent）
2. 唯一缓动主角 `cubic-bezier(.16,1,.3,1)`；抽屉 `.32,.72,0,1`；坠落 `.7,0,.84,0`
3. stagger 公式：loading 词 90ms+.12s 基；详情内容 52ms+.26s 基；规则线再 +40ms
4. 文字上滚/翻页 = 外层 overflow:hidden + 固定高 + 内层 translateY(±1.3em)，非 opacity 切换
5. 面板 = 1/3 屏宽，canvas 让位一半；移动端底部抽屉 90dvh
