# 发行构建

需要 Python 3.11+ 和 Blender 5.0+。在源码根目录运行：

```text
python tools/build_release.py --blender "你的 Blender 可执行文件路径"
```

脚本使用显式文件清单，先校验源码，再调用 Blender 官方扩展构建命令，校验 ZIP 内容，并在独立后台 Blender 中解包验证注册、打组、关联编辑、独立复制和停用。

默认输出到 `release/`：

- `SketchGroup-v1.0.0.zip`：可安装扩展。
- `SketchGroup-v1.0.0-source/`：适合提交到 GitHub 的源码与文档。
- `SketchGroup-v1.0.0-source.zip`：同一份公开源码快照。
- `SHA256SUMS.txt`：两个 ZIP 的 SHA-256。
- `RELEASE_NOTES.md`：GitHub Release 说明。

已有同名产物时不会覆盖；使用 `--output <新的目录>` 重新构建。增加运行时模块或公共文档后，应更新构建脚本的文件清单。

发布前还应在 Blender 中从磁盘安装 ZIP，检查双击／框外退出、快捷键、嵌套和遮罩。后台验证不包含鼠标事件或 GPU 显示。

GitHub Release 标题使用 `SketchGroup v1.0.0`，标签使用 `v1.0.0`；附上可安装 ZIP 和 `SHA256SUMS.txt`，源码 ZIP 可一同提供。上传完成后核对附件哈希。

扩展构建与校验参见 [Blender 官方命令说明](https://docs.blender.org/manual/en/latest/advanced/command_line/extension_arguments.html)。本地验证通过不等于 Blender 官方扩展平台审核通过。
