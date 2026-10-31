# 外刊精读 · Press Reader

每天自动抓取《卫报》《经济学人》《时代周刊》《新闻周刊》《基督箴言报》，为每篇文章生成
**中文标题、导读、逐段对照翻译、重点词汇、长难句解析、背景补充**，再打包成一个可以装到手机桌面的静态站点。

打开就是 App，读完就关掉。不需要账号，不需要装应用商店。

---

## 一、30 秒跑起来

```bash
cd press-reader
pip install -r requirements.txt

python run.py demo      # 先看效果：灌入 5 篇演示文章并构建
python run.py serve     # 浏览器打开 http://127.0.0.1:8000
```

想接真实数据，只要两步：

**① 填 key** —— 打开项目根目录的 `.env`，把 DeepSeek 的 key 贴在等号后面（默认配置就是 DeepSeek，`base_url` 和 `model` 都不用改）：

```
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
GUARDIAN_API_KEY=
```

**② 自检，然后跑**：

```bash
python run.py check     # 验证 key、模型、各刊 feed 是否都通
python run.py all       # 抓取 → 翻译讲解 → 构建
```

`check` 会打印模型服务是否可用、账号下有哪些模型、五个刊物各自的连通状态；哪个源连不上会标 ✗，其余源照常更新。

> **Windows 用户可以不碰命令行**：项目根目录有三个双击即用的文件——
> `check.bat` 自检、`update.bat` 抓取翻译并构建、`preview.bat` 启动本地预览（自动打开浏览器）。
> 它们会自动找 `python`（找不到就用 `py -3`），缺依赖会先装。

没有 `GUARDIAN_API_KEY` 也能跑，卫报会自动降级成 RSS 摘要模式。
`.env` 已在 `.gitignore` 里；线上用 GitHub Secrets，本地用 `.env`，两边互不干扰。

---

## 二、整体思路

一句话概括：**把「抓取—翻译—排版—发布」拆成四个独立的幂等步骤，用定时任务串起来，产物是纯静态文件。**

```
              每天 07:00 (GitHub Actions cron)
                          │
   ┌──────────────────────┴──────────────────────┐
   │  1. fetch   官方 RSS / 卫报 Open Platform    │
   │             去重 → 每天上限 12 篇             │
   └──────────────────────┬──────────────────────┘
                          │  data/enriched/2026-10-31.json
   ┌──────────────────────┴──────────────────────┐
   │  2. enrich  LLM 逐篇生成结构化 JSON           │
   │             内容哈希缓存，重跑不计费            │
   └──────────────────────┬──────────────────────┘
                          │
   ┌──────────────────────┴──────────────────────┐
   │  3. build   Jinja2 → 静态 HTML + PWA         │
   └──────────────────────┬──────────────────────┘
                          │  site/
   ┌──────────────────────┴──────────────────────┐
   │  4. deploy  GitHub Pages（HTTPS + CDN）      │
   └─────────────────────────────────────────────┘
```

拆成四步的好处：任何一步挂了都不影响其他步；数据是文件、可以 git 回溯；本地跑和线上跑完全一致。

### 目录结构

```
press-reader/
├── run.py                  # 命令行入口：all / fetch / enrich / build / demo / serve
├── config.yaml             # 源、模型、每日篇数上限等所有配置
├── src/
│   ├── sources.py          # 抓取层：卫报 API + RSS，统一成同一种结构
│   ├── enrich.py           # 翻译讲解层：prompt、JSON 校验、对齐、缓存
│   ├── render.py           # 渲染层：静态站点、词汇高亮、搜索索引
│   └── util.py             # 文本清洗、时间、读写
├── templates/              # Jinja2 模板
├── assets/                 # CSS / JS / Service Worker / 图标
├── demo/articles.json      # 演示数据（接入真实源后可删）
├── data/enriched/*.json    # 每天一个文件，按日期增量
└── .github/workflows/daily.yml
```

---

## 三、数据来源：为什么这样选

