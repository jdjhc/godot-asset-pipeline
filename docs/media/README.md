# 工作流演示素材

四段 GIF 均为 1080 × 720，按每帧 140 ms 编码。字幕描述实际操作及其边界。

| 文件 | 展示能力 |
| --- | --- |
| 01-subject-selection.gif | 可视化选择、固定原始参考、自动建立主体输入 |
| 02-video-views.gif | 视频候选帧、方位唯一性约束、自动创建多视图节点 |
| 03-versions-rebuild.gif | 历史输入追溯、真实版本比较、失效传播、重生成范围预览 |
| 04-canvas-management.gif | 自动布局、画布级移除与撤销、跨画布共享资产 |

## 录制方式

`tools/prepare_workflow_demo.py --project /path/to/project` 创建独立临时项目，备份 SQLite 数据库并通过硬链接读取已有产物。所有编辑只作用于副本；不要手动覆盖副本中的硬链接文件。演示仅保留相关节点可见，不修改原项目。

为副本启动 `addons/asset_pipeline/backend/server.py --project /path/to/demo`，再通过 Godot 的 `--path /path/to/demo --script /path/to/tools/capture_workflows.gd -- /path/to/frames` 录制。使用真实面板与真实本地后台，执行框选保存、选帧确认、依赖检查、画布整理、撤销和复用。录制脚本没有 `node.run` 或 `batch.run` 调用。

视频候选帧已预先解码；历史模型已经生成。版本演示只预览重生成范围，不伪造生成进度。操作间等待被压缩，因此 GIF 不代表服务耗时或性能基准。

最后使用 `python tools/encode_workflows.py /path/to/frames` 编码（需要 Pillow）。运行结束后关闭演示后台并删除临时项目即可。

旧版 `asset-pipeline-showcase.gif` 仅作为早期模型预览演示留存，README 已采用四段工作流演示。

示例画面参考《绝区零》街区设计，不声明为插件原创美术；此目录不包含可下载的 3D 模型。
