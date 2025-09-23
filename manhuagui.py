import asyncio
import aiohttp
import re
import json
import os
import sys
import argparse
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from lxml import etree
from urllib.parse import urljoin, urlparse
from aiolimiter import AsyncLimiter
from lzstring import LZString

class ManhuaguiScraper:
    def __init__(self, base_url="https://tw.manhuagui.com"):
        self.base_url = base_url
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Referer": self.base_url,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        # 设置限速器：每 10 秒 2 个请求
        self.limiter = AsyncLimiter(2, 10)
        self.image_server = ["https://i.hamreus.com", "https://cf.hamreus.com"]

        # 尝试加载本地 Cookie 文件以模拟登录
        self._load_cookies_into_headers()
        # 确保注入 R18 验证 Cookie
        self._ensure_r18_cookie()

    def _load_cookies_into_headers(self):
        """从 config/cookies.json 读取 Cookie 并注入到默认请求头。"""
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
            # 选择与 base_url 最匹配的域（最长匹配优先）
            matched_key = None
            for key in sorted(cookie_map.keys(), key=lambda k: len(k), reverse=True):
                if base_host == key or base_host.endswith(key):
                    matched_key = key
                    break

            # 若未匹配到，退而求其次使用第一个键值
            if matched_key is None:
                matched_key = next(iter(cookie_map.keys()))

            cookie_str = cookie_map.get(matched_key, "").strip()
            if cookie_str:
                # 直接通过 Cookie 头注入，适用于 aiohttp 每次请求
                self.headers["Cookie"] = cookie_str
        except Exception as e:
            # 读取失败时忽略，不中断主流程
            print(f"加载 Cookie 失败: {e}")

    def _ensure_r18_cookie(self):
        """确保在请求头 Cookie 中包含 isAdult=1 以通过 R18 验证。"""
        try:
            cookie_header = self.headers.get("Cookie", "").strip()
            # 若已存在 isAdult=1 则不重复添加
            if "isAdult=1" not in cookie_header:
                if cookie_header:
                    cookie_header = f"{cookie_header}; isAdult=1"
                else:
                    cookie_header = "isAdult=1"
                self.headers["Cookie"] = cookie_header
        except Exception:
            # 任何异常都不阻塞主流程
            pass

    async def _get_document(self, url, session):
        """异步获取并解析网页文档，并应用速率限制"""
        async with self.limiter:
            try:
                async with session.get(url, headers=self.headers, timeout=15) as response:
                    response.raise_for_status()
                    html_content = await response.text()
                    return BeautifulSoup(html_content, 'html.parser')
            except aiohttp.ClientError as e:
                print(f"请求 {url} 失败: {e}")
                return None

    async def get_manga_details(self, manga_url, session):
        """
        异步提取漫画的详细信息（标题、作者、类型等）。
        :param manga_url: 漫画详情页的URL。
        :return: 包含漫画信息的字典。
        """
        doc = await self._get_document(urljoin(self.base_url, manga_url), session)
        if not doc:
            return None

        print(doc)
        details = {}
        details['series'] = doc.select_one("div.book-title h1").text.strip()
        details['summary'] = doc.select_one("div#intro-all").text.strip()
        # 封面图片 URL（优先 src，其次 data-src），保留为绝对或相对，之后统一 urljoin
        cover_img = doc.select_one("p.hcover > img")
        details['cover_url'] = ""
        if cover_img:
            cover_src = cover_img.get('src') or cover_img.get('data-src') or ""
            if cover_src:
                details['cover_url'] = urljoin(self.base_url, cover_src)
        
        # 作者：限制在同一 li 节点内，并仅选择指向 /author/ 的链接
        author_element = doc.select_one("span:contains('漫画作者'), span:contains('漫畫作者')")
        details['writer'] = ""
        if author_element:
            author_li = author_element.find_parent("li")
            if author_li:
                author_links = [a for a in author_li.select("a") if (a.get('href') or '').startswith('/author/')]
                if author_links:
                    details['writer'] = ", ".join(a.text.strip() for a in author_links)

        # 类型/剧情：限制在同一 li 节点内，并仅选择指向 /list/ 的链接
        genre_element = doc.select_one("span:contains('漫画剧情'), span:contains('漫畫劇情'), span:contains('漫画類型'), span:contains('漫畫類型')")
        details['genre'] = ""
        if genre_element:
            genre_li = genre_element.find_parent("li")
            if genre_li:
                genre_links = [a for a in genre_li.select("a") if (a.get('href') or '').startswith('/list/')]
                if genre_links:
                    details['genre'] = ", ".join(a.text.strip() for a in genre_links)
        
        status_text = doc.select_one("div.book-detail > ul.detail-list > li.status > span > span").text
        details['status'] = "Ongoing" if "连载中" in status_text or "連載中" in status_text else "Completed"
        
        return details

    async def get_chapter_list(self, manga_url, session):
        """
        异步提取漫画的章节列表。
        :param manga_url: 漫画详情页的URL。
        :return: 章节列表，每个章节是一个包含URL和名称的字典。
        """
        doc = await self._get_document(urljoin(self.base_url, manga_url), session)
        if not doc:
            return []

        # 优先处理隐藏的加密章节列表：input#__VIEWSTATE（LZString Base64）
        try:
            viewstate_input = doc.select_one("input#__VIEWSTATE")
            if viewstate_input and viewstate_input.get('value'):
                encoded = viewstate_input.get('value') or ""
                decoded_html = LZString().decompressFromBase64(encoded) or ""
                if decoded_html:
                    hidden_doc = BeautifulSoup(decoded_html, 'html.parser')
                    extracted = self._extract_chapters_from_document(hidden_doc)
                    if extracted:
                        return extracted
        except Exception as e:
            # 解码失败则回退到普通解析
            pass

        # 常规页面直接解析
        extracted = self._extract_chapters_from_document(doc)
        if extracted:
            return extracted

        # 最后回退到旧的 div.chapter 结构（兼容历史页面）
        chapters = []
        chapter_div = doc.find('div', class_='chapter')
        if chapter_div:
            for chapter_link in chapter_div.find_all('a'):
                href = chapter_link.get('href')
                if not href:
                    continue
                title_attr = chapter_link.get('title')
                if title_attr and title_attr.strip():
                    chapter_title = title_attr.strip()
                else:
                    span = chapter_link.find('span')
                    chapter_title = (span.get_text().strip() if span and span.get_text() else chapter_link.get_text().strip())
                chapters.append({'url': href, 'title': chapter_title})
        return chapters

    def _extract_chapters_from_document(self, document: BeautifulSoup):
        """从标准章节列表结构提取章节：#chapter-list-* -> ul -> li > a.status0"""
        chapters = []
        section_list = document.select("[id^=chapter-list-]")
        if section_list:
            for section in section_list:
                page_lists = section.select("ul")
                page_lists.reverse()
                for page in page_lists:
                    for a in page.select("li > a.status0"):
                        href = a.get('href')
                        if not href:
                            continue
                        title_attr = a.get('title')
                        if title_attr and title_attr.strip():
                            name = title_attr.strip()
                        else:
                            span = a.find('span')
                            name = (span.get_text().strip() if span and span.get_text() else a.get_text().strip())
                        chapters.append({'url': href, 'title': name})
        return chapters

    async def get_page_list(self, chapter_url, session):
        """
        异步解析章节页面，解密并提取图片链接。
        :param chapter_url: 章节页面的URL。
        :return: 包含所有图片URL的列表。
        """
        doc = await self._get_document(urljoin(self.base_url, chapter_url), session)
        if not doc:
            return []

        # Find the packed JavaScript code
        html_content = str(doc)
        
        # Use a more robust regex to find the JSON data directly
        img_json_match = re.search(r"JSON\.parse\(\s*Unpacker\.unpack\(.*?'(.*?)'.*\)\s*\)", html_content)
        if not img_json_match:
            img_json_match = re.search(r"var\s*p\s*=\s*'(.+?)'", html_content)
            
        if img_json_match:
            packed_data = img_json_match.group(1)
            try:
                unpacked_json_str = packed_data.replace('\\"', '"').replace('\\\\', '\\')
                json_content = re.search(r"\{.*?\}", unpacked_json_str)
                if json_content:
                    image_json = json.loads(json_content.group(0))
                    if 'files' in image_json and 'path' in image_json:
                        files = image_json['files']
                        path = image_json['path']
                        pages = []
                        sl = image_json.get('sl', {})
                        for img_file in files:
                            img_url = f"{self.image_server[0]}{path}{img_file}?e={sl.get('e')}&m={sl.get('m')}"
                            pages.append(img_url)
                        return pages
            except json.JSONDecodeError as e:
                print(f"JSON 解码失败: {e}")
                
        return []

    async def download_cover(self, cover_url: str, dest_path: str, session) -> bool:
        """下载封面图片到指定路径。返回是否成功。"""
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
    
    def create_xml_file(self, manga_details, chapter_title, chapter_number, chapter_url):
        """
        根据提取的信息创建XML文件。
        :return: XML文件的字符串表示。
        """
        # 创建根元素，不使用命名空间属性
        root = etree.Element("ComicInfo")
        
        etree.SubElement(root, "Title").text = chapter_title
        etree.SubElement(root, "Series").text = manga_details.get('series', '')
        etree.SubElement(root, "Number").text = str(chapter_number)
        etree.SubElement(root, "Summary").text = manga_details.get('summary', '')
        etree.SubElement(root, "Writer").text = manga_details.get('writer', '')
        etree.SubElement(root, "Genre").text = manga_details.get('genre', '')
        etree.SubElement(root, "Web").text = chapter_url
        etree.SubElement(root, "PublishingStatusTachiyomi").text = manga_details.get('status', 'Unknown')
        etree.SubElement(root, "SourceMihon").text = "漫画柜"

        etree_doc = etree.ElementTree(root)
        return etree.tostring(etree_doc, pretty_print=True, xml_declaration=True, encoding='UTF-8').decode('utf-8')

