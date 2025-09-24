import asyncio
import aiohttp
import re
import json
import os
import sys
import argparse
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from aiolimiter import AsyncLimiter
from lxml import etree


class BaoziScraper:
    def __init__(self, base_url: str = "https://www.baozimh.com"):
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Referer": f"{self.base_url}/",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "no-cache",
        }
        # 每 10 秒 2 个请求
        self.limiter = AsyncLimiter(2, 10)
        # 可用镜像（按优先级）
        self.mirrors = [
            "cn.baozimh.com",
            "tw.baozimh.com",
            # "www.baozimh.com",
            # "cn.webmota.com",
            # "tw.webmota.com",
            # "www.webmota.com",
            # "cn.kukuc.co",
            # "tw.kukuc.co",
            # "www.kukuc.co",
            # "cn.twmanga.com",
            # "tw.twmanga.com",
            # "www.twmanga.com",
            # "cn.dinnerku.com",
            # "tw.dinnerku.com",
            # "www.dinnerku.com",
        ]

        # 尝试加载 cookies.json
        self._load_cookies_into_headers()

    def _load_cookies_into_headers(self):
        try:
            config_dir = os.path.join(os.path.dirname(__file__), "config")
            cookies_path = os.path.join(config_dir, "cookies.json")
            if not os.path.exists(cookies_path):
                return
            with open(cookies_path, "r", encoding="utf-8") as f:
                cookie_map = json.load(f)
            if not isinstance(cookie_map, dict) or not cookie_map:
                return
            base_host = urlparse(self.base_url).netloc or ""
            matched_key = None
            for key in sorted(cookie_map.keys(), key=lambda k: len(k), reverse=True):
                if base_host == key or base_host.endswith(key):
                    matched_key = key
                    break
            if matched_key is None:
                matched_key = next(iter(cookie_map.keys()))
            cookie_str = cookie_map.get(matched_key, "").strip()
            if cookie_str:
                self.headers["Cookie"] = cookie_str
        except Exception as e:
            print(f"加载 Cookie 失败: {e}")

    async def _get_document(self, url: str, session: aiohttp.ClientSession) -> BeautifulSoup | None:
        async with self.limiter:
            try:
                async with session.get(url, headers=self.headers, timeout=20) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
                    return BeautifulSoup(html, 'html.parser')
            except aiohttp.ClientError as e:
                print(f"请求失败 {url}: {e}")
                return None

    async def get_manga_details(self, manga_url: str, session: aiohttp.ClientSession):
        # 先尝试当前 base_url，不行则轮询镜像
        doc = await self._get_document(urljoin(self.base_url, manga_url), session)
        if not doc:
            # 轮询镜像域名，直至成功
            for domain in self.mirrors:
                trial_base = f"https://{domain}"
                doc = await self._get_document(urljoin(trial_base, manga_url), session)
                if doc:
                    self.base_url = trial_base
                    # 更新 Referer 头
                    self.headers["Referer"] = f"{self.base_url}/"
                    break
            if not doc:
                return None

        details: dict[str, str] = {}
        # 标题
        title_el = doc.select_one("h1.comics-detail__title")
        details['series'] = title_el.text.strip() if title_el else ""

        # 简介
        desc_el = doc.select_one("p.comics-detail__desc")
        details['summary'] = desc_el.text.strip() if desc_el else ""

        # 作者
        author_el = doc.select_one("h2.comics-detail__author")
        details['writer'] = author_el.text.strip() if author_el else ""

        # 封面（amp-img）
        cover_el = doc.select_one("div.pure-g div > amp-img")
        cover_src = cover_el.get('src').strip() if cover_el and cover_el.get('src') else ""
        details['cover_url'] = urljoin(self.base_url, cover_src) if cover_src else ""

        # 状态
        status_el = doc.select_one("div.tag-list > span.tag")
        status_text = status_el.text.strip() if status_el else ""
        if status_text in ("连载中", "連載中"):
            details['status'] = "Ongoing"
        elif status_text in ("已完结", "已完結"):
            details['status'] = "Completed"
        else:
            details['status'] = "Unknown"

        return details

    async def get_chapter_list(self, manga_url: str, session: aiohttp.ClientSession):
        doc = await self._get_document(urljoin(self.base_url, manga_url), session)
        if not doc:
            # 再尝试镜像
            for domain in self.mirrors:
                trial_base = f"https://{domain}"
                doc = await self._get_document(urljoin(trial_base, manga_url), session)
                if doc:
                    self.base_url = trial_base
                    self.headers["Referer"] = f"{self.base_url}/"
                    break
            if not doc:
                return []

        # 判断是否存在完整章节目录标题
        full_list_title = doc.find(class_='section-title', string=lambda s: bool(s) and ("章节目录" in s or "章節目錄" in s))
        if full_list_title is None:
            chapter_nodes = doc.select(".comics-chapters")
        else:
            container = full_list_title.parent if full_list_title else None
            chapter_nodes = container.select(".comics-chapters") if container else []
            chapter_nodes = list(reversed(chapter_nodes))

        chapters = []
        for node in chapter_nodes:
            a = node.select_one('a')
            if not a:
                continue
            href = a.get('href')
            if not href:
                continue
            title = node.get_text(strip=True)
            chapters.append({
                'url': href,
                'title': title,
            })
        return chapters

    async def get_page_list(self, chapter_url: str, session: aiohttp.ClientSession):
        # 迭代“下一页/下一頁”直至结束，收集 amp-img 的 src
        pages: list[str] = []
        next_url = urljoin(self.base_url, chapter_url)
        visited = set()
        while next_url and next_url not in visited:
            visited.add(next_url)
            doc = await self._get_document(next_url, session)
            if not doc:
                break
            imgs = doc.select('.comic-contain amp-img')
            for img in imgs:
                src = img.get('src') or ""
                if src:
                    pages.append(urljoin(self.base_url, src.strip()))

            next_link = doc.select_one('#next-chapter')
            if next_link:
                text = next_link.get_text(strip=True)
                if text in ("下一页", "下一頁"):
                    href = next_link.get('href') or ""
                    next_url = urljoin(self.base_url, href)
                    continue
            break
        return pages

    async def download_cover(self, cover_url: str, dest_path: str, session: aiohttp.ClientSession) -> bool:
        if not cover_url:
            return False
        try:
            async with self.limiter:
                async with session.get(cover_url, headers=self.headers, timeout=20) as resp:
                    resp.raise_for_status()
                    content = await resp.read()
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, 'wb') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"下载封面失败: {e}")
            return False

    def create_xml_file(self, manga_details: dict, chapter_title: str, chapter_number: str, chapter_url: str) -> str:
        root = etree.Element("ComicInfo")

        etree.SubElement(root, "Title").text = chapter_title
        etree.SubElement(root, "Series").text = manga_details.get('series', '')
        etree.SubElement(root, "Number").text = str(chapter_number)
        etree.SubElement(root, "Summary").text = manga_details.get('summary', '')
        etree.SubElement(root, "Writer").text = manga_details.get('writer', '')
        etree.SubElement(root, "Genre").text = manga_details.get('genre', '')
        etree.SubElement(root, "Web").text = chapter_url
        etree.SubElement(root, "PublishingStatusTachiyomi").text = manga_details.get('status', 'Unknown')
        etree.SubElement(root, "SourceMihon").text = "包子漫画"

        etree_doc = etree.ElementTree(root)
        return etree.tostring(etree_doc, pretty_print=True, xml_declaration=True, encoding='UTF-8').decode('utf-8')


