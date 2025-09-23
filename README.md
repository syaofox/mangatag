#### 抓取

##### 使用漫画编号
uv run manhuagui.py 1055

##### 使用完整URL
uv run manhuagui.py "https://www.manhuagui.com/comic/1055/"

#### 限制处理章节数量（用于测试）
uv run manhuagui.py 1055 --limit 5

# 查看帮助
uv run manhuagui.py --help


遍历 `outputs/漫画名/章节目录`，从文件夹名前缀获取序号并更新对应的 `ComicInfo.xml` 中的 Number 字段，支持可选的 dry-run 和详细输出。


### 使用方法
- 基本用法
```bash
uv run python update_xml_numbers.py "outputs/漫画名目录"
```
- 试运行（不写回文件）
```bash
uv run python update_xml_numbers.py "outputs/漫画名目录" --dry-run -v
```
- 显示详细日志
```bash
uv run python update_xml_numbers.py "outputs/漫画名目录" -v
```

### 行为说明
- 遍历 `outputs/漫画名/章节目录` 的每个章节文件夹。
- 解析章节文件夹名开头的数字前缀（如 `001-第01卷`, `012_特典`, `3 第3话`）。
- 更新对应 `ComicInfo.xml` 的 `<Number>` 为该前缀（保持前导零）。
- 兼容两种 XML 存放结构：
  - `outputs/漫画名/章节目录/ComicInfo.xml`
  - `outputs/漫画名/章节目录/xml/ComicInfo.xml`





`update_archives_with_xml.py`。功能：读取指定漫画的 XML 目录，按 XML 中的 Title 对漫画目录下的 .cbz/.zip 进行模糊匹配，在压缩包根目录添加或覆盖 `ComicInfo.xml`。

用法示例（按你给的路径）:
- 先试运行查看匹配与计划更新
```bash
uv run python update_archives_with_xml.py "/home/syaofox/dnas/data/adult/books/comic/连载中/[北崎拓,あかほり悟]天漫浮世錄[manhuagui]" "/home/syaofox/data/dev/mangatag/outputs/天漫浮世錄" --dry-run -v
```
- 实际写入（若压缩包已存在 ComicInfo.xml 且需覆盖，加 --force）
```bash
uv run python update_archives_with_xml.py "/home/syaofox/dnas/data/adult/books/comic/连载中/[北崎拓,あかほり悟]天漫浮世錄[manhuagui]" "/home/syaofox/data/dev/mangatag/outputs/天漫浮世錄" --force -v
```

参数要点:
- `--threshold 0.60`：模糊匹配阈值（默认 0.60，可调高低以适配“章节名有部分相同”的情况）
- `--dry-run`：试运行不写入
- `--force`：存在则覆盖
- `-v`：详细日志

兼容 XML 路径:
- `outputs/漫画名/章节目录/ComicInfo.xml`
- `outputs/漫画名/章节目录/xml/ComicInfo.xml`

匹配逻辑会对标题和文件名做规范化（小写、去空白和常见符号），提升“部分相同”时的命中率。



已增加“用章节文件夹名进行模糊匹配”的方式，并支持三种匹配策略：
- both（默认）：同时以 XML 的 Title 与 章节文件夹名 比对，取更高分
- title：仅用 Title 匹配
- folder：仅用 文件夹名 匹配

使用示例
- 试运行（两种方式择优匹配，显示详细日志）
```bash
uv run python update_archives_with_xml.py "/home/syaofox/dnas/data/adult/books/comic/连载中/[北崎拓,あかほり悟]天漫浮世錄[manhuagui]" "/home/syaofox/data/dev/mangatag/outputs/天漫浮世錄" --dry-run -v --strategy both
```
- 仅使用文件夹名匹配并写入（若已存在则覆盖）
```bash
uv run python update_archives_with_xml.py "/home/syaofox/dnas/data/adult/books/comic/连载中/[北崎拓,あかほり悟]天漫浮世錄[manhuagui]" "/home/syaofox/data/dev/mangatag/outputs/天漫浮世錄" --strategy folder --force -v
```
- 调整匹配阈值（章节名部分相同时可降低一些，比如 0.5）
```bash
uv run python update_archives_with_xml.py "<comic_dir>" "<xml_root>" --threshold 0.5 --dry-run
```

说明
- 规范化匹配：会将字符串小写、去空白和常见符号，提高“部分相同”情况下的命中率。
- 支持的XML位置：`outputs/漫画名/章节目录/ComicInfo.xml` 和 `outputs/漫画名/章节目录/xml/ComicInfo.xml`
- 写入策略：默认不覆盖已有 `ComicInfo.xml`，加 `--force` 才会覆盖。