这是整个项目最需要想清楚的地方。直接爬全文最省事，但**侵权、易失效、还会被封**。所以按授权程度分两档：

| 刊物 | 渠道 | 实际拿到的内容 | 讲解档位 |
|---|---|---|---|
| The Guardian 卫报 | 官方 Open Platform API（免费 key） | **完整正文**，动辄 20 段 | `full` 全文逐段对照 |
| TIME 时代周刊 | 官方 RSS | 标题 + 一两句真摘要（15–25 词） | `digest` 摘要精读 |
| The Economist 经济学人 | 官方 RSS | 标题 + 一行 standfirst（7–12 词） | `blurb` 提要拆解 |
| Newsweek 新闻周刊 | 官方 RSS | 常被 Cloudflare 拦截（403） | — |
| CS Monitor 基督箴言报 | 官方 RSS | 同上 | — |

**三档讲解**是这套东西能站住的关键。同一套 prompt 对三种长度的输入要求不同：

| 档位 | 判定 | 讲解重点 | 词汇/例句/背景 |
|---|---|---|---|
| `full` | 正文 ≥ 3 段 | 逐段对照、长难句拆主干 | 6–10 / 2–3 / 1–2 |
| `digest` | 摘要 ≥ 15 词 | 把这段摘要讲透，不补充文章没有的内容 | 4–6 / 1–2 / 1–2 |
| `blurb` | 只有一两行 | 只拆这一句 standfirst，背景讲事实不讲观点 | 3–5 / ≤1 / 1 |

`blurb` 档是专门为经济学人设计的：它 RSS 里放的其实是编辑写的 standfirst（"Her successor as ECB chief will have an unenviable job"），
这句话本身精炼、常有修辞，正是值得精读的句子。系统提示里明令「只依据给出的英文材料，不得编造文章里没有的观点」，
所以宁可讲得少，也不会瞎猜全文内容。

词条来自**柯林斯英汉双解大词典**（见第六节）。

**为什么不爬全文：**

1. **版权**。经济学人、时代都是付费内容，RSS 只放摘要本身就是出版方的意思表示。把付费墙后的正文抓下来再翻译传播，属于典型的侵权，自己看也有风险。
2. **稳定性**。HTML 结构一变爬虫就废，RSS 是出版方承诺维护的接口，十年都不会变。
3. **成本**。全文翻译一篇要几千 token，12 篇/天就是一笔持续开销；摘要精读把成本压到原来的 1/5，而学习收益并没有同比例下降——一段 150 词的摘要里，值得讲的词、句、背景一样齐全。

**卫报是例外**：它的 Open Platform 明确允许在署名 + 回链的前提下使用正文，所以拿到了全文，做真正的逐段对照。申请地址 `open-platform.theguardian.com`，两分钟，免费。

> 站点每篇文章底部都保留了原文链接和版权声明，页面不投放广告、不做二次分发。这是「个人学习用途」的边界，请守住。

---

## 四、翻译与讲解：怎么保证质量

### 1. 一次调用，产出六样东西

`src/enrich.py` 里是一个强约束的 prompt，要求模型只返回 JSON，结构固定：

```json
{
  "title_zh": "中文标题",
  "summary_zh": "80-120 字导读，说清讲了什么、结论是什么",
  "difficulty": "B2 / C1 / C2",
  "paragraphs": [{"en": "...", "zh": "..."}],
  "vocab": [{"word","phonetic","pos","meaning_zh","example_en","example_zh"}],
  "sentences": [{"en","structure","analysis_zh"}],
  "background": [{"title","text_zh"}]
}
```

几个刻意的设计：

- **强制逐段 1:1 对齐**。prompt 里明确「输入几段，输出就几段，顺序一致」，`_normalize()` 还会二次校验段落数——对不上就按索引补齐，宁可少一段译文，也不让页面错位。
- **词汇只挑「影响理解的」**。明确禁止挑 the / of，优先熟词僻义和外刊高频表达。这是精读和背单词表的分界线。
- **长难句要拆主干**。要求输出 `structure` 字段（主干 / 修饰挂在哪），而不是笼统地「这句话很复杂」。
- **背景不复述内容**。背景是讲读者不知道的事（比如加州的鸭子曲线、鲁汶是哪座城市），不是把文章再总结一遍。

