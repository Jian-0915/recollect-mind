"""记忆标签：写入时按关键词打主题标签，支持自定义标签。

不调模型 —— 12 组主题词，命中数排序取前 3。标签只用于筛选/统计，不参与召回打分。
"""
MAX_TAGS = 3

_TAGS = {
    "旅游": ("旅行", "旅游", "出游", "景点", "景区", "爬山", "露营", "酒店", "民宿",
             "机票", "高铁", "行李", "门票", "西湖", "哈尔滨", "成都", "云南", "三亚",
             "杭州", "出差", "自驾", "海边"),
    "工作": ("加班", "上班", "工作", "公司", "项目", "老板", "领导", "会议", "面试",
             "辞职", "跳槽", "客户", "汇报", "需求", "代码", "开发", "调试", "同事",
             "工位", "工资", "绩效", "简历"),
    "家庭": ("妈妈", "爸爸", "奶奶", "爷爷", "表哥", "表姐", "表妹", "表弟", "姥姥",
             "姥爷", "舅舅", "姑姑", "家人", "亲戚", "婚礼", "结婚", "伴郎", "嫂子",
             "老家", "父母", "亲兄弟"),
    "学习": ("考研", "考试", "复习", "论文", "答辩", "毕业设计", "课程", "老师",
             "学习", "背单词", "自习", "证书", "图书馆", "专业", "课题"),
    "健康": ("医院", "看病", "体检", "疼", "腰痛", "感冒", "发烧", "失眠", "跑步",
             "健身", "按摩", "锻炼", "早睡", "体重", "减肥", "熬夜", "累"),
    "饮食": ("吃", "做饭", "红烧肉", "咖啡", "蛋糕", "奶茶", "餐厅", "外卖", "火锅",
             "酸奶", "食谱", "厨房", "手冲", "菜", "早饭", "晚饭"),
    "宠物": ("猫", "狗", "宠物", "团子", "铲屎", "遛狗", "皮蛋"),
    "消费": ("买", "购物", "下单", "花了", "价格", "快递", "淘宝", "京东", "付款",
             "优惠", "退货"),
    "居住": ("搬家", "房子", "租房", "装修", "宿舍", "小区", "绿萝", "窗台", "房东",
             "水电", "家电"),
    "娱乐": ("电影", "游戏", "演唱会", "看剧", "悬疑剧", "音乐", "唱歌", "尤克里里",
             "读书", "小说", "综艺", "球赛"),
    "情绪": ("开心", "难过", "焦虑", "失望", "紧张", "生气", "放松", "害怕", "委屈",
             "兴奋", "emo"),
    "社交": ("朋友", "聚会", "请客", "聊天", "发小", "室友", "同学", "闺蜜", "约",
             "生日", "相亲"),
}
_ORDER = {t: i for i, t in enumerate(_TAGS)}


def tags_for(text, limit=MAX_TAGS):
    """按命中主题词数打分，取前 N 个标签。"""
    t = text or ""
    hits = []
    for tag, words in _TAGS.items():
        n = sum(1 for w in words if w in t)
        if n:
            hits.append((n, tag))
    hits.sort(key=lambda x: (-x[0], _ORDER[x[1]]))
    return [tag for _, tag in hits[:limit]]


def add(conn, event_id, tags, source="auto"):
    """追加标签（同事件同标签只留一条）。"""
    out = []
    for t in tags or []:
        t = (t or "").strip()[:12]
        if not t or t in out:
            continue
        out.append(t)
        conn.execute(
            "INSERT OR IGNORE INTO event_tags(event_id,tag,source) VALUES(?,?,?)",
            (event_id, t, source),
        )
    return out


def for_event(conn, event_id):
    return [r["tag"] for r in conn.execute(
        "SELECT tag FROM event_tags WHERE event_id=? ORDER BY rowid", (event_id,))]


def by_events(conn, ids):
    """批量取：{event_id: [tag]}，避免列表页 N+1。"""
    out = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in conn.execute(
            "SELECT event_id,tag FROM event_tags WHERE event_id IN (%s)" % marks, tuple(ids)):
        out.setdefault(r["event_id"], []).append(r["tag"])
    return out


def all_tags(conn):
    return [{"tag": r["tag"], "count": r["n"]} for r in conn.execute(
        "SELECT tag, COUNT(*) n FROM event_tags GROUP BY tag ORDER BY n DESC")]


def backfill(conn):
    """给没有标签的历史事件补打（可重复运行）。"""
    n = 0
    for r in conn.execute(
            "SELECT e.id,e.gist FROM events e "
            "WHERE e.archived=0 AND NOT EXISTS "
            "(SELECT 1 FROM event_tags t WHERE t.event_id=e.id)"):
        ts = tags_for(r["gist"])
        if ts:
            add(conn, r["id"], ts, "backfill")
            n += 1
    conn.commit()
    return n