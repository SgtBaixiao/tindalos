"""评估 rubric：4 维 × 1/5 锚点 + 确定性检查清单。

维度：
- structural 结构完整性：schema 合法 / id 全局唯一 / 引用可解析 / 幕与场景层级完整
- consistency 一致性：引用与 KG 无矛盾 / 线索可达
- depth 深度：事件密度 / 设定 / NPC 与线索刻画 / 世界关系丰富度
- playability 可玩性：事件完整性与分支 / 线索引导 / 场景可交互

RUBRIC 形状：dim -> {1: 锚点描述, 5: 锚点描述}（1-5 区间两端锚点）。
DETERMINISTIC_CHECKS：确定性检查清单，每条含 id/name/dims（贡献的评分维度）；
core=True 为 spec 规定的 8 项核心检查，其余为深度/可玩性的结构计数补充项。
"""

DIMENSIONS: list[str] = ["structural", "consistency", "depth", "playability"]

RUBRIC: dict[str, dict[int, str]] = {
    "structural": {
        1: "剧本无法通过 models 校验（悬空引用/重复 id/未知键），或存在空幕、空场景等结构性残缺，层级不可运行",
        5: "结构完整：schema 合法、id 全局唯一、引用全部可解析、每幕至少一个场景、每场景至少一个事件",
    },
    "consistency": {
        1: "引用大面积悬空，KG 存在矛盾（端点未注册/有效窗重叠或倒置），线索与事件、NPC 相互脱节",
        5: "引用全部可解析，KG 无矛盾，每条线索都有可达的 linked 目标，NPC/场景/线索/事件彼此咬合",
    },
    "depth": {
        1: "事件稀疏（平均每场景 <2）、场景无时间地点设定、NPC 无 personality 与描述、线索无描述、世界关系近乎为空",
        5: "事件密度充足，场景含 time/place 设定，NPC 有 personality 与描述，线索有描述，世界知识图谱关系丰富（≥2 条）",
    },
    "playability": {
        1: "场景无事件或事件无描述/无后续走向，线索不可达，无任何分支，KP 无法据此主持",
        5: "每场景有事件，事件有完整描述与后续走向，存在分支选择（next_event_ids ≥2），线索均可引导至事件",
    },
}

DETERMINISTIC_CHECKS: list[dict] = [
    # —— spec 规定的 8 项核心确定性检查 ——
    {"id": "schema_valid", "name": "schema 合法（models 校验通过）", "dims": ["structural"], "core": True},
    {"id": "id_unique", "name": "id 全局唯一（幕/场景/事件/NPC/线索）", "dims": ["structural"], "core": True},
    {"id": "refs_resolvable", "name": "引用全部可解析（NPC/事件/幕）", "dims": ["structural", "consistency"], "core": True},
    {"id": "kg_consistent", "name": "KG 无矛盾（端点注册/有效窗）", "dims": ["consistency"], "core": True},
    {"id": "act_has_scene", "name": "每幕至少一个场景", "dims": ["structural"], "core": True},
    {"id": "scene_has_event", "name": "每个场景至少一个事件", "dims": ["structural", "playability"], "core": True},
    {"id": "npc_personality", "name": "每个 NPC 均有 personality", "dims": ["depth"], "core": True},
    {"id": "clue_linked", "name": "每条线索有 linked 目标", "dims": ["consistency", "playability"], "core": True},
    # —— 深度/可玩性的结构计数补充项 ——
    {"id": "event_density", "name": "平均每场景事件数 ≥2", "dims": ["depth"]},
    {"id": "setting_complete", "name": "场景含 time/place 设定", "dims": ["depth"]},
    {"id": "npc_described", "name": "NPC 有描述或 personality", "dims": ["depth"]},
    {"id": "clue_described", "name": "线索有描述", "dims": ["depth"]},
    {"id": "relation_richness", "name": "世界知识图谱关系数 ≥2", "dims": ["depth"]},
    {"id": "event_complete", "name": "事件均有 description", "dims": ["playability"]},
    {"id": "branching", "name": "存在分支（某事件 next_event_ids ≥2）", "dims": ["playability"]},
]
