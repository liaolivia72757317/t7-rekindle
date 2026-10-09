# 品牌素材

| 素材 | 尺寸 | 用途 |
| --- | --- | --- |
| [cover.png](cover.png) | 2173 × 724 | 项目主页封面 |
| [logo.png](logo.png) | 1254 × 1254 | 独立徽记 |
| [app-icon.png](app-icon.png) | 1254 × 1254 | 应用图标源图：浅色底、金银 T7 与红色 R 印记 |
| [logo-horizontal.png](../../src/Desktop/Resources/Assets/brand/logo-horizontal.png) | 2172 × 724 | 品牌横版 Logo |
| [logo-stacked.png](logo-stacked.png) | 1254 × 1254 | 竖版 Logo |
| [logo-wordmark.png](logo-wordmark.png) | 2172 × 724 | 简洁横版 Logo |
| [logo-horizontal-tagline.png](logo-horizontal-tagline.png) | 2172 × 724 | 带标语的横版 Logo |
| [logo-stacked-tagline.png](logo-stacked-tagline.png) | 1254 × 1254 | 带标语的竖版 Logo |
| [logo-stacked-tagline-sidebar.png](../../src/Desktop/Resources/Assets/brand/logo-stacked-tagline-sidebar.png) | 1254 × 1254 | 带标语的侧栏竖版 Logo，作为品牌变体保留 |
| [logo-stacked-no-slogan.png](../../src/Desktop/Resources/Assets/brand/logo-stacked-no-slogan.png) | 1254 × 1254 | 无标语竖版 Logo 源图 |
| [logo-stacked-no-slogan-sidebar.png](../../src/Desktop/Resources/Assets/brand/logo-stacked-no-slogan-sidebar.png) | 1062 × 962 | 当前侧栏无标语竖版 Logo，裁去多余留白 |

以上 PNG 按表中尺寸保存并保留透明通道（封面为不透明图片）。

[app-icon-derived.ico](../../src/Desktop/Resources/Assets/brand/app-icon-derived.ico)
由 `app-icon.png` 生成，包含 16、20、24、32、40、48、64、96、128、256 像素尺寸，作为品牌图标衍生素材保留。

启动器当前使用以下资源：

- [logo-stacked-no-slogan-sidebar.png](../../src/Desktop/Resources/Assets/brand/logo-stacked-no-slogan-sidebar.png)：浅色主题侧栏 Logo，在 216 × 180 DIP 品牌区内居中等比例显示，边距为左右 24、上下 15 DIP，并围绕中心放大至 1.05 倍；小窗口等比例缩小，深色及高对比度主题使用文本标识。资源与源图均随仓库发布，由 Desktop 项目嵌入应用。
- [launcher-background.png](../../src/Desktop/Resources/Assets/art/launcher-background.png)：浅色主题窗口背景“静谧山门与远山墨韵”，保留 1476 × 1065 原始尺寸，以 `UniformToFill` 等比例铺满窗口；由 Desktop 项目嵌入应用，深色及高对比度主题沿用纯色背景。
- [home-banner.png](../../src/Desktop/Resources/Assets/art/home-banner.png)：首页轮播区域的单张展示图“刀锋再起：熟悉的武将，久违的交锋。”，画面为赵云与夏侯惇对阵，保留 1989 × 790 原始尺寸，由 Desktop 项目嵌入应用；展示区随宽度保持原图比例，小窗口可滚动查看，不叠加旧标题，深色及高对比度主题保留文字回退。
- [launcher-full-2560x1920.png](../../src/Desktop/Resources/Assets/art/launcher-full-2560x1920.png)：浅色主题对战页状态卡背景。
- [launcher.ico](../../src/Desktop/Resources/Assets/brand/launcher.ico)：由 `app-icon.png` 生成，供应用、窗口、托盘及关于页使用；包含 16、20、24、32、40、48、64、96、128、256 像素尺寸，保留透明通道。
