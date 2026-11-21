# ZZZ Toon · 绝区零服装预览 Blender 插件

[English](README.md) · [下载插件](https://github.com/Gartchen/ZZZ-Toon-Shading/releases/latest) · [卡通渲染复现教程](docs/shading.zh-CN.md)

在 Blender EEVEE 中搭建可编辑的绝区零服装预览场景。选择 Remielle、Velina 或 Vivian，填入本地模型、贴图和待机动画，即可全屏播放。角色居中，背景循环滚动，镜头可以在全身和近景之间连续推拉。


## 从安装到第一次预览

1. 准备 **Blender 5.3**。本版在 **Windows、Blender 5.3.0** 上测试。在[发布页](https://github.com/Gartchen/ZZZ-Toon-Shading/releases/latest)下载 **zzz_toon-0.47.5-public.1.zip**。
2. 打开 **编辑 → 偏好设置 → 插件**，在右上角菜单选择 **从磁盘安装**，选中刚下载的 ZIP，启用 **ZZZ Toon**。
3. 新建场景或打开[示例工程](examples)。在 **3D 视图**按 **N**，点侧栏里的 **ZZZ Toon**，选择 **中文**或 **English**，再选角色。
4. 填写带蒙皮的 **模型 FBX** 和匹配的 **待机动画**。展开脸部、头发、服装、武器及 MatCap 分组，按槽位标签逐张选择贴图。文件可以改名，标签会标明所需内容。三个角色的槽位有所不同。
5. 在 **滚动背景** 中选一张可平铺的图片，或者填齐三层页面素材。留空时会使用插件自带的条纹摄影棚背景。选择可写的 **工程目录**。
6. 点击 **配置并打开服装预览**。Blender 会在独立工程中导入骨架和动作、创建材质与合成节点，随后打开生成的 `.blend`。点击 **进入全屏预览** 即可播放。要导出静帧或动画，使用 **渲染 → 渲染图像 / 渲染动画**。

| 全屏操作 | 效果 |
| --- | --- |
| 按住左键左右拖 | 转动角色 |
| 按住左键上下拖 | 上下调整镜头取景 |
| 滚轮向上 / 向下 | 连续拉近到特写 / 拉远到全身 |
| Esc | 返回原来的布局 |

## 需要准备哪些输入

| 输入 | 要求 |
| --- | --- |
| 模型 | 与所选服装对应的 FBX。保留骨架、网格与材质槽名称、顶点色、蒙皮权重、UV 和三角面顺序。部分角色可另填带额外 UV 的辅助 FBX。 |
| 待机动画 | 已烘焙的 Unity AnimationClip 文本 `.anim`，或[动画读取器](zzz_toon/clip_io.py)支持的解析后 JSON。骨骼路径需要与模型匹配。 |
| 材质贴图 | 填齐可见的基础贴图槽，包括列出的 MatCap、面部光照图和眼睛查色表。Remielle 和 Velina 各有 24 个基础槽位，Vivian 有 23 个，含伞的材质组。 |
| 背景 | 一张可平铺的彩色背景图，或还原原页面布局所需的三层素材。生成的画面只保留角色和滚动背景。 |
| 可选参考数据 | 根据槽位提供角色 LUT、原始 mip 图集、细纹图、辅助 UV 或匹配的逐网格与面部描边数据。某些辅助文件使用项目专用格式。详见[输入说明](docs/shading.zh-CN.md#本地辅助输入)。 |

只填基础输入也可以生成场景。插件会读取 FBX 自带的表面属性并生成缺少的 mip 层级。角色 LUT 留空时，该调色步骤直接通过。补齐匹配的抓帧辅助数据后，阴影分界、高光朝向、细线和透明层更接近参考。下方展示图采用完整本地参考配置。发布包提供着色器实现、服装数值预设和原创背景样片，游戏素材由用户在本机填写。

三个示例分别预选了 [Remielle](examples/Remielle.blend)、[Velina](examples/Velina.blend) 和 [Vivian](examples/Vivian.blend)，输入槽留空。填入角色前可先按 **F12**，查看示例自带的程序化两色材质球。

**公开安装包的实测输出。** 上排只填角色模型、基础贴图和待机动画，使用默认背景。下排加入本地辅助输入和原页面图层。这张小图用于检查两种输入组合，下面的展示图可放大看材质细节。

![Remielle · Velina · Vivian](docs/media/fresh-install.jpg)

## 效果展示

近景图将游戏角色颜色目标与 Blender 匹配机位用同一范围裁切。全身图依次展示原生抓帧、全身录屏和对应 Blender 镜头。Remielle 和 Velina 的原生抓帧只拍到大腿，Vivian 的抓帧包含伞顶和双脚。旋转 GIF 均取自上一轮 4K60 Blender 成片已经推到近景之后的一整圈。

### Remielle

![Remielle 原生抓帧和 Blender 近景](docs/media/remielle-close.jpg)

![Remielle 原抓帧、全身游戏录屏和 Blender](docs/media/remielle-full.jpg)

![Remielle Blender 近景转身](docs/media/remielle-turn.gif)

### Velina

![Velina 原生抓帧和 Blender 近景](docs/media/velina-close.jpg)

![Velina 原抓帧、全身游戏录屏和 Blender](docs/media/velina-full.jpg)

![Velina Blender 近景转身](docs/media/velina-turn.gif)

### Vivian

![Vivian 原生抓帧和 Blender 近景](docs/media/vivian-close.jpg)

![Vivian 原抓帧、全身游戏录屏和 Blender](docs/media/vivian-full.jpg)

![Vivian Blender 近景转身](docs/media/vivian-turn.gif)

## 常见问题

| 现象 | 先检查哪里 |
| --- | --- |
| 找不到插件面板 | 确认插件已启用，将鼠标移到 3D 视图再按 N。 |
| 模型或动画被拒绝 | 核对所选服装、骨骼路径与材质槽；合并网格或重排顶点会破坏辅助数据对应关系。 |
| 武器过暗或伞缺细节 | 先看武器组 D/N/M/A 和 MatCap 的分配，再看匹配的表面数据。 |
| 刘海完全遮眼、脸色过浅 | 核对眼睛查色表、顶点色、头发透明度、面部辅助数据和角色 LUT，并在相同角度对比。 |
| 预览播放较慢 | 公开版在本机实时生成画面。全屏待机预录缓存未随包分发，时间轴可能跳帧；可用“渲染动画”逐帧导出高质量成片。 |

游戏美术和参考画面归《绝区零》创作者所有。本项目由独立开发者制作。[复现教程](docs/shading.zh-CN.md)说明了哪些输入来自抓帧，哪些步骤是 Blender 近似，哪些参数经过单角色观感校准。

开发过程中参考的社区着色器实现见 [HoyoToon](https://github.com/Hoyotoon/HoyoToon)。
