# Logo / 图标资产

标记 = 站点第 05 章「协作拓扑」的 2D 简化版：
**中央六边形 = `.agents/` 共享黑板，六个平台色节点 = 六个 AI 会话，
六条连线 = 每个会话读规则 / 写心跳 / 读指令。**

色值全部取自 `site/assets/css/style.css` 的设计令牌（信号青 `#46E0FF` ×
琥珀 × 深底 `#05070C`），六个节点色与 `--c-cld…--c-wb0` 一一对应。

## 文件

| 文件 | 用途 |
|---|---|
| `mark.svg` | 标记本体，暗底全彩。用在深色页面、深色 PPT |
| `mark-mono.svg` | 单色版，用 `currentColor` —— 放进任意容器，改 `color` 就换色 |
| `favicon.svg` | 浏览器标签图标。16px 下彩色圆点会糊，所以去掉圆点、加粗连线； 内含 `prefers-color-scheme` 分支，浅色标签栏自动换成深青 |
| `favicon-16.png` `favicon-32.png` | 上面那个的 PNG 兜底（给不认 SVG favicon 的老浏览器） |
| `logo.svg` | 横向锁定版，**暗底**用。含 wordmark（已转矢量路径，不依赖查看端字体） |
| `logo-light.svg` | 横向锁定版，**亮底**用。六个节点色换成加深版，否则浅青 / 青柠在白底上糊掉 |
| `logo-dark.png` `logo-light.png` | 锁定版的位图（1600px 宽，2x），给不收 SVG 的地方用 |
| `icon-tile.svg` | 圆角磁贴（22% 圆角），头像 / 应用图标 / 商店图 |
| `icon-square.svg` | 直角满幅。**iOS 主屏图标与 PWA 要用这个** —— 系统会自己切圆角，给圆角图会被二次裁切 |
| `apple-touch-icon.png` `icon-192.png` `icon-512.png` | 上两个的位图导出 |
| `og-image.svg` `og-image.png` | 社交分享卡 1200×630 |

## 改图形

不要手改 SVG —— 几何是算出来的（六重对称，手改一处必歪）。改
`_make.py` 里的参数再重跑：

```bash
python site/assets/logo/_make.py          # 需要 fonttools + brotli
```

脚本只依赖仓库里自托管的 `site/vendor/fonts/`（Sora 800 出 wordmark，
PlexMono 500 出副标），不联网。

`_make.py` 只生成 SVG。位图导出用无头 Chrome 渲染（playwright-core + 系统
Chrome），把 SVG 按目标宽度塞进页面再截元素 —— 只固定宽度、高度交给 SVG
自己的宽高比，否则非正方形的锁定版会被拉扁：

```js
// svg{display:block;width:<目标宽>px;height:auto}
// 然后 locator('svg').screenshot({ path: out, omitBackground: true })
```

尺寸对照：favicon 16 / 32，apple-touch-icon 180，PWA 192 / 512，
社交卡 1200×630，锁定版位图 1600 宽。

## 用在 README

GitHub 支持 `<picture>` 按主题切换，所以两套锁定版都能用上：

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="site/assets/logo/logo.svg">
  <img alt="multi-agent-collab" src="site/assets/logo/logo-light.svg" width="440">
</picture>
```