def parse_manga_url(url_input: str) -> str:
    # 纯数字视为 ID
    if url_input.isdigit():
        return f"/comic/{url_input}"

    # 完整 URL
    if url_input.startswith(('http://', 'https://')):
        parsed = urlparse(url_input)
        if any(host in parsed.netloc for host in (
            'baozimh.com', 'webmota.com', 'kukuc.co', 'twmanga.com', 'dinnerku.com'
        )):
            return parsed.path or '/'
        raise ValueError("URL必须是包子漫画站点链接")

    # 相对路径
    if url_input.startswith('/comic/'):
        return url_input

    # 兼容传入 'comic/<slug>'（无前导斜杠）
    if url_input.startswith('comic/'):
        return '/' + url_input

    # slug 形式（字母数字下划线中划线），例如 hanghaiwang-weitianrongyilang_l20yux
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", url_input):
        return f"/comic/{url_input}"

    # 兜底尝试数字
    try:
        cid = int(url_input)
        return f"/comic/{cid}"
    except ValueError:
        raise ValueError("请输入有效的漫画URL或漫画编号")


def parse_arguments():
    parser = argparse.ArgumentParser(description='包子漫画 信息提取工具')
    parser.add_argument('manga_url', help='漫画URL/编号/slug，例如 https://tw.baozimh.com/comic/hanghaiwang-xxx 或 hanghaiwang-xxx 或 12345')
    parser.add_argument('--limit', '-l', type=int, default=None, help='限制处理的章节数量（用于测试）')
    return parser.parse_args()