def parse_manga_url(url_input):
    """
    解析漫画URL输入，支持完整URL和漫画编号两种方式
    :param url_input: 用户输入的URL或漫画编号
    :return: 漫画的相对URL路径
    """
    # 如果是纯数字，认为是漫画编号，默认使用台湾站
    if url_input.isdigit():
        return f"/comic/{url_input}/"
    
    # 如果是完整URL，提取相对路径
    if url_input.startswith(('http://', 'https://')):
        parsed_url = urlparse(url_input)
        if 'manhuagui.com' in parsed_url.netloc:
            return parsed_url.path
        else:
            raise ValueError("URL必须是漫画柜网站的链接")
    
    # 如果已经是相对路径，直接返回
    if url_input.startswith('/comic/'):
        return url_input
    
    # 其他情况，尝试作为漫画编号处理
    try:
        comic_id = int(url_input)
        return f"/comic/{comic_id}/"
    except ValueError:
        raise ValueError("请输入有效的漫画URL或漫画编号")

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='漫画柜漫画信息提取工具')
    parser.add_argument('manga_url', 
                       help='漫画URL或漫画编号。支持格式：\n'
                            '1. 完整URL: https://tw.manhuagui.com/comic/36217/\n'
                            '2. 相对URL: /comic/36217/\n'
                            '3. 漫画编号: 36217 (默认使用台湾站)')
    parser.add_argument('--limit', '-l', type=int, default=None,
                       help='限制处理的章节数量（用于测试）')
    return parser.parse_args()

