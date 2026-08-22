"""搜索并下载论文工具（多源版本）

支持多个学术搜索源（全部使用官方API，非爬虫）：
1. arXiv - 公开API，无限制
2. Semantic Scholar - 公开API，1 req/sec
3. CrossRef - 公开API，无限制
4. PubMed - 公开API，无限制

PDF下载策略：
1. arXiv PDF - 直接下载，无拦截
2. 开放获取PDF - 直接下载
3. 受限PDF - 提示用户手动下载或使用代理

human-in-the-loop机制：搜索→用户选择→确认下载
"""
import os
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNS_DIR = os.path.join(_PIPELINE_DIR, "runs")

# 用户代理轮换列表
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
]


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    log_path = os.path.join(_PIPELINE_DIR, "agent.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _get_ua() -> str:
    """随机用户代理"""
    import random
    return random.choice(USER_AGENTS)


def _fetch_json(url: str, headers: dict = None, timeout: int = 15) -> dict:
    """获取JSON响应"""
    req = urllib.request.Request(url, headers=headers or {'User-Agent': _get_ua()})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def _fetch_xml(url: str, timeout: int = 15) -> str:
    """获取XML响应"""
    req = urllib.request.Request(url, headers={'User-Agent': _get_ua()})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode('utf-8')


def _download_file(url: str, path: str, timeout: int = 60) -> bool:
    """下载文件到指定路径"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': _get_ua()})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            with open(path, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        _log(f"_download_file error: {e}")
        return False


# ─────────────────────────────────────────────
# 搜索源 1: arXiv (官方API，无限制)
# ─────────────────────────────────────────────

def search_arxiv(query: str, max_results: int = 3) -> list:
    """搜索arXiv论文"""
    _log(f"search_arxiv: query='{query}'")
    
    # 使用全字段搜索，更宽松
    search_query = f"all:{urllib.parse.quote(query)}"
    url = f"https://export.arxiv.org/api/query?search_query={search_query}&max_results={max_results}&sortBy=relevance"
    
    try:
        xml_data = _fetch_xml(url)
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        root = ET.fromstring(xml_data)
        
        papers = []
        for entry in root.findall('a:entry', ns):
            arxiv_id = entry.find('a:id', ns).text.strip().split('/abs/')[-1]
            title = entry.find('a:title', ns).text.strip().replace('\n', ' ')
            authors = [a.find('a:name', ns).text for a in entry.findall('a:author', ns)]
            abstract = entry.find('a:summary', ns).text.strip()
            published = entry.find('a:published', ns).text[:10]
            
            papers.append({
                'id': arxiv_id,
                'title': title,
                'authors': ', '.join(authors[:3]) + ('...' if len(authors) > 3 else ''),
                'abstract': abstract[:300] + '...' if len(abstract) > 300 else abstract,
                'published': published,
                'pdf_url': f"https://arxiv.org/pdf/{arxiv_id}",
                'source': 'arXiv',
                'pdf_available': True,  # arXiv PDF总是可用
            })
        
        _log(f"search_arxiv: found {len(papers)} papers")
        return papers
    except Exception as e:
        _log(f"search_arxiv error: {e}")
        return []


# ─────────────────────────────────────────────
# 搜索源 2: Semantic Scholar (官方API，1 req/sec)
# ─────────────────────────────────────────────

def search_semantic_scholar(query: str, max_results: int = 3) -> list:
    """搜索Semantic Scholar论文"""
    _log(f"search_semantic_scholar: query='{query}'")
    
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={urllib.parse.quote(query)}&limit={max_results}&fields=title,authors,year,abstract,externalIds,openAccessPdf"
    
    try:
        time.sleep(1)  # 尊重速率限制
        data = _fetch_json(url)
        papers = []
        
        for item in data.get('data', []):
            external_ids = item.get('externalIds', {})
            arxiv_id = external_ids.get('ArXiv')
            doi = external_ids.get('DOI')
            
            if arxiv_id:
                paper_id = arxiv_id
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
                pdf_available = True
            elif doi:
                paper_id = doi
                open_pdf = item.get('openAccessPdf', {})
                pdf_url = open_pdf.get('url', '')
                pdf_available = bool(pdf_url)
            else:
                paper_id = item.get('paperId', '')
                open_pdf = item.get('openAccessPdf', {})
                pdf_url = open_pdf.get('url', '')
                pdf_available = bool(pdf_url)
            
            authors = [a.get('name', '') for a in item.get('authors', [])]
            
            papers.append({
                'id': paper_id,
                'title': item.get('title', ''),
                'authors': ', '.join(authors[:3]) + ('...' if len(authors) > 3 else ''),
                'abstract': (item.get('abstract') or '')[:300],
                'published': str(item.get('year', '')),
                'pdf_url': pdf_url,
                'source': 'Semantic Scholar',
                'pdf_available': pdf_available,
            })
        
        _log(f"search_semantic_scholar: found {len(papers)} papers")
        return papers
    except Exception as e:
        _log(f"search_semantic_scholar error: {e}")
        return []


# ─────────────────────────────────────────────
# 搜索源 3: CrossRef (官方API，无限制)
# ─────────────────────────────────────────────

def search_crossref(query: str, max_results: int = 3) -> list:
    """搜索CrossRef论文"""
    _log(f"search_crossref: query='{query}'")
    
    url = f"https://api.crossref.org/works?query={urllib.parse.quote(query)}&rows={max_results}"
    
    try:
        data = _fetch_json(url)
        papers = []
        
        for item in data.get('message', {}).get('items', []):
            doi = item.get('DOI', '')
            title = item.get('title', [''])[0] if item.get('title') else ''
            authors = []
            for author in item.get('author', []):
                name = f"{author.get('given', '')} {author.get('family', '')}".strip()
                if name:
                    authors.append(name)
            
            pdf_url = ''
            pdf_available = False
            for link in item.get('link', []):
                if link.get('content-type') == 'application/pdf':
                    pdf_url = link.get('URL', '')
                    pdf_available = True
                    break
            
            published = ''
            if item.get('published-print'):
                parts = item['published-print'].get('date-parts', [[]])[0]
                if parts:
                    published = str(parts[0])
            
            papers.append({
                'id': doi,
                'title': title,
                'authors': ', '.join(authors[:3]) + ('...' if len(authors) > 3 else ''),
                'abstract': (item.get('abstract') or '')[:300],
                'published': published,
                'pdf_url': pdf_url,
                'source': 'CrossRef',
                'pdf_available': pdf_available,
            })
        
        _log(f"search_crossref: found {len(papers)} papers")
        return papers
    except Exception as e:
        _log(f"search_crossref error: {e}")
        return []


# ─────────────────────────────────────────────
# 搜索源 4: PubMed (官方API，无限制)
# ─────────────────────────────────────────────

def search_pubmed(query: str, max_results: int = 3) -> list:
    """搜索PubMed论文"""
    _log(f"search_pubmed: query='{query}'")
    
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={urllib.parse.quote(query)}&retmax={max_results}&retmode=json"
    
    try:
        search_data = _fetch_json(search_url)
        ids = search_data.get('esearchresult', {}).get('idlist', [])
        
        if not ids:
            return []
        
        ids_str = ','.join(ids)
        detail_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={ids_str}&retmode=json"
        detail_data = _fetch_json(detail_url)
        
        papers = []
        for pmid in ids:
            item = detail_data.get('result', {}).get(pmid, {})
            if not item:
                continue
            
            authors = [a.get('name', '') for a in item.get('authors', [])]
            
            papers.append({
                'id': pmid,
                'title': item.get('title', ''),
                'authors': ', '.join(authors[:3]) + ('...' if len(authors) > 3 else ''),
                'abstract': '',
                'published': item.get('pubdate', '')[:10],
                'pdf_url': f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                'source': 'PubMed',
                'pdf_available': False,  # PubMed通常需要订阅
            })
        
        _log(f"search_pubmed: found {len(papers)} papers")
        return papers
    except Exception as e:
        _log(f"search_pubmed error: {e}")
        return []


# ─────────────────────────────────────────────
# 统一搜索接口
# ─────────────────────────────────────────────

def search_papers(query: str, max_results: int = 3, sources: list = None) -> list:
    """多源搜索论文"""
    if sources is None:
        sources = ['arxiv', 'semantic_scholar', 'crossref', 'pubmed']
    
    _log(f"search_papers: query='{query}', sources={sources}")
    
    all_papers = []
    
    for source in sources:
        try:
            if source == 'arxiv':
                papers = search_arxiv(query, max_results)
            elif source == 'semantic_scholar':
                papers = search_semantic_scholar(query, max_results)
            elif source == 'crossref':
                papers = search_crossref(query, max_results)
            elif source == 'pubmed':
                papers = search_pubmed(query, max_results)
            else:
                continue
            
            all_papers.extend(papers)
        except Exception as e:
            _log(f"search_papers: {source} error: {e}")
    
    # 去重（按标题）
    seen_titles = set()
    unique_papers = []
    for paper in all_papers:
        title_lower = paper['title'].lower().strip()
        if title_lower and title_lower not in seen_titles:
            seen_titles.add(title_lower)
            unique_papers.append(paper)
    
    _log(f"search_papers: total {len(unique_papers)} unique papers")
    return unique_papers[:max_results * 2]


# ─────────────────────────────────────────────
# 下载论文
# ─────────────────────────────────────────────

def download_paper(paper_id: str, run_id: str, source: str = 'arxiv') -> dict:
    """下载论文PDF并提取文本
    
    Returns:
        {
            'success': bool,
            'text': str,  # 提取的文本（如果成功）
            'pdf_path': str,  # PDF保存路径
            'message': str,  # 提示信息
        }
    """
    _log(f"download_paper: paper_id='{paper_id}', run_id='{run_id}', source='{source}'")
    
    run_dir = os.path.join(_RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    
    # 确定PDF URL
    if source == 'arxiv' or ('.' in paper_id and '/' not in paper_id and not paper_id.startswith('10.')):
        pdf_url = f"https://arxiv.org/pdf/{paper_id}"
    elif source == 'semantic_scholar':
        try:
            url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields=openAccessPdf"
            data = _fetch_json(url)
            pdf_url = data.get('openAccessPdf', {}).get('url', '')
        except:
            pdf_url = ''
    elif paper_id.startswith('10.') or source == 'crossref':
        # DOI - try to resolve via CrossRef API
        pdf_url = ''
        try:
            import urllib.parse
            doi_url = f"https://api.crossref.org/works/{urllib.parse.quote(paper_id, safe='')}"
            data = _fetch_json(doi_url, timeout=15)
            # Try to get open-access PDF link
            links = data.get('message', {}).get('link', [])
            for link in links:
                if link.get('content-type') == 'application/pdf':
                    pdf_url = link.get('URL', '')
                    break
            # Fallback: try Unpaywall
            if not pdf_url:
                try:
                    unpaywall_url = f"https://api.unpaywall.org/v2/{paper_id}?email=test@example.com"
                    up_data = _fetch_json(unpaywall_url, timeout=10)
                    best_oa = up_data.get('best_oa_location', {})
                    if best_oa:
                        pdf_url = best_oa.get('url_for_pdf') or best_oa.get('url', '')
                except:
                    pass
        except Exception as e:
            _log(f"DOI resolve error for {paper_id}: {e}")
    else:
        pdf_url = ''
    
    if not pdf_url:
        return {
            'success': False,
            'message': f"无法获取PDF链接（DOI: {paper_id}）。请手动下载论文并上传PDF文件。",
        }
    
    pdf_path = os.path.join(run_dir, f"{paper_id.replace('/', '_')}.pdf")
    txt_path = os.path.join(run_dir, "original.txt")
    
    # 尝试下载PDF
    _log(f"download_paper: downloading {pdf_url}")
    if not _download_file(pdf_url, pdf_path):
        return {
            'success': False,
            'message': f"PDF下载失败。可能是网络问题或访问被拒绝。\n\n请手动下载PDF：{pdf_url}\n\n然后上传PDF文件或粘贴文本内容。",
        }
    
    _log(f"download_paper: saved to {pdf_path}")
    
    # 提取文本
    text = ""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        for page in doc:
            text += page.get_text()
        doc.close()
    except ImportError:
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(pdf_path)
        except ImportError:
            return {
                'success': True,
                'pdf_path': pdf_path,
                'message': f"PDF已下载到 {pdf_path}，但无法自动提取文本。\n\n请手动复制文本内容或安装pymupdf：pip install pymupdf",
            }
    
    if not text:
        return {
            'success': False,
            'pdf_path': pdf_path,
            'message': f"PDF已下载但提取文本失败。请手动复制文本内容。",
        }
    
    # 保存提取的文本
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(text)
    
    _log(f"download_paper: extracted {len(text)} chars to {txt_path}")
    return {
        'success': True,
        'text': text,
        'pdf_path': pdf_path,
        'message': f"已下载并提取文本，共 {len(text)} 字符。",
    }


def search_and_download_paper(query: str, run_id: str, max_results: int = 3) -> dict:
    """搜索并下载论文（human-in-the-loop版本）"""
    _log(f"search_and_download_paper: query='{query}', run_id='{run_id}'")
    
    papers = search_papers(query, max_results)
    
    if not papers:
        return {
            'action': 'error',
            'message': f'未找到与"{query}"相关的论文。\n\n建议：\n1. 尝试英文关键词\n2. 尝试更具体的论文标题\n3. 直接上传PDF文件'
        }
    
    return {
        'action': 'select_paper',
        'papers': papers,
        'message': f'找到 {len(papers)} 篇相关论文：',
        'run_id': run_id,
    }


def confirm_and_download(paper_id: str, run_id: str, source: str = 'arxiv') -> dict:
    """用户确认后下载论文"""
    _log(f"confirm_and_download: paper_id='{paper_id}', run_id='{run_id}', source='{source}'")
    
    result = download_paper(paper_id, run_id, source)
    
    if not result['success']:
        return {
            'action': 'error',
            'message': result['message'],
        }
    
    # 获取论文信息
    papers = search_papers(paper_id, 1)
    paper = papers[0] if papers else {'id': paper_id, 'title': 'Unknown'}
    
    return {
        'action': 'downloaded',
        'paper': paper,
        'text': result.get('text', ''),
        'message': f"已下载论文: {paper['title']}\n\n{result['message']}",
    }