async def main():
    args = parse_arguments()
    try:
        manga_relative_url = parse_manga_url(args.manga_url)
        print(f"解析的漫画URL: {manga_relative_url}")
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)

    scraper = BaoziScraper()
    async with aiohttp.ClientSession() as session:
        print("开始提取漫画详细信息...")
        manga_info = await scraper.get_manga_details(manga_relative_url, session)
        if not manga_info:
            print("漫画信息提取失败，请检查URL或网络连接。")
            return
        print("漫画信息提取成功。")
        print(f"系列：{manga_info.get('series','')}")
        print(f"作者：{manga_info.get('writer','')}")
        print("-" * 20)

        # 下载封面
        cover_url = manga_info.get('cover_url', '')
        if cover_url:
            safe_name = manga_info.get('series', 'Unknown')
            for ch in '/\\:*?"<>|':
                safe_name = safe_name.replace(ch, '_')
            outputs_root = os.path.join("outputs", safe_name)
            os.makedirs(outputs_root, exist_ok=True)
            ext = os.path.splitext(urlparse(cover_url).path)[1] or ".jpg"
            cover_path = os.path.join(outputs_root, f"cover{ext}")
            ok = await scraper.download_cover(cover_url, cover_path, session)
            print(f"封面已保存到 {cover_path}" if ok else "封面下载失败，继续处理章节...")

        print("开始提取章节列表...")
        chapters = await scraper.get_chapter_list(manga_relative_url, session)
        if not chapters:
            print("章节列表为空或提取失败。")
            return
        print(f"找到 {len(chapters)} 个章节。")

        if args.limit:
            chapters = chapters[:args.limit]
            print(f"限制处理前 {len(chapters)} 个章节。")

        for i, chapter in enumerate(reversed(chapters)):
            full_chapter_url = urljoin(scraper.base_url, chapter['url'])
            print(f"正在处理章节：{chapter['title']} (URL: {full_chapter_url})")

            chapter_number = str(i + 1).zfill(3)
            xml_content = scraper.create_xml_file(manga_info, chapter['title'], chapter_number, full_chapter_url)

            safe_series = manga_info.get('series', 'Unknown')
            for ch in '/\\:*?"<>|':
                safe_series = safe_series.replace(ch, '_')
            chapter_name = f"{chapter_number}-{''.join(c if c not in '/\\:*?"<>|' else '_' for c in chapter['title'])}"

            output_dir = os.path.join("outputs", safe_series, chapter_name)
            os.makedirs(output_dir, exist_ok=True)

            xml_file_path = os.path.join(output_dir, "ComicInfo.xml")
            with open(xml_file_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            print(f"XML 文件已保存到 {xml_file_path}")

            # 如需图片列表：
            # pages = await scraper.get_page_list(chapter['url'], session)
            # print(f"章节图片数量：{len(pages)}")

            print("-" * 20)

        print("所有章节的XML文件已生成。")


if __name__ == '__main__':
    asyncio.run(main())