async def main():
    # 解析命令行参数
    args = parse_arguments()
    
    try:
        manga_relative_url = parse_manga_url(args.manga_url)
        print(f"解析的漫画URL: {manga_relative_url}")
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
    
    scraper = ManhuaguiScraper()
    
    async with aiohttp.ClientSession() as session:
        print(f"开始提取漫画详细信息...")
        manga_info = await scraper.get_manga_details(manga_relative_url, session)
        
        if not manga_info:
            print("漫画信息提取失败，请检查URL或网络连接。")
            return

        print("漫画信息提取成功。")
        print(f"系列：{manga_info['series']}")
        print(f"作者：{manga_info['writer']}")
        print("-" * 20)

        # 下载封面到 outputs/漫画名/cover.<扩展名>
        cover_url = manga_info.get('cover_url', '')
        if cover_url:
            manga_name = manga_info.get('series', 'Unknown').replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            outputs_root = os.path.join("outputs", manga_name)
            os.makedirs(outputs_root, exist_ok=True)
            # 从 URL 推断扩展名
            parsed = urlparse(cover_url)
            basename = os.path.basename(parsed.path)
            ext = os.path.splitext(basename)[1] or ".jpg"
            cover_path = os.path.join(outputs_root, f"cover{ext}")
            ok = await scraper.download_cover(cover_url, cover_path, session)
            if ok:
                print(f"封面已保存到 {cover_path}")
            else:
                print("封面下载失败，继续处理章节...")
        
        print(f"开始提取章节列表...")
        chapters = await scraper.get_chapter_list(manga_relative_url, session)
        if not chapters:
            print("章节列表为空或提取失败。")
            return

        print(f"找到 {len(chapters)} 个章节。")
        
        # 如果指定了限制，只处理指定数量的章节
        if args.limit:
            chapters = chapters[:args.limit]
            print(f"限制处理前 {len(chapters)} 个章节。")
            
        
        for i, chapter in enumerate(reversed(chapters)):
            full_chapter_url = urljoin(scraper.base_url, chapter['url'])
            print(f"正在处理章节：{chapter['title']} (URL: {full_chapter_url})")

            chapter_number = str(i + 1).zfill(3)

            xml_content = scraper.create_xml_file(manga_info, chapter['title'], chapter_number, full_chapter_url)
            
            # 创建输出目录结构: outputs/漫画名/章节目录/xml
            manga_name = manga_info.get('series', 'Unknown').replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            chapter_name = chapter_number + '-' + chapter['title'].replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            
            # 创建完整的目录结构
            output_dir = os.path.join("outputs", manga_name, chapter_name)
            os.makedirs(output_dir, exist_ok=True)
            
            xml_file_path = os.path.join(output_dir, "ComicInfo.xml")
            with open(xml_file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            print(f"XML 文件已保存到 {xml_file_path}")

            # 如果需要下载图片链接，可以取消下面这行的注释
            # pages = await scraper.get_page_list(chapter['url'], session)
            # print(f"章节图片数量：{len(pages)}")

            print("-" * 20)
            
        print("所有章节的XML文件已生成。")

if __name__ == '__main__':
    # 运行异步主函数
    asyncio.run(main())