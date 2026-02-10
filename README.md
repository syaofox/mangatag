# MangaTag - 漫画压缩包元数据编辑工具

基于 FastAPI + HTMX 的 Web 应用，用于扫描、编辑并写回 .cbz/.zip 压缩包内的 ComicInfo.xml 元数据。

## 功能特性

- 📂 **目录扫描**：递归列出包含 .zip/.cbz 的子目录，支持多种排序方式
- 📝 **CSV 编辑**：以 CSV 形式批量编辑 Title、Series、Number、Summary 等字段
- 🔄 **批量操作**：批量置为、查找替换、添加前缀/后缀、简繁转换
- 📤 **导入导出**：支持 CSV 文件导入与导出
- 🛡️ **路径白名单**：通过环境变量限制可访问的根目录，保障安全

## 安装

### 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）

### 安装依赖

```bash
uv sync
```

## 快速开始

### 启动 Web 服务

```bash
./run.sh
```

或直接使用 uvicorn：

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000`。

### 可选环境变量

- **ALLOWED_BASE_PATHS**：允许访问的根目录，多个用英文逗号分隔。未配置时不做限制（适合本地使用）。
- **SESSION_SECRET**：Session 签名密钥，生产环境建议设置。

示例：

```bash
export ALLOWED_BASE_PATHS="/home/user/comics,/home/user/outputs"
./run.sh
```

## 使用说明

1. **基路径**：在基路径输入框中填入漫画根目录（如 `/path/to/books`），点击「刷新」获取子目录列表
2. **选择目录**：从下拉列表选择要编辑的章节目录
3. **扫描**：点击「扫描目录并读取 ComicInfo.xml」，生成 CSV
4. **编辑**：在 CSV 编辑区修改字段，可使用批量编辑功能
5. **保存**：点击「保存修改到压缩包」将更改写回压缩包

### CSV 字段说明

| 列名 | 说明 |
|------|------|
| FileName | 压缩包文件名（固定，不可修改） |
| Title | 标题 |
| Series | 系列名 |
| Number | 卷/话编号 |
| Summary | 简介 |
| Writer | 作者 |
| Genre | 类型 |
| Web | 网页链接 |
| PublishingStatusTachiyomi | 连载状态 |
| SourceMihon | 来源 |
| PublicationYear | 出版年份 |
| PublicationMonth | 出版月份 |

## 项目结构

```
mangatag/
├── app.py                 # FastAPI 应用入口
├── edit_archive_xml.py    # 核心业务逻辑
├── update_archives_with_xml.py  # 压缩包与 XML 匹配（内部依赖）
├── run.sh                 # 启动脚本
├── templates/             # HTMX 模板
└── pyproject.toml
```

## 许可证

MIT