### 2. 三个防翻车的机制

| 机制 | 作用 |
|---|---|
| **内容哈希缓存** | `sha1(prompt版本 + 模型 + 文章id + 正文)`，同一篇重跑直接读缓存，不重复计费 |
| **JSON 兜底解析** | 模型偶尔会套 ` ```json ` 代码块或加前言，`_extract_json()` 先剥离再解析，还不行就截取首尾大括号 |
| **并发 + 重试** | 4 线程并发，失败退避重试 3 次；单篇失败只是这一篇没有译文，不影响整站构建 |

### 3. 成本

以 DeepSeek 为例，一篇摘要精读约 2k 输入 + 2.5k 输出 token，12 篇/天大约 5 万 token。
按当前价位折算，**每天约几分钱，一个月不到 2 元**。全文化（卫报那几篇）会高一些，仍在个位数。

模型可换：`config.yaml` 里改 `base_url` + `model` 即可，任何 OpenAI 兼容接口都行（通义、Kimi、GLM、本地 Ollama 都可以）。

DeepSeek 当前有两个模型，实测同一篇 6 段文章：

| 模型 | 单篇耗时 | 译文质量 | 建议 |
|---|---|---|---|
| `deepseek-flash` | 约 20 秒 | 自然，偶有口语化 | **默认**，12 篇约 1 分钟 |
| `deepseek-v4-pro` | 约 67 秒 | 更贴原文句式 | 想精修时手动切 |

切模型前先跑 `python run.py check` 看账号下有哪些模型名——名字对不上会直接 400。

---

## 五、更新方式：为什么用 GitHub Actions

| 方案 | 问题 |
|---|---|
| 自己买服务器跑 cron | 要钱、要运维、挂了没人知道 |
| 本地电脑定时任务 | 电脑关机就不更新 |
| **GitHub Actions** | 免费额度足够（每天一次构建约 1 分钟），失败会发邮件，日志可查，还能手动点一下补跑 |

工作流在 `.github/workflows/daily.yml`：

- `cron: "0 23 * * *"` → UTC 23 点 = **北京时间早上 7 点**，起床就有新内容。
- `workflow_dispatch` 保留手动触发，方便补跑。
- 构建完把 `data/enriched/*.json` 提交回仓库 —— 这样历史数据不丢，去重靠它，回滚也靠它。
- 用官方 `actions/deploy-pages` 发布，走 GitHub 的 CDN 和 HTTPS。

**去重逻辑**：`sources.collect()` 会先读所有历史 JSON 里的 id，抓到的文章 id 命中就跳过。所以重复运行不会重复入库，也不会重复花钱。

**每天限流**：`max_per_source: 3` + `total_cap: 12`。这是刻意的——每天 12 篇精读已经远超大多数人的阅读量，多了只会变成未读红点。

**归档**：保留 45 天（`days_keep`），超期自动不渲染，但 JSON 文件仍在仓库里，随时可以调回来。

---

## 六、点词查词：柯林斯英汉双解

正文里**任意一个英文单词**都能点，点下去从底部弹出词条：音标、柯林斯星级、词性、英文释义、中文释义、双语例句。

### 词条从哪来

柯林斯官方接口（`api.collinsdictionary.com`）需要商业授权，而且只有英文释义；中文的「双解」内容是国内出版方引进的。
实际可得的是有道词典的公开查询接口，返回体里的 `collins` 字段就是**完整的柯林斯词条**——星级、词性、英文释义、中文译义、双语例句都在里面。
`src/lexicon.py` 做的就是解析这个字段，把它归一化成统一结构。

```python
# 请求
GET https://dict.youdao.com/jsonapi?q=arithmetic
# 取用
data["collins"]["collins_entries"][0]["star"]        # 柯林斯星级 1~5
data["collins"]["collins_entries"][0]["entries"]["entry"][i]["tran_entry"][j]
    ["pos_entry"]["pos"]        # N-UNCOUNT
    ["tran"]                    # 英文释义 + 结尾的中文译义
    ["exam_sents"]["sent"]      # [{eng_sent, chn_sent}]
```

释义结尾的中文是靠正则切出来的（`_split_en_zh`）：从最后一个中文字符起截到句末，前面是英文释义，后面是中文译义。

### 为什么是构建时预取，而不是打开页面时现查

| 方案 | 问题 |
|---|---|
| 浏览器直接请求词典接口 | 跨域被拦（接口没有 CORS 头），且每次点击都要等一次网络 |
| 自建一个查询后端 | 又要一台服务器，违背「纯静态」的初衷 |
| **构建时预取 + 内嵌** | 点下去 0 延迟，离线可用，不需要任何后端 |

做法是：构建文章页时，把正文里所有实词（去掉 the / of 这类虚词，按词频排序取前 170 个）查一遍，
结果写进 `data/lexicon.json` 长期缓存，再把这篇用到的词压进页面里的一个 `<script type="application/json">`。

- **跨天复用**：同一个词只查一次，第二天的新文章里再出现，直接命中缓存。
- **体积**：一篇约 150 个词，压缩后内嵌约 100KB（gzip 后 30KB 上下），一次性随页面加载。
- **速度**：6 线程并发，400 个词约 17 秒。日常增量只有几十个新词，几秒就完。

### 点词的实现

没有给每个单词套 `<span>`（会让 HTML 膨胀、还会打乱标点）。用的是浏览器的光标定位 API：

```js
const r = document.caretRangeFromPoint(x, y);   // 拿到点击位置的文本节点和偏移
// 再用正则在这段文本里向两侧扩展到完整单词
```

拿到单词后做词形还原再查：`spreads → spread`、`flattened → flatten`、`industries → industry`……
命中就渲染面板；没命中显示「柯林斯未收录该词」，不会弹一个空壳。

面板是底部抽屉（bottom sheet）：遮罩 + 圆角 + 上滑动画，`max-height 76vh` 可内部滚动，
点空白、点 ✕、按 Esc 都能关。被点的词用 CSS Highlight API 高亮（不支持的浏览器自动跳过，不影响功能）。

### 换成官方柯林斯 / 别的词典

`config.yaml`：

```yaml
lexicon:
  provider: youdao          # 改成 collins_official
  collins_api_key: ""       # 填官方 accessKey
```

`src/lexicon.py` 里 `fetch_collins_official()` 已经把官方返回结构对齐好了。
想接欧路、必应、或本地 ECDICT 词库，实现一个返回同样结构的函数，在 `Lexicon._fetch_one()` 里分流即可。

---

## 七、手机端阅读体验

这不是一个「能看」的网页，是按手机阅读器标准做的：

**排版**
- 单列，最大宽度 720px，左右留白 16px；正文 18px，行高 1.82，英文用衬线字体（Iowan Old Style → Palatino → Georgia），中文用系统黑体。英文衬线 + 中文黑体是双语排版最舒服的组合。
- 段落间距 26px，译文的字号比原文小 2px、左侧加竖线、带「译」标记 —— 一眼能分清哪句是原文、哪句是译文。

**交互**
- **三种阅读模式**：`对照 / 原文 / 译文`，正文上方吸顶切换。推荐用法：第一遍对照读懂，第二遍切原文检验，卡住再切译文。模式记在 localStorage，下次打开还是你上次的选择。
- **点词查词**：正文里任意英文单词都能点，弹出柯林斯词条（详见第六节）。带虚线的词是本篇重点词，面板里会额外挂上大模型的精读批注——同一个弹层，两种信息。
- **重点词高亮**是构建时用 Python 正则做的：先算出所有匹配区间再拼接字符串，避免标签互相嵌套把 HTML 写坏。
- **阅读进度条**、**字号三档**（A− / A+）、**深浅色主题**（跟随系统，可手动锁定）。
- 底部固定三个 Tab（今日 / 归档 / 关于），全部按钮 ≥ 36px，适配 iPhone 安全区（`env(safe-area-inset-*)`）。

**性能**
- 零前端框架，`app.js` 约 5KB，`style.css` 约 12KB，首屏无阻塞请求，3G 下也能秒开。
- 首页只渲染最近 5 天，归档页独立，长列表不做虚拟滚动（纯 HTML 更快）。
- 搜索是构建时生成 `search-index.json`（约 30KB），前端做子串匹配，没有后端。

**离线**
- `manifest.webmanifest` + `sw.js`，用 Safari/Chrome 打开后「添加到主屏幕」，就是一个全屏 App。
- Service Worker 策略：HTML 走「网络优先、失败回退缓存」，静态资源走「缓存优先」。所以有网时拿到当天新文章，没网时读过的文章照常打开。

---

## 八、部署：三条路，按需选

### 方案 A：GitHub Pages（推荐，免费 + HTTPS + CDN）

1. 新建仓库，把本项目推上去。
2. `Settings → Pages → Source` 选 **GitHub Actions**。
3. `Settings → Secrets and variables → Actions` 添加：
   - `LLM_API_KEY`
   - `GUARDIAN_API_KEY`
4. `Actions → 每日更新外刊 → Run workflow` 手动跑一次。
5. 手机打开 `https://<用户名>.github.io/<仓库名>/`，Safari 分享 → 添加到主屏幕。

> 如果仓库名不是 `<用户名>.github.io`，站点在子路径下。此时把 `config.yaml` 的 `base_url` 填成完整地址
> （如 `https://name.github.io/press-reader`），manifest 的 `start_url` 才会正确指向首页。

### 方案 B：Cloudflare Pages

仓库连过去，构建命令 `python run.py all`，输出目录 `site`，环境变量同上。国内访问通常比 GitHub Pages 稳。

### 方案 C：局域网预览（改样式时用）

```bash
HOST=0.0.0.0 python run.py serve
```

终端会打印一个 `http://192.168.x.x:8000` 的地址，手机连同一个 Wi-Fi 直接访问。改完模板刷新即可，不用等 CI。

---

## 九、常见改动

| 想做的事 | 改哪里 |
|---|---|
| 换模型 / 换供应商 | `config.yaml` → `llm.base_url` + `llm.model` |
| 增加一个刊物 | `config.yaml` → `sources` 加一项，`type: rss` 填 feeds |
| 每天多抓几篇 | `fetch.max_per_source` / `total_cap` |
| 改讲解风格（更应试 / 更轻松） | `src/enrich.py` 顶部的 `SYSTEM_PROMPT`，记得同时改 `PROMPT_VERSION` 让缓存失效 |
| 换配色 | `assets/style.css` 顶部 `:root` 变量 |
| 中文标题太长 | `templates/_card.html` 里加 `| truncate(n)` |
| 每篇收录更多生词 | `lexicon.max_words_per_article`（越大页面越重） |
| 换词典源 | `lexicon.provider`，见第六节 |

---

## 十、已知限制

- **四个刊只有摘要**（经济学人 / 时代 / 新闻周刊 / 箴言报）。这是版权边界，不是技术限制。想读全文请订阅原刊，本站负责帮你把摘要读透。
- **机器翻译需要抽查**。模型偶尔会把长句的主语挂错，尤其是带插入语的句子。页面保留了原文对照，就是让你能自己核对。
- **RSS 偶尔抽风**。某个源 403/超时是常态，抓取层已经隔离，不会影响其他源；连续几天抓不到再去看是不是换了 feed 地址。
- **演示数据是虚构的**。`demo/articles.json` 里的 5 篇是按真实刊物风格写的示例文章，用于预览界面和校验翻译质量，不代表任何真实报道。

---

## 十一、许可

代码 MIT。抓取到的英文内容版权归原出版方所有；中文译文与讲解为本项目生成的二次创作，仅供个人学习，请勿商用或大规模转载。
