"""
台股概念股 / 產業關聯資料
靈感來源：Taiwan-Stock-Knowledge-Graph (jojowither)
用靜態對照表 + networkx 輕量化實作，不依賴 Neo4j
"""
from __future__ import annotations

# 概念 → 成分股（代碼, 名稱）
# 可持續擴充；同一檔股票可屬於多個概念
CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "AI 伺服器": [
        ("2330", "台積電"), ("2317", "鴻海"), ("2382", "廣達"), ("2376", "技嘉"),
        ("3231", "緯創"), ("2356", "英業達"), ("6669", "緯穎"), ("3017", "奇鋐"),
        ("3034", "聯詠"), ("2379", "瑞昱"),
    ],
    "CoWoS / 先進封裝": [
        ("2330", "台積電"), ("3711", "日月光投控"), ("6147", "頎邦"), ("8039", "台虹"),
        ("3189", "景碩"), ("3533", "嘉澤"), ("6415", "矽力-KY"),
    ],
    "CoPoS / 面板級封裝": [
        ("2330", "台積電"),
        # 設備供應商
        ("6789", "采鈺"), ("3535", "晶彩科"), ("3680", "家登"),
        ("6664", "群翊"), ("2467", "志聖"), ("7734", "印能"),
        ("5443", "均豪"), ("6640", "均華"), ("6187", "萬潤"),
        ("3131", "弘塑"), ("3583", "辛耘"),
        # 封測大廠
        ("3711", "日月光投控"), ("2449", "京元電子"), ("6239", "力成"),
    ],
    "HBM / 記憶體": [
        ("2408", "南亞科"), ("3532", "台勝科"), ("5269", "祥碩"), ("2344", "華邦電"),
        ("8150", "南茂"), ("8046", "南電"),
    ],
    "Apple 供應鏈": [
        ("2330", "台積電"), ("2317", "鴻海"), ("2354", "鴻準"), ("3008", "大立光"),
        ("2474", "可成"), ("4938", "和碩"), ("2383", "台光電"), ("2392", "正崴"),
    ],
    "電動車 / 特斯拉": [
        ("2317", "鴻海"), ("2308", "台達電"), ("2313", "華通"), ("2376", "技嘉"),
        ("1503", "士電"), ("6409", "旭隼"), ("3665", "貿聯-KY"),
    ],
    "5G / 網通": [
        ("2412", "中華電"), ("3045", "台灣大"), ("4904", "遠傳"), ("2345", "智邦"),
        ("2332", "友訊"), ("3380", "明泰"), ("6285", "啟碁"),
    ],
    "重電 / 電網": [
        ("1503", "士電"), ("1513", "中興電"), ("1514", "亞力"), ("1519", "華城"),
        ("1526", "日馳"), ("8070", "長華*"),
    ],
    "散熱": [
        ("3017", "奇鋐"), ("3324", "雙鴻"), ("3653", "健策"), ("8210", "勤誠"),
        ("6196", "帆宣"),
    ],
    "矽光子": [
        ("3450", "聯鈞"), ("3455", "由田"), ("2455", "全新"), ("4977", "眾達-KY"),
        ("3363", "上詮"), ("5202", "力新"),
    ],
    "光通訊": [
        ("2455", "全新"), ("3450", "聯鈞"), ("4977", "眾達-KY"), ("3363", "上詮"),
        ("5203", "訊連"), ("5284", "jpp-KY"), ("5292", "華申"),
    ],
    "軍工 / 無人機": [
        ("2634", "漢翔"), ("8011", "台通"), ("4803", "VHQ-KY"), ("3413", "京鼎"),
        ("2643", "捷迅"),
    ],
    "航運": [
        ("2603", "長榮"), ("2609", "陽明"), ("2615", "萬海"), ("2610", "華航"),
        ("2618", "長榮航"),
    ],
    "金融": [
        ("2881", "富邦金"), ("2882", "國泰金"), ("2884", "玉山金"), ("2886", "兆豐金"),
        ("2891", "中信金"), ("2892", "第一金"), ("5880", "合庫金"),
    ],
    "面板": [
        ("2409", "友達"), ("3481", "群創"), ("6116", "彩晶"), ("3105", "穩懋"),
    ],
    "生技製藥": [
        ("4174", "浩鼎"), ("4142", "國光生"), ("4966", "譜瑞-KY"), ("6446", "藥華藥"),
        ("6452", "康友-KY"),
    ],
    "太陽能 / 綠能": [
        ("3576", "聯合再生"), ("6244", "茂迪"), ("8299", "群聯"), ("1722", "台肥"),
    ],
    "衛星 / 低軌": [
        ("3704", "合勤控"), ("2345", "智邦"), ("6285", "啟碁"), ("4906", "正文"),
    ],
    "銅箔 / CCL": [
        ("8358", "金居"), ("2383", "台光電"), ("6213", "聯茂"),
        ("2368", "金像電"), ("8046", "南電"),
    ],
    "AI ASIC / IP": [
        ("2330", "台積電"), ("3443", "創意"), ("3661", "世芯-KY"),
        ("3529", "力旺"), ("6643", "M31"),
    ],
    "AI 散熱": [
        ("3017", "奇鋐"), ("3324", "雙鴻"), ("3653", "健策"),
        ("8210", "勤誠"), ("8996", "高力"),
    ],
    "AI 電源": [
        ("2308", "台達電"), ("2301", "光寶科"), ("6412", "群電"),
    ],
}


def build_stock_to_concepts() -> dict[str, list[str]]:
    """反向索引：股票代碼 → 所屬概念列表"""
    rev: dict[str, list[str]] = {}
    for concept, stocks in CONCEPTS.items():
        for sid, _ in stocks:
            rev.setdefault(sid, []).append(concept)
    return rev


def stock_name_lookup() -> dict[str, str]:
    """快速查詢股票名稱"""
    d: dict[str, str] = {}
    for stocks in CONCEPTS.values():
        for sid, name in stocks:
            d[sid] = name
    return d


def related_stocks(stock_id: str) -> dict[str, list[tuple[str, str]]]:
    """給定股票代碼，回傳同概念股清單
    return: { concept_name: [(stock_id, name), ...] }
    """
    rev = build_stock_to_concepts()
    concepts = rev.get(stock_id, [])
    result: dict[str, list[tuple[str, str]]] = {}
    for c in concepts:
        peers = [(sid, name) for sid, name in CONCEPTS[c] if sid != stock_id]
        result[c] = peers
    return result


def build_graph_edges(stock_id: str, max_peers_per_concept: int = 6) -> tuple[list, list]:
    """建立以 stock_id 為中心的子圖（nodes, edges）
    nodes: [{id, label, type}]  type: 'center'|'concept'|'stock'
    edges: [{source, target}]
    """
    names = stock_name_lookup()
    center_name = names.get(stock_id, stock_id)
    nodes = [{"id": stock_id, "label": f"{stock_id} {center_name}", "type": "center"}]
    edges = []
    seen_nodes = {stock_id}

    rel = related_stocks(stock_id)
    for concept, peers in rel.items():
        cid = f"C::{concept}"
        nodes.append({"id": cid, "label": concept, "type": "concept"})
        edges.append({"source": stock_id, "target": cid})
        for sid, name in peers[:max_peers_per_concept]:
            if sid not in seen_nodes:
                nodes.append({"id": sid, "label": f"{sid} {name}", "type": "stock"})
                seen_nodes.add(sid)
            edges.append({"source": cid, "target": sid})
    return nodes, edges
