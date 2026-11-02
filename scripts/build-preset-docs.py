"""Build the offline help editions and their checksummed, manual-only manifest."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'resources/presets'
SCREENSHOTS = {
    'HOME': ('home-civic.png', '图 1 · 首页与导航：先选阅读或写作，再从左下查看进度与设置。'),
    'LIBRARY': ('library-civic.png', '图 3 · 知源文库：搜索和目录用于筛选，卡片圆形勾选用于选材。'),
    'READER': ('reader-civic.png', '图 4 · 资料阅读：正文、阅读方式、原始快照下载与阅读侧栏。'),
    'WRITING': ('writing-civic.png', '图 5 · 新建文稿：填写主题和要求，选择结构、文风与写作方式。'),
    'SETTINGS': ('settings-civic.png', '图 2 · 系统设置：管理默认模型，独立控制自动处理、翻译和闲时时段。'),
    'OUTLINE': ('outline-civic.png', '图 6 · 纲要：通读章节说明，调整结构后再确认。'),
    'BODY': ('body-civic.png', '图 7 · 全文预览：通读正文与实际引用，左上／右上图标分别收放侧栏。'),
    'REVIEW': ('review-civic.png', '图 8 · 检查校验：在独立页面查看当前稿状态、开始校验或导出。'),
    'WIKI': ('wiki-civic.png', '图 9 · 构建 Wiki：先检查固定范围、待构建总数与首批预览，再明确提交。'),
    'INSTRUCTIONS': ('instructions-civic.png', '图 10 · 智能指令：选择实际任务分类，阅读固定规则后再决定编辑与保存。'),
}

EDITIONS = {
    'server': {
        'EDITION_LABEL': '服务端部署版', 'START_TITLE': '在浏览器中打开文枢',
        'START_CONTENT': '<ol class="steps"><li>在浏览器中打开管理员提供的文枢地址，进入首页；使用电脑浏览器即可，无需在访问端安装文枢或 WebView2。</li><li>资料、文稿、模型配置和任务保存在服务器，上传文件会传到服务器处理；导出文件则由浏览器保存到当前电脑。</li><li>关闭标签页或浏览器不会停止后台任务。要停止生成，请先在页面取消或中断；再次访问同一服务可继续查看已保存工作。</li></ol><div class="note"><p><strong>访问的是部署地址对应的资料库。</strong>不同部署地址可能连接不同资料库；未保存的编辑草稿不应依靠关闭网页保留。服务无法访问时先确认地址和网络，再联系管理员检查服务。</p></div>',
        'MODEL_LOCATION': '<div class="note"><p><strong>模型地址须从服务器可达。</strong>这里的 <code>127.0.0.1</code> 指服务器自身，不是你打开浏览器的电脑；填写管理员提供的可达接口地址。网页抓取及联网检索同样使用服务器网络。</p></div>',
        'CARE_CONTENT': '<p><strong>退出网页前先保存。</strong>浏览器下载目录只保存导出文件，不是完整资料库。关闭网页后任务继续运行，重新打开可查看进度；不要通过关闭浏览器代替取消任务。</p><p><strong>资料库由管理员备份。</strong>安排后台任务安全结束并暂停写入后，整体备份实际数据目录，包括原文、文稿、配置及私有密钥目录；Linux 部署需保留原 <code>.secrets/model.key</code>。恢复时使用原密钥，不用新密钥替代。服务升级或更换访问电脑不会把资料迁到浏览器本机。</p>',
    },
    'windows': {
        'EDITION_LABEL': 'Windows 本地安装版', 'START_TITLE': '安装与第一次打开',
        'START_CONTENT': '<ol class="steps"><li>在 Windows 10／11 x64 上运行文枢安装程序，按提示完成当前用户安装，再从桌面或开始菜单打开。无需另外安装 Python、Node.js 或 Docker。</li><li>安装程序会检查 WebView2：已有时复用；缺失时联网补装，失败可重试或取消。离线使用前应先准备好运行库。</li><li>若使用绿色版，完整解压到可写目录，再双击最外层 <code>Wenshu.exe</code>，不要只复制或启动内部程序，也不要与安装版同时运行。</li></ol><div class="note"><p><strong>本地安装不等于自带离线大模型。</strong>原文阅读、检索和已保存文件可在本机使用；摘要、翻译和生成需要另行配置可用模型。网页导入及联网检索还需要相应网络。</p></div>',
        'MODEL_LOCATION': '<div class="note"><p><strong>模型地址从本机访问。</strong>本机运行的模型可使用其本地接口，远程模型需填本机能够访问的地址。跨设备或 Windows 账户迁移后，受当前账户保护的 API Key 通常需要重新填写。</p></div>',
        'CARE_CONTENT': '<p><strong>安装版</strong>默认资料目录为 <code>%LOCALAPPDATA%\\Wenshu</code>，与程序安装目录分开，升级和卸载保留资料。<strong>绿色版</strong>资料在解压目录的 <code>data</code>，窗口偏好、日志及运行配置在 <code>profile</code>。</p><p>备份前保存编辑，正常退出并等待当前任务结束，再整体复制资料目录；绿色版同时保留 <code>profile</code>，需要移动时移动整个绿色目录。不要在运行时覆盖数据库或只复制单个数据库文件。尚未开始的任务保留到下次继续，导入队列处理完毕后再迁移目录。更换电脑或账户后检查模型连接，必要时重填密钥。</p>',
    },
}


def build_manuals(source_dir: Path, *, draft=False):
    template = (source_dir / 'user-guide.template.html').read_text(encoding='utf-8')
    style = (source_dir / 'manual.css').read_text(encoding='utf-8')
    template = template.replace('{{MANUAL_STYLE}}', '<style>' + style + '</style>')
    for key, (filename, caption) in SCREENSHOTS.items():
        path = source_dir / 'screenshots' / filename
        marker = '{{SCREEN_' + key + '}}'
        if template.count(marker) != 1:
            raise SystemExit('Missing or repeated manual screenshot placeholder: ' + marker)
        if path.is_file():
            raw = path.read_bytes()
            if raw[:8] != b'\x89PNG\r\n\x1a\n':
                raise SystemExit('Manual screenshot is not a PNG: ' + str(path))
            width, height = struct.unpack('>II', raw[16:24])
            uri = 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
            picture = (f'<img src="{uri}" alt="{escape(caption)}" width="{width}" height="{height}" '
                       f'style="max-width:{width}px;margin-inline:auto" loading="lazy" decoding="async">'
                       f'<figcaption>{escape(caption)}</figcaption>')
        elif draft:
            picture = f'<div class="pending-screenshot">截图待生成：{escape(caption)}</div>'
        else:
            raise SystemExit('Manual screenshot missing: ' + str(path))
        template = template.replace(marker, picture)
    variants = {}
    for edition, replacements in EDITIONS.items():
        content = template.replace('{{EDITION}}', edition)
        for key, value in replacements.items():
            content = content.replace('{{' + key + '}}', value)
        if re.search(r'\{\{[A-Z_]+\}\}', content):
            raise SystemExit('Unresolved manual placeholder in ' + edition)
        path = PACK / 'manual' / f'user-guide-{edition}.html'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        variants[edition] = {'file': path.relative_to(PACK).as_posix(), 'sha256': digest(path)}
    # Keep old saved URLs and old resource consumers usable. Runtime selection
    # reads variants; existing installed snapshots never change on browsing.
    legacy = PACK / 'manual/user-guide.html'
    legacy.write_bytes((PACK / variants['server']['file']).read_bytes())
    return legacy, variants


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_item(item_id, title, file, topic, kind):
    return {'id': item_id, 'title': title, 'file': file, 'language': 'zh', 'category': '教育与学习 · ' + topic,
            'kind': kind, 'sourceName': '文枢配套文档', 'sourceUrl': '',
            'license': '项目原创 · 随软件分发', 'licenseFile': 'licenses/wenshu-original.txt',
            'attribution': '文枢配套使用说明。', 'snapshotKind': 'original_document',
            'classificationPaths': [{'level1Id': 'education_learning', 'level2': '软件使用' if kind == 'manual' else '写作方法', 'level3': topic}],
            'sha256': digest(PACK / file)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft', action='store_true', help='Temporary QA only: allow missing screenshots')
    parser.add_argument('--source-dir', type=Path, default=os.environ.get('WENSHU_MANUAL_SOURCE_DIR'),
                        help='Manual development source directory; defaults to ignored docs-dev/manual')
    args = parser.parse_args()
    source_dir = (args.source_dir or ROOT / 'docs-dev/manual').expanduser().resolve()
    if not source_dir.is_dir() or not all((source_dir / name).is_file() for name in ('user-guide.template.html', 'manual.css')):
        parser.error('手册开发源未提供；请用 --source-dir DIRECTORY 或 WENSHU_MANUAL_SOURCE_DIR 指向含模板、manual.css 和 screenshots/ 的目录。已有成品帮助无需重新生成即可构建打包。')
    manual, variants = build_manuals(source_dir, draft=args.draft)
    original_notice = PACK / 'licenses/wenshu-original.txt'
    original_notice.parent.mkdir(parents=True, exist_ok=True)
    original_notice.write_text(
        '文枢配套原创文档\n\n使用手册属于文枢配套说明，可随文枢软件提供给用户阅读。'
        '代码库和默认安装包不附带学习文章、教程或写作例文。\n', encoding='utf-8')
    item = original_item('wenshu-user-guide', '文枢使用指南',
                         'manual/user-guide.html', '文枢使用', 'manual')
    item['variants'] = variants
    manifest = {'id': 'wenshu-starter-library', 'version': '2026.11.02.manuals',
                'capturedAt': '2026-11-02', 'items': [item]}
    (PACK / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'manual': str(manual), 'variants': variants,
                      'screenshots': len(SCREENSHOTS), 'manualDocuments': 1,
                      'bundledExamples': 0, 'draft': args.draft}, ensure_ascii=False))


if __name__ == '__main__':
    main()
