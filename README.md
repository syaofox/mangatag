# MangaTag - 漫画信息管理工具

一个用于从漫画柜网站抓取漫画信息并生成 ComicInfo.xml 文件的工具集，支持批量处理和管理漫画元数据。

## 功能特性

- 🚀 **异步抓取**：使用 aiohttp 实现高效的异步网络请求
- 📚 **智能解析**：自动提取漫画标题、作者、类型、状态等信息
- 🎯 **精确匹配**：支持多种匹配策略，智能识别章节对应关系
- 🔧 **批量处理**：支持批量更新 XML 文件和压缩包
- 🛡️ **安全操作**：支持试运行模式，避免误操作

## 安装

### 环境要求
- Python 3.10+
- uv 包管理器（推荐）

### 安装依赖
```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -r requirements.txt
```

## 工具说明

### 1. manhuagui.py - 漫画信息抓取工具

从漫画柜网站抓取漫画信息并生成 ComicInfo.xml 文件。

#### 使用方法

**使用漫画编号**
```bash
uv run manhuagui.py 1055
```

**使用完整URL**
```bash
uv run manhuagui.py "https://www.manhuagui.com/comic/1055/"
```

**限制处理章节数量（用于测试）**
```bash
uv run manhuagui.py 1055 --limit 5
```

**查看帮助**
```bash
uv run manhuagui.py --help
```

#### 输出结构
```
outputs/
└── 漫画名/
    ├── 001-第01卷/
    │   └── ComicInfo.xml
    ├── 002-第02卷/
    │   └── ComicInfo.xml
    └── ...
```

#### 特性
- 支持漫画编号和完整URL两种输入方式
- 自动创建目录结构
- 生成标准 ComicInfo.xml 格式
- 包含漫画标题、作者、类型、状态等完整信息
- 异步处理，支持限速避免被封

### 2. update_xml_numbers.py - XML序号更新工具

遍历漫画目录，从章节文件夹名前缀获取序号并更新对应的 ComicInfo.xml 中的 Number 字段。

#### 使用方法

**基本用法**
```bash
uv run python update_xml_numbers.py "outputs/漫画名目录"
```

**试运行（不写回文件）**
```bash
uv run python update_xml_numbers.py "outputs/漫画名目录" --dry-run -v
```

**显示详细日志**
```bash
uv run python update_xml_numbers.py "outputs/漫画名目录" -v
```

#### 行为说明
- 遍历 `outputs/漫画名/章节目录` 的每个章节文件夹
- 解析章节文件夹名开头的数字前缀（如 `001-第01卷`, `012_特典`, `3 第3话`）
- 更新对应 `ComicInfo.xml` 的 `<Number>` 为该前缀（保持前导零）
- 兼容两种 XML 存放结构：
  - `outputs/漫画名/章节目录/ComicInfo.xml`
  - `outputs/漫画名/章节目录/xml/ComicInfo.xml`

#### 支持的文件夹名格式
- `001-第01卷`
- `012_特典`
- `3 第3话`
- `连载第093話`

### 3. update_archives_with_xml.py - 压缩包XML更新工具

读取指定漫画的 XML 目录，按 XML 中的 Title 对漫画目录下的 .cbz/.zip 进行模糊匹配，在压缩包根目录添加或覆盖 `ComicInfo.xml`。

#### 使用方法

**试运行查看匹配与计划更新**
```bash
uv run python update_archives_with_xml.py "/path/to/comic/dir" "/path/to/xml/root" --dry-run -v
```

**实际写入（若压缩包已存在 ComicInfo.xml 且需覆盖，加 --force）**
```bash
uv run python update_archives_with_xml.py "/path/to/comic/dir" "/path/to/xml/root" --force -v
```

**调整匹配阈值**
```bash
uv run python update_archives_with_xml.py "/path/to/comic/dir" "/path/to/xml/root" --threshold 0.5 --dry-run
```

#### 参数说明
- `--threshold 0.60`：模糊匹配阈值（默认 0.60，可调高低以适配"章节名有部分相同"的情况）
- `--dry-run`：试运行不写入
- `--force`：存在则覆盖
- `-v`：详细日志
- `--strategy`：匹配策略选择

#### 匹配策略
- `both`（默认）：同时以 XML 的 Title 与章节文件夹名比对，取更高分
- `title`：仅用 Title 匹配
- `folder`：仅用文件夹名匹配

#### 使用示例

**试运行（两种方式择优匹配，显示详细日志）**
```bash
uv run python update_archives_with_xml.py "/path/to/comic/dir" "/path/to/xml/root" --dry-run -v --strategy both
```

**仅使用文件夹名匹配并写入（若已存在则覆盖）**
```bash
uv run python update_archives_with_xml.py "/path/to/comic/dir" "/path/to/xml/root" --strategy folder --force -v
```

#### 特性
- 支持 .cbz 和 .zip 格式
- 智能章节索引匹配（精确优先）
- 规范化匹配：小写、去空白和常见符号
- 单位强约束：卷/回(話)必须一致
- 安全更新：通过临时文件避免损坏原文件
- 一一对应：同一压缩包只允许被一个 XML 使用

#### 兼容的XML路径
- `outputs/漫画名/章节目录/ComicInfo.xml`
- `outputs/漫画名/章节目录/xml/ComicInfo.xml`

### 4. 编辑压缩包内 XML（FastAPI + HTMX Web UI）

在浏览器中扫描目录下的 .cbz/.zip，读取或预填 ComicInfo.xml，以 CSV 形式编辑后写回压缩包。支持批量简繁转换、查找替换、前缀后缀、导出/导入 CSV。

#### 启动方式

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000`。

#### 路径白名单（安全）

服务端仅允许访问配置的根目录及其子路径，避免任意目录读写。通过环境变量配置：

- **ALLOWED_BASE_PATHS**：允许的根目录，多个用英文逗号分隔。未配置时默认仅允许当前工作目录。
- **SESSION_SECRET**（可选）：Session 签名密钥，生产环境请务必设置。

示例：

```bash
export ALLOWED_BASE_PATHS="/home/user/comics,/home/user/outputs"
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

#### 功能说明

- **基路径 + 刷新**：列出包含 .zip/.cbz 的子目录，选择后填充「章节压缩包目录」。
- **扫描**：读取该目录下所有压缩包内的 ComicInfo.xml（无则预填），生成 CSV；支持按数字/字母/Number 列排序。
- **CSV 编辑**：第一列为 FileName（固定），其余列为 ComicInfo 字段；支持批量编辑与导入/导出 CSV。
- **保存**：将 CSV 写回对应压缩包；可选「检测文档数量一致」校验。

## 工作流程

1. **抓取漫画信息**：使用 `manhuagui.py` 从网站抓取漫画信息并生成 XML 文件
2. **更新序号**：使用 `update_xml_numbers.py` 根据文件夹名更新 XML 中的序号
3. **更新压缩包**：使用 `update_archives_with_xml.py` 将 XML 文件写入对应的压缩包
4. **编辑压缩包内 XML**：使用 FastAPI Web UI（`app.py`）在浏览器中编辑已有压缩包的 ComicInfo.xml

## 注意事项

- 请遵守网站的访问频率限制，避免过于频繁的请求
- 建议先使用 `--dry-run` 参数测试，确认无误后再执行实际操作
- 使用 `--force` 参数会覆盖压缩包中已存在的 ComicInfo.xml 文件
- 确保有足够的磁盘空间用于临时文件操作

## 许可证

本项目采用 MIT 许可证。