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

        details = {}
        details['series'] = doc.select_one("div.book-title h1").text.strip()
        details['summary'] = doc.select_one("div#intro-all").text.strip()
        
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

        chapters = []
        # 查找章节区域
        chapter_div = doc.find('div', class_='chapter')
        if chapter_div:
            # 查找所有章节链接
            chapter_links = chapter_div.find_all('a')
            for chapter_link in chapter_links:
                href = chapter_link.get('href')
                if href and href.startswith('/comic/'):
                    title_attr = chapter_link.get('title')
                    if title_attr:
                        chapter_title = title_attr.strip()
                    else:
                        # 回退：优先 span 文本，否则 a 的可见文本
                        span = chapter_link.find('span')
                        chapter_title = (span.get_text().strip() if span and span.get_text() else chapter_link.get_text().strip())

                    chapter = {}
                    chapter['url'] = href
                    chapter['title'] = chapter_title
                    chapters.append(chapter)
        
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