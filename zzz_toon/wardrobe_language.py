"""Visible strings in the wardrobe panel. The choice is local to the scene."""
import re

EN = {
    '进入全屏预览':'Enter fullscreen preview', '相机视图中滚轮推拉 · 空格播放':'Wheel to zoom · Space to play',
    '全身 ↔ 近景':'Full body ↔ Close-up', '角色':'Character', '工程目录':'Output',
    '配置并打开服装预览':'Build and open outfit preview', '生成的工程':'Generated project',
    '使用所选角色的参数在独立工程中生成可播放的服装预览':'Build a playable outfit preview in a new project',
    '播放待机。左键拖动转身或调高度，滚轮推拉，Esc 返回':'Play the idle. Drag to turn or adjust height, scroll to zoom, Esc to return',
    '正在生成预览工程':'Building the preview project', '正在配置角色、待机与滚动背景…':'Building character, idle and scrolling background…',
    '角色模型 FBX':'Character model FBX', '预览待机动画':'Preview idle animation', '模型 FBX':'FBX', '待机动画':'Idle',
    '整张滚动背景':'Tiled background image', '辅助 UV':'UV', '内置辅助 UV':'Use bundled secondary UV',
    '脸部与眼睛':'Face and eyes', '头发':'Hair', '服装':'Outfit', '武器与羽翼':'Weapon and wings',
    '采样图集':'Mip atlases', '颜色与细纹':'Color and fine detail', '滚动背景':'Scrolling background',
    '几何辅助数据':'Geometry inputs', '使用下方三层页面素材':'Use the three layers below',
    '自动匹配或生成':'Match or generate automatically', '使用内置文件':'Use bundled file', '选择文件':'Choose file',
    '脸部轮廓顶点数据':'Face outline vertices', '大字图案':'Large lettering', '小字图案':'Small lettering',
    '背景明暗底图':'Background shading', '表面细纹':'Surface detail', '角色调色 LUT':'Character color LUT',
    '面部光照图':'Face lightmap', '眼睛颜色查找表':'Eye color lookup', '脸部':'Face', '宝石':'Gem', '羽翼':'Wings', '武器':'Weapon',
    '底色':'Base color', '法线与光照':'Normal and lighting', '材质遮罩':'Material mask', '辅助通道':'Auxiliary channels',
    '原始网格':'Source mesh', '描边网格':'Outline mesh', '表面顶点数据':'Surface vertices', '脸部顶点数据':'Face vertices',
    '眼部顶点数据':'Eye vertices', '辅助 UV 数据':'Secondary UV data', '面部投影遮挡数据':'Face shadow receiver',
    '请填写有效文件：':'Select valid files: ', '动画与所选角色不匹配：':'Idle does not match the character: ',
    '模型的骨架、蒙皮、网格或材质槽与所选服装不匹配：':'The rig, skin, meshes or material slots do not match the outfit: ',
    '待机动画没有绑定到所选模型的骨架':'The idle has no tracks bound to this model',
    '角色模型槽需要 FBX 文件':'The model slot requires an FBX file',
    '预览待机动画槽需要 Unity .anim 或 AnimationClip JSON':'The idle slot requires Unity .anim or AnimationClip JSON',
    '、':', ', '；':'; ', ' 槽 ':' slot ',
}
_TERMS = re.compile('|'.join(re.escape(key) for key in sorted(EN, key=len, reverse=True)))


def text(settings, value):
    if settings.wardrobe_language == 'EN':
        return _TERMS.sub(lambda m: EN[m[0]], value)
    return value
