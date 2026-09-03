# preview_vid_downloader

输入 Creative Studio preview 页面的 `vid`（或完整 preview URL）下载视频。

## 用法

```bash
python3 download_by_vid.py v10033g50000d8haagnog65iv8gp4sa0
```

或直接传 preview 链接：

```bash
python3 download_by_vid.py 'https://ad-creative-studio-platform.tiktok-row.net/preview?vid=v10033g50000d8haagnog65iv8gp4sa0'
```

指定输出目录/文件：

```bash
python3 download_by_vid.py v10033g50000d8haagnog65iv8gp4sa0 -o ./downloads/
python3 download_by_vid.py v10033g50000d8haagnog65iv8gp4sa0 -o ./demo.mp4
```

指定清晰度：

```bash
python3 download_by_vid.py v10033g50000d8haagnog65iv8gp4sa0 -r 720p
python3 download_by_vid.py v10033g50000d8haagnog65iv8gp4sa0 -r best
```

## 依赖

默认下载器只用 Python 标准库，不需要安装 `euler`、`thrift`、`requests` 或内部 IDL 包。

`legacy_scripts/` 里放了用户提供的原始脚本备份；这些脚本依赖内部运行环境（如 `euler`、`bytedtos`、`pypolaris` 和生成的 `idls.*` Python 包），当前本机环境没有这些包，所以默认入口没有使用它们。

## IDL / thrift 说明

这次可运行的下载方案走 preview 页面前端同款 VOD token 流程，不依赖 thrift IDL。

我在 `/Users/bytedance/Downloads` 里没有找到原始脚本 import 的这些 IDL：

- `idls.toutiao.smart_player_thrift`
- `idls.toutiao.guldan_thrift`
- `idls.ad.creative_factory_thrift`
- `idls.base_thrift`

因此 zip 里的 `idls/` 仅保留说明文件。如果后续你提供这些 thrift 或生成后的 `idls/` 目录，可以直接放到这个目录里。
