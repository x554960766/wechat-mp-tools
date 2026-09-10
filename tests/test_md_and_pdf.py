import os
import sys
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.downloader import (
    html_to_markdown,
    find_chromium_executable,
    export_html_to_pdf,
    download_single_article,
    try_extract_gallery_article,
)
from backend.config import get_settings


class TestMarkdownAndPDF(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="wechat_test_"))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_html_to_markdown_elements(self):
        html_input = """
        <div id="js_content">
            <p>欢迎阅读<strong>测试文章</strong>，包含 <em>斜体文本</em> 以及 <del>删除线</del>。</p>
            <h2>一级子标题</h2>
            <blockquote>
                这是引用区块的文字。<br>
                第二行引用内容。
            </blockquote>
            <p>技术代码示例：</p>
            <pre data-lang="python"><code>def greet(name):
    return f"Hello, {name}!"
</code></pre>
            <p>相关列表：</p>
            <ul>
                <li>列表项目 A</li>
                <li>列表项目 B</li>
            </ul>
            <ol>
                <li>步骤一</li>
                <li>步骤二</li>
            </ol>
            <p>数据表格：</p>
            <table>
                <tr><th>格式</th><th>状态</th></tr>
                <tr><td>HTML</td><td>已支持</td></tr>
                <tr><td>MD</td><td>已支持</td></tr>
                <tr><td>PDF</td><td>已支持</td></tr>
            </table>
            <p>插图说明：</p>
            <img src="https://mmbiz.qpic.cn/image_01.jpg" alt="架构图" />
        </div>
        """

        meta = {
            "title": "微信下载增强功能测试",
            "author": "测试作者",
            "source": "技术研习社",
            "cover_url": "https://mmbiz.qpic.cn/cover.jpg",
            "publish_time": 1740464400,
        }
        media_map = {
            "https://mmbiz.qpic.cn/cover.jpg": "media/cover.jpg",
            "https://mmbiz.qpic.cn/image_01.jpg": "media/img_001.jpg",
        }

        md_output = html_to_markdown(html_input, meta=meta, media_map=media_map)

        # 验证元信息头部
        self.assertIn("![cover_image](media/cover.jpg)", md_output)
        self.assertIn("# 微信下载增强功能测试", md_output)
        self.assertIn("作者：测试作者", md_output)
        self.assertIn("公众号：技术研习社", md_output)
        self.assertIn("---", md_output)

        # 验证正文元素
        self.assertIn("**测试文章**", md_output)
        self.assertIn("*斜体文本*", md_output)
        self.assertIn("~~删除线~~", md_output)
        self.assertIn("## 一级子标题", md_output)
        self.assertIn("> 这是引用区块的文字。", md_output)
        self.assertIn("```python", md_output)
        self.assertIn('return f"Hello, {name}!"', md_output)
        self.assertIn("- 列表项目 A", md_output)
        self.assertIn("- 列表项目 B", md_output)
        self.assertIn("1. 步骤一", md_output)
        self.assertIn("2. 步骤二", md_output)
        self.assertIn("| 格式 | 状态 |", md_output)
        self.assertIn("| HTML | 已支持 |", md_output)
        self.assertIn("![架构图](media/img_001.jpg)", md_output)

    def test_find_chromium_and_pdf_export(self):
        chrome_path = find_chromium_executable()
        self.assertIsNotNone(chrome_path, "应当在当前 macOS 系统上检测到 Chrome 或 Edge 浏览器")

        # 创建一个测试 HTML 文件
        test_html_file = self.test_dir / "sample.html"
        test_html_file.write_text(
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>PDF Test</title></head>"
            "<body><h1>PDF 测试标题</h1><p>这是一份高质量的测试 PDF 文档。</p></body></html>",
            encoding="utf-8"
        )
        test_pdf_file = self.test_dir / "sample.pdf"

        success = export_html_to_pdf(test_html_file, test_pdf_file, title="PDF Test")
        self.assertTrue(success)
        self.assertTrue(test_pdf_file.exists())
        self.assertGreater(test_pdf_file.stat().st_size, 1000)

    def test_gallery_article_markdown(self):
        gallery_raw_html = """
        <html>
        <head><title>图集精选</title></head>
        <body>
        <script>
        var picture_page_info_list = [
            { cdn_url: "https://mmbiz.qpic.cn/gallery_01.jpg" },
            { cdn_url: "https://mmbiz.qpic.cn/gallery_02.jpg" }
        ];
        window.desc = "这是一组珍贵的历史摄影作品。\\n记录了岁月变迁。";
        </script>
        </body>
        </html>
        """
        rebuilt_html = try_extract_gallery_article(gallery_raw_html)
        self.assertIsNotNone(rebuilt_html)

        md = html_to_markdown(rebuilt_html, meta={"title": "图集精选", "source": "摄影志"})
        self.assertIn("# 图集精选", md)
        self.assertIn("![](https://mmbiz.qpic.cn/gallery_01.jpg)", md)
        self.assertIn("这是一组珍贵的历史摄影作品。", md)

    @patch("requests.get")
    def test_download_single_article_generates_md_and_pdf(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = (
            '<!DOCTYPE html><html><head>'
            '<meta property="og:title" content="端到端下载导出测试">'
            '<meta property="og:description" content="测试文章概要">'
            '<meta property="og:image" content="https://mmbiz.qpic.cn/cover123.jpg">'
            '</head><body>'
            '<div id="js_content"><p>这是微信公众号正文内容。</p></div>'
            '</body></html>'
        ).encode("utf-8")
        mock_response.iter_content.return_value = [b"mock image data"]
        mock_get.return_value = mock_response

        out_dir = self.test_dir / "download_out"
        result = download_single_article("https://mp.weixin.qq.com/s/mock_article", out_dir, "端到端下载导出测试")

        self.assertTrue(result["success"])
        art_path = Path(result["path"])
        self.assertTrue(art_path.exists())

        # 检查是否生成了 .html, .md, .pdf
        html_files = list(art_path.glob("*.html"))
        md_files = list(art_path.glob("*.md"))
        pdf_files = list(art_path.glob("*.pdf"))

        self.assertGreater(len(html_files), 0, "应生成 .html 文件")
        self.assertGreater(len(md_files), 0, "应生成 .md 文件")
        self.assertGreater(len(pdf_files), 0, "应生成 .pdf 文件")

        # 检查 metadata.json
        meta_file = art_path / "metadata.json"
        self.assertTrue(meta_file.exists())
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        self.assertTrue(meta.get("has_markdown"))
        self.assertTrue(meta.get("has_pdf"))
        self.assertTrue(meta.get("markdown_file").endswith(".md"))
        self.assertTrue(meta.get("pdf_file").endswith(".pdf"))


if __name__ == "__main__":
    unittest.main()
