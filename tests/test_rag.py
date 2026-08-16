"""RAG 模块测试：分块/分词/BM25/入库幂等/混合检索/QA 本地降级。

全部离线跑：autouse fixture 把 TINDALOS_RAG_DIR 指向 pytest tmp 目录，
清空 LLM/embedding key 并禁用 LLM；跑完 reset() 清理数据目录。
"""

from __future__ import annotations

import pytest

from tindalos import rag

# 真实感模组文字 fixture：含德罗赫达 / 蛇人 / 缪楚等实体
FIXTURE = """德罗赫达镇（Drogheda）位于爱尔兰东海岸，居民大多信奉天主教，对本地蛇人传说讳莫如深。

调查员抵达德罗赫达时正值深秋，海风裹挟着咸腥味。镇民口中的"蛇人"并非单纯的乡野怪谈——他们世代相传：每逢月圆之夜，废弃的圣布里奇特教堂下会传出鳞片摩擦石板的声响。

缪楚（Muirchu）是本地的古董商，自称能通晓古凯尔特文。他手中有一卷残缺的羊皮纸，记载着蛇人祭司的献祭仪式，上面还标注着德罗赫达附近三处地下祭坛的位置。

老渔夫康纳在码头边低声告诫调查员：千万别在日落之后接近教堂地窖，那里供奉着蛇人神祇的蛇蜕。缪楚对此只报以冷笑，说康纳不过是胆小怕事。

在教堂地窖的壁画上，调查员辨认出蛇人向海洋巨兽献祭的场景。壁画下方刻着一行古文字，缪楚辨认出那是"血脉与鳞片俱归于深渊"。

德罗赫达档案馆保存着 1892 年的一份验尸报告，死者全身布满蛇形咬痕。档案管理员承认，类似案件在过去百年间出现过七次。

调查员在缪楚的古董店里发现一个青铜匣，匣盖刻着盘绕的蛇。打开时，一股陈腐的檀香气味涌出，里面放着一枚蛇鳞状的黑曜石符咒。"""

# 第二模组：与缪楚无关，用于 module_id 过滤测试
FIXTURE2 = """旧港镇的石匠坊里，一尊石像鬼像在雨夜微微转动脖颈。泥瓦匠们坚信那是镇守者，从不打扫它脚下的碎石。

石匠坊地窖藏着一本账册，记载着向"无名之物"供奉石料与鲜血的记录。账册的纸页边缘被虫蛀出蛇形的孔洞。"""


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """零退避睡眠：LLMClient 重试路径（网络/5xx/429/408）不真正 sleep，防测试挂慢。"""
    import tindalos.llm as llm

    monkeypatch.setattr(llm, "_sleep_backoff", lambda attempt: None)


@pytest.fixture(autouse=True)
def rag_env(tmp_path, monkeypatch):
    """每测一个独立 rag 目录 + 强制离线/本地模式。"""
    monkeypatch.setenv("TINDALOS_RAG_DIR", str(tmp_path / "rag"))
    monkeypatch.delenv("TINDALOS_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("TINDALOS_LLM_ENABLED", "0")
    monkeypatch.delenv("TINDALOS_DASHSCOPE_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("TINDALOS_EMBED_BASE", raising=False)
    # config 单例按 env 一次性构造：重置后 get_settings() 才能读到本轮 env
    monkeypatch.setattr("tindalos.config._settings", None)
    yield
    rag.reset()


# --------------------------------------------------------------------------
# 分块
# --------------------------------------------------------------------------


def test_chunk_text_length_and_overlap():
    text = "a" * 500
    chunks = rag.chunk_text(text, chunk_chars=400, overlap=80)
    assert len(chunks) == 2
    assert len(chunks[0]) == 400
    # 相邻窗口重叠 80 字符
    assert chunks[0][320:400] == chunks[1][:80]


def test_chunk_text_small_and_empty():
    assert rag.chunk_text("短文本") == ["短文本"]
    assert rag.chunk_text("") == []
    assert rag.chunk_text("   ") == []


# --------------------------------------------------------------------------
# 分词 + BM25
# --------------------------------------------------------------------------


def test_tokenize_chinese_bigrams():
    toks = rag.tokenize("克苏鲁神话")
    for bigram in ["克苏", "苏鲁", "鲁神", "神话"]:
        assert bigram in toks
    # 单字也在
    assert "克" in toks and "话" in toks


def test_tokenize_latin_lowercase():
    assert "drogheda" in rag.tokenize("Drogheda cult")
    assert "cult" in rag.tokenize("Drogheda cult")
    assert rag.tokenize("FOO BAR") == ["foo", "bar"]


def test_bm25_hits_relevant_sentence():
    docs = [s.strip() for s in FIXTURE.split("\n\n") if s.strip()]
    bm = rag.BM25Index()
    bm.fit(docs)
    assert len(bm.score("缪楚")) == len(docs)
    top = bm.search("缪楚", top_k=3)
    assert top and "缪楚" in top[0]["text"]


def test_bm25_search_doc_id_filter():
    docs = [s.strip() for s in FIXTURE.split("\n\n") if s.strip()]
    bm = rag.BM25Index()
    bm.fit(docs, doc_ids=[f"d{i}" for i in range(len(docs))])
    top = bm.search("缪楚", top_k=5, doc_ids=["d2"])  # 只允许 d2
    assert all(r["doc_id"] == "d2" for r in top)
    assert top and "缪楚" in top[0]["text"]


def test_offline_embedder_deterministic():
    embed = rag.make_embedder()
    a = embed(["克苏鲁神话", "蛇人祭司"])
    b = embed(["克苏鲁神话", "蛇人祭司"])
    assert a.shape == (2, 256)
    assert a.dtype == "float32"
    assert (a == b).all()  # 纯函数、无随机、跨调用可复现
    # L2 归一化
    norms = (a ** 2).sum(axis=1) ** 0.5
    assert all(abs(n - 1.0) < 1e-4 for n in norms)


# --------------------------------------------------------------------------
# 入库 + 检索
# --------------------------------------------------------------------------


def test_ingest_module_idempotent():
    n1 = rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    n2 = rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    assert n1 == n2 and n1 > 0
    s = rag.stats()
    assert s["chunks"] == n1  # 幂等：重复入库 chunk 数不变
    assert s["modules"] == 1
    assert s["embed_mode"] == "offline"


def test_search_returns_relevant_block():
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    res = rag.search("缪楚", top_k=3)
    assert res
    assert all(r["module_id"] == "m1" for r in res)
    assert "缪楚" in res[0]["text"]
    assert set(res[0]) >= {"text", "score", "module_id", "module_name", "chunk_index", "kind"}
    assert res[0]["kind"] == "child"


def test_search_module_id_filter():
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    rag.ingest_module("m2", "旧港石匠坊", FIXTURE2)
    res = rag.search("缪楚", module_id="m1", top_k=3)
    assert res and all(r["module_id"] == "m1" for r in res)
    assert "缪楚" in res[0]["text"]
    # 另一模块不含缪楚，无过滤时也不应命中
    res2 = rag.search("石像鬼", module_id="m2", top_k=3)
    assert res2 and all(r["module_id"] == "m2" for r in res2)


def test_search_empty_index():
    assert rag.search("缪楚") == []


# --------------------------------------------------------------------------
# QA
# --------------------------------------------------------------------------


def test_qa_local_mode_without_key():
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    out = rag.qa("缪楚是什么人物？")
    assert out["mode"] == "local"
    assert out["answer"]
    assert "无 LLM" in out["answer"]
    assert out["sources"]
    assert out["rules"] is None
    src = out["sources"][0]
    assert {"text", "module_id", "module_name", "score"} <= set(src)


def test_qa_with_rules_tag():
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    out = rag.qa("蛇人祭司的献祭仪式在哪记载？", rules="COC7")
    assert out["rules"] == "COC7"
    assert out["mode"] == "local"


def test_qa_llm_failure_degrades_to_local(monkeypatch):
    """有 key + LLM 启用但端点不可达 → 诚实降级本地。"""
    monkeypatch.setenv("TINDALOS_API_KEY", "sk-test")
    monkeypatch.setenv("TINDALOS_LLM_ENABLED", "1")
    monkeypatch.setenv("TINDALOS_API_BASE", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("TINDALOS_LLM_TIMEOUT", "2")
    # 重置 config 单例，使本轮 env 生效（否则 get_settings() 拿到旧配置）
    monkeypatch.setattr("tindalos.config._settings", None)
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    out = rag.qa("缪楚是什么人物？")
    assert out["mode"] == "local"
    assert "degraded_reason" in out
    assert out["answer"]


def test_qa_empty_index():
    out = rag.qa("缪楚是谁？")
    assert out["mode"] == "local"
    assert out["sources"] == []
    assert out["answer"]


def test_dedup_sources_keeps_highest_score():
    """内容去重：同文本只留 score 最高的一条；空文本剔除。"""
    srcs = [
        {"text": "A 内容", "score": 0.3},
        {"text": "A 内容", "score": 0.9},
        {"text": "B 内容", "score": 0.5},
        {"text": "", "score": 1.0},
        {"text": "  A  内容 ", "score": 0.6},  # 空白差异也应视为同一文本
    ]
    out = rag._dedup_sources(srcs)
    assert len(out) == 2
    assert out[0]["score"] == 0.9
    assert out[1]["score"] == 0.5


def test_qa_sources_dedup_duplicate_module_content():
    """同一 PDF 用两个 module_id 重复上传 → qa() 的 sources 无重复文本。"""
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    rag.ingest_module("m1b", "德罗赫达之宴", FIXTURE)  # 模拟同一规则书上传两次
    out = rag.qa("缪楚是什么人物？")
    texts = [s["text"] for s in out["sources"]]
    assert len(texts) == len(set(texts))
    # 关键信息仍应命中
    assert any("缪楚" in t for t in texts)


def test_reset_clears_data():
    rag.ingest_module("m1", "德罗赫达之宴", FIXTURE)
    assert rag.stats()["chunks"] > 0
    rag.reset()
    s = rag.stats()
    assert s["chunks"] == 0
    assert s["modules"] == 0
