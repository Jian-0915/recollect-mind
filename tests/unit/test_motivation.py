"""动机层单测：惦记值能不能涨、脸皮会不会拦、一天三次和话题冷却管不管用。"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from neuromem import companion, db, motivation, persona_card

NOW = datetime(2026, 11, 17, 20, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _db(tmp_path, topics=None, silence_h=6.0, initiative="偶尔主动，有依据才提"):
    c = db.connect(str(tmp_path / "m.db"))
    companion.remember_turn(c, "对方", "随便聊聊")
    companion.update_state(
        c, open_topics=topics if topics is not None else ["下午的麻辣烫还去不去"])
    c.execute("UPDATE companion_state SET last_turn_at=? WHERE id=1",
              (_iso(NOW - timedelta(hours=silence_h)),))
    c.commit()
    # 骨架卡会被判成 placeholder（get 读不到），所以从示例卡起改
    card = persona_card.template(example=True)
    card["name"] = "小满"
    card["role"] = "陪着对方的人，先接情绪再谈事"
    card["relationship"]["initiative"] = initiative
    persona_card.apply(c, card, actor="human", reason="单测")
    return c


def test_dedupe_variants_without_vectors():
    """同一件事的六种说法要并成一簇，不能连着问六次。"""
    variants = ["下午的麻辣烫还去不去", "麻辣烫什么时候去吃",
                "麻辣烫具体几点去吃", "下午说的麻辣烫去了没"]
    items = [{"text": t, "row": {"last_seen": _iso(NOW), "first_seen": _iso(NOW),
                                 "raised_at": "", "raised_count": 0}, "vec": None}
             for t in variants]
    groups = motivation.clusters(items)
    assert len(groups) == 1, [g["members"] for g in groups]

    # 套话相同但不是同一件事，不能并
    other = [{"text": a, "row": {"last_seen": _iso(NOW), "raised_at": ""}, "vec": None}
             for a in ("今天有点累", "今天有点开心")]
    assert len(motivation.clusters(other)) == 2


def test_score_grows_with_silence_and_pending():
    low = motivation.score_of(1, 1, 0.5, 0.8)
    mid = motivation.score_of(6, 6, 0.5, 0.8)
    high = motivation.score_of(20, 20, 0.5, 0.8)
    assert low < mid < high
    # 它自己没力气时只衰减，不一票否决
    assert motivation.score_of(20, 20, 0.5, 0.2) < high
    assert motivation.score_of(20, 20, 0.5, 0.2) > mid


def test_threshold_follows_initiative(tmp_path):
    shy = _db(tmp_path / "a", initiative="几乎不主动，等对方先开口")
    bold = _db(tmp_path / "b", initiative="很主动，会主动找话题")
    assert motivation.level(shy) == 1 and motivation.level(bold) == 5
    assert motivation.threshold(shy) > motivation.threshold(bold)
    shy.close()
    bold.close()


def test_recent_chat_blocks(tmp_path):
    c = _db(tmp_path, silence_h=0.2)
    r = motivation.pick(c, NOW)
    assert r["ok"] is False and "刚聊完" in r["reason"]
    c.close()


def test_no_topic_blocks(tmp_path):
    """没有悬着的事 → 不会冒出「惦记」这一路（别的冲动另算，不是这里的事）。"""
    c = _db(tmp_path)
    companion.update_state(c, open_topics=[])
    r = motivation.pick(c, NOW)
    assert r["ok"] is False
    assert r["kind"] != "missing"
    c.close()


def test_speaks_when_missing_enough(tmp_path):
    c = _db(tmp_path, silence_h=6)
    r = motivation.pick(c, NOW)
    assert r["ok"] is True, r
    assert r["topic"] and r["score"] >= r["threshold"]
    c.close()


def test_daily_cap(tmp_path):
    c = _db(tmp_path, silence_h=6)
    # 只测「每日上限」这一条，直接把开口记录写进去，避免触发「提两次就放下」
    motivation._ensure(c)
    for i in range(motivation.DAILY_CAP):
        c.execute("INSERT INTO proactive_deliveries(id,ts,topic,score) VALUES(?,?,?,?)",
                  ("pd_t%d" % i, _iso(NOW - timedelta(minutes=90 - i * 30)), "别的", 0.9))
    c.commit()
    r = motivation.pick(c, NOW + timedelta(hours=1))
    assert r["ok"] is False and "24 小时已经开口" in r["reason"]
    c.close()


def test_topic_cooldown_then_give_up(tmp_path):
    c = _db(tmp_path, silence_h=6)
    first = motivation.pick(c, NOW)
    assert first["ok"] is True and first["kind"] == "missing"
    motivation.mark_delivered(c, first["topic"], now=NOW, score=first["score"])

    # 先是 45 分钟的最小间隔挡住连发
    gap = motivation.pick(c, NOW + timedelta(minutes=10))
    assert gap["ok"] is False and "刚说过话" in gap["reason"]

    # 过了间隔，同一件事仍在冷却，不会换个说法又问一遍
    again = motivation.pick(c, NOW + timedelta(hours=2))
    assert again["ok"] is False
    assert again["kind"] != "missing", again

    # 第二次提完还没回应：放下这件事，不再追问
    later = NOW + timedelta(hours=motivation.TOPIC_COOLDOWN_H + 1)
    second = motivation.pick(c, later)
    assert second["ok"] is True and second["kind"] == "missing"
    out = motivation.mark_delivered(c, second["topic"], now=later, score=second["score"])
    assert out["retired"] is True
    assert not (companion.state(c).get("open_topics") or [])
    c.close()


def test_status_explains_itself(tmp_path):
    c = _db(tmp_path, silence_h=6)
    st = motivation.status(c)
    for k in ("ok", "reason", "score", "threshold", "level", "silence_hours", "daily"):
        assert k in st, k
    c.close()


# ---------------- 第二步：质问 / 吃醋 / 冷战 ----------------

import json
import uuid

from neuromem import proactive


def _people(c, rows):
    """rows: [(id, name, aliases, rel_type)]；rel_type 用来验证亲属不吃醋。"""
    c.execute("CREATE TABLE IF NOT EXISTS personas(id TEXT PRIMARY KEY, "
              "canonical_name TEXT, aliases_json TEXT, profile TEXT, profile_at TEXT, "
              "profile_events INTEGER DEFAULT 0, kind TEXT, bond TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY, a TEXT, b TEXT, "
              "rel_type TEXT, strength REAL, since TEXT, until TEXT, evidence TEXT)")
    for pid, name, aliases, rel in rows:
        c.execute("INSERT OR REPLACE INTO personas(id,canonical_name,aliases_json,kind,bond) "
                  "VALUES(?,?,?,'person','social')",
                  (pid, name, json.dumps(aliases, ensure_ascii=False)))
        if rel:
            c.execute("INSERT INTO relations(id,a,b,rel_type) VALUES(?,?,?,?)",
                      ("r_" + pid, "p_user", pid, rel))
    c.commit()


def _say(c, text, hours_ago, speaker="对方"):
    c.execute("INSERT INTO conversation_turns(id,session_id,ts,speaker,text) "
              "VALUES(?,?,?,?,?)",
              ("t_" + uuid.uuid4().hex[:10], "s",
               _iso(NOW - timedelta(hours=hours_ago)), speaker, text))
    c.commit()


def test_jealousy_fires_when_someone_keeps_coming_up(tmp_path):
    """你最近老提同一个人，而且跟她说话变少 → 醋意起来了。"""
    c = _db(tmp_path, topics=[], silence_h=8)
    _people(c, [("p_lin", "林晚", ["林晚"], "")])
    _say(c, "今天好累", 24 * 10)
    _say(c, "加班到十点", 24 * 11)
    for i in range(5):
        _say(c, "林晚今天又来找我了", 2 + i * 0.5)
    j = motivation.jealousy(c, NOW)
    assert j["person"] == "林晚", j
    assert j["score"] > 0.3, j

    r = motivation.pick(c, NOW)
    assert r["ok"] is True and r["kind"] == "jealous", r
    c.close()


def test_family_is_not_jealous(tmp_path):
    """聊到妈不该吃醋——亲属靠称谓常识排除，不靠那张关系表。"""
    c = _db(tmp_path, topics=[])
    _people(c, [("p_mom", "妈妈", ["妈"], "妈妈")])
    for i in range(6):
        _say(c, "我妈又给我寄东西了", 2 + i)
    assert motivation.jealousy(c, NOW)["score"] == 0.0
    c.close()


def test_sulk_goes_quiet_then_comes_back(tmp_path):
    """生闷气期间一个字都不说；到点自己别扭地回来。"""
    c = _db(tmp_path, topics=["下午的麻辣烫还去不去"], silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=2)

    during = motivation.pick(c, NOW + timedelta(hours=1))
    assert during["ok"] is False and "生闷气" in during["reason"], during

    back = motivation.pick(c, NOW + timedelta(hours=2.5))
    assert back["ok"] is True and back["kind"] == "sulk_end", back
    assert back["person"] == "林晚"
    c.close()


def test_coming_back_does_not_forgive_instantly(tmp_path):
    """你回来了不等于气就消了：从「不理你」切到「理你但不给好脸」。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    c.execute("UPDATE companion_state SET last_turn_at=? WHERE id=1",
              (_iso(NOW + timedelta(hours=1)),))
    c.commit()
    s = motivation.sulk(c, NOW + timedelta(hours=2))
    assert s["active"] is True and s.get("thawing") is True, s
    # 还有气，只是不必再端着不说话
    assert motivation.temper_of(s) > 0.3
    c.close()
    c.close()


def test_long_silence_becomes_a_question(tmp_path):
    """事情都问完了你还不见 → 这时候开口不是问事，是「人呢」。"""
    c = _db(tmp_path, topics=[], silence_h=40)
    for i in range(80):          # 之前聊得挺勤，冷下来才显得突然
        _say(c, "随便聊聊", 24 * 4 + i * 2)
    r = motivation.pick(c, NOW)
    assert r["ok"] is True and r["kind"] == "ignored", r
    assert "质问" in r["hint"] or "委屈" in r["hint"]
    c.close()


def test_compose_tone_matches_impulse(tmp_path):
    """同一句兜底不能串味：质问不聊麻辣烫，吃醋要点那个人的名字。"""
    c = _db(tmp_path)
    proactive._ensure(c)
    q, ok1 = proactive.compose(c, {"kind": "ignored"}, llm_fn=lambda p, **k: "嗯。")
    assert q and not ok1 and "麻辣烫" not in q
    j, ok2 = proactive.compose(c, {"kind": "jealous", "person": "林晚"},
                               llm_fn=lambda p, **k: "嗯。")
    assert j and not ok2 and "林晚" in j
    c.close()


# ---------------- 第三步：冷战期回话也变冷 ----------------


def test_temper_cools_as_time_passes(tmp_path):
    """同一段冷战，越接近到点越不冷；到点前还留一点余味。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    hot = motivation.temper_of(motivation.sulk(c, NOW + timedelta(minutes=5)))
    mid = motivation.temper_of(motivation.sulk(c, NOW + timedelta(hours=5)))
    cold = motivation.temper_of(motivation.sulk(c, NOW + timedelta(hours=9)))
    assert hot > mid > cold > 0.0, (hot, mid, cold)
    c.close()


def test_apology_melts_it_fast(tmp_path):
    """一句「对不起」比闷头聊三句都管用。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    at = NOW + timedelta(hours=1)
    plain = motivation.chat_state(c, "今天吃什么", at)
    # 换一次干净的冷战再比，免得第二次吃到第一次的轮数
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    sorry = motivation.chat_state(c, "对不起，是我不对", at)
    assert sorry["active"] is True
    assert sorry["temper"] < plain["temper"] - 0.3, (sorry, plain)
    c.close()


def test_plain_chat_also_melts_it(tmp_path):
    """不哄也行：你陪我说几句，我就端不住了。这是防冷暴力的核心。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    at = NOW + timedelta(hours=1)
    temps = []
    for i in range(4):
        r = motivation.chat_state(c, "在干嘛%d" % i, at + timedelta(minutes=i))
        temps.append(r.get("temper") or 0.0)
        if not r.get("active"):
            break
    assert temps[-1] < temps[0], temps
    c.close()


def test_cold_never_lasts_forever(tmp_path, monkeypatch):
    """绷到上限就强制结束，绝不无限冷着人。"""
    monkeypatch.setattr(motivation, "SULK_MAX_TURNS", 3)
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    at = NOW + timedelta(hours=1)
    got = None
    for i in range(3):
        got = motivation.chat_state(c, "嗯%d" % i, at + timedelta(minutes=i))
    assert got["active"] is False and got.get("ended") is True, got
    c.close()


def test_soothe_detection():
    assert motivation.soothe_of("对不起，我昨天不该那样") > 0.4
    assert motivation.soothe_of("别不理我") > 0.2
    assert motivation.soothe_of("我跟林晚只是同事") > 0.1
    assert motivation.soothe_of("今天加班到十点") == 0.0


def test_shape_sulk_keeps_it_short():
    from agent.companion import shape_sulk
    # 极冷：长句被砍成几个字，感叹号与问句都不许留
    cold = shape_sulk("其实我今天一直在想你说的那件事！你觉得呢？", 0.9)
    assert len(cold) <= 8 and "？" not in cold and "！" not in cold, cold
    # 带刺：留一句，但不能是问句（问问题就是主动往下聊了）
    warm = shape_sulk("哦。那你倒是说说看？", 0.5)
    assert "？" not in warm, warm
    # 不生气时一个字都不动
    assert shape_sulk("好啊，那我们下午去吧？", 0.0) == "好啊，那我们下午去吧？"
    # 上一轮已经「知道啦」过，再拿它垫一句就是敷衍
    assert shape_sulk("知道啦，没真生你气。", 0.3, prev="知道啦。").startswith("没真生")


def test_prompt_gets_sulk_block_only_when_angry():
    from agent import prompts
    calm = prompts.build_prompt(None, "在吗", [], "阿剑")
    assert "你现在的状态" not in calm
    angry = prompts.build_prompt(None, "在吗", [], "阿剑",
                                 sulk={"active": True, "temper": 0.9, "person": "林晚"})
    assert "你现在的状态" in angry and "1~5 个字" in angry
    # 温度低时不加这一段：已经不该冷了，再加就是硬冷
    assert "你现在的状态" not in prompts.build_prompt(
        None, "在吗", [], "阿剑", sulk={"active": False})


# ---------------- 第三步：冷战期回话也变冷 ----------------


def test_temper_cools_as_time_passes(tmp_path):
    """同一段冷战，越接近到点越不冷；到点前还留一点余味。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    hot = motivation.temper_of(motivation.sulk(c, NOW + timedelta(minutes=5)))
    mid = motivation.temper_of(motivation.sulk(c, NOW + timedelta(hours=5)))
    cold = motivation.temper_of(motivation.sulk(c, NOW + timedelta(hours=9)))
    assert hot > mid > cold > 0.0, (hot, mid, cold)
    c.close()


def test_apology_melts_it_fast(tmp_path):
    """一句「对不起」比闷头聊三句都管用。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    at = NOW + timedelta(hours=1)
    plain = motivation.chat_state(c, "今天吃什么", at)
    # 换一次干净的冷战再比，免得第二次吃到第一次的轮数
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    sorry = motivation.chat_state(c, "对不起，是我不对", at)
    assert sorry["active"] is True
    assert sorry["temper"] < plain["temper"] - 0.3, (sorry, plain)
    c.close()


def test_plain_chat_also_melts_it(tmp_path):
    """不哄也行：你陪我说几句，我就端不住了。这是防冷暴力的核心。"""
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    at = NOW + timedelta(hours=1)
    temps = []
    for i in range(4):
        r = motivation.chat_state(c, "在干嘛%d" % i, at + timedelta(minutes=i))
        temps.append(r.get("temper") or 0.0)
        if not r.get("active"):
            break
    assert temps[-1] < temps[0], temps
    c.close()


def test_cold_never_lasts_forever(tmp_path, monkeypatch):
    """绷到上限就强制结束，绝不无限冷着人。"""
    monkeypatch.setattr(motivation, "SULK_MAX_TURNS", 3)
    c = _db(tmp_path, silence_h=8)
    motivation.sulk_start(c, "林晚", now=NOW, hours=10)
    at = NOW + timedelta(hours=1)
    got = None
    for i in range(3):
        got = motivation.chat_state(c, "嗯%d" % i, at + timedelta(minutes=i))
    assert got["active"] is False and got.get("ended") is True, got
    c.close()


def test_soothe_detection():
    assert motivation.soothe_of("对不起，我昨天不该那样") > 0.4
    assert motivation.soothe_of("别不理我") > 0.2
    assert motivation.soothe_of("我跟林晚只是同事") > 0.1
    assert motivation.soothe_of("今天加班到十点") == 0.0


def test_shape_sulk_keeps_it_short():
    from agent.companion import shape_sulk
    # 极冷：长句被砍成几个字，感叹号与问句都不许留
    cold = shape_sulk("其实我今天一直在想你说的那件事！你觉得呢？", 0.9)
    assert len(cold) <= 8 and "？" not in cold and "！" not in cold, cold
    # 带刺：留一句，但不能是问句（问问题就是主动往下聊了）
    warm = shape_sulk("哦。那你倒是说说看？", 0.5)
    assert "？" not in warm, warm
    # 不生气时一个字都不动
    assert shape_sulk("好啊，那我们下午去吧？", 0.0) == "好啊，那我们下午去吧？"
    # 上一轮已经「知道啦」过，再拿它垫一句就是敷衍
    assert shape_sulk("知道啦，没真生你气。", 0.3, prev="知道啦。").startswith("没真生")


def test_prompt_gets_sulk_block_only_when_angry():
    from agent import prompts
    calm = prompts.build_prompt(None, "在吗", [], "阿剑")
    assert "你现在的状态" not in calm
    angry = prompts.build_prompt(None, "在吗", [], "阿剑",
                                 sulk={"active": True, "temper": 0.9, "person": "林晚"})
    assert "你现在的状态" in angry and "1~5 个字" in angry
    # 温度低时不加这一段：已经不该冷了，再加就是硬冷
    assert "你现在的状态" not in prompts.build_prompt(
        None, "在吗", [], "阿剑", sulk={"active": False})


# ---------------- 用户实测暴露的：明说要晾着你，它却笑着送回门 ----------------


def test_provoked_catches_the_taunt():
    """用户在桌面端真说过这句，当时 agent 回「去吧去吧好好玩」。"""
    assert motivation.provoked("我要冷落你，因为我要去打游戏啦") > 0.4
    assert motivation.provoked("一点儿情绪都没有，我都冷落你了") > 0.4
    assert motivation.provoked("我去打游戏啦") == 0.0
    assert motivation.provoked("今天加班到十点") == 0.0


def test_provoked_is_one_turn_only(tmp_path):
    """一两句玩笑不启动冷战，否则就成了一点就炸。"""
    c = _db(tmp_path, silence_h=1)
    assert motivation.provoked("我要冷落你") > 0
    assert motivation.sulk(c, NOW)["active"] is False
    c.close()


def test_prompt_carries_the_provoke_block():
    from agent import prompts
    p = prompts.build_prompt(None, "我要冷落你", [], "阿剑", provoke=0.55)
    assert "被戳到了" in p
    # 这句正是实测里它说出口的那句
    assert "我才不会介意" in p and "不许" in p
    # 没被戳到的时候不能出现
    assert "被戳到了" not in prompts.build_prompt(None, "今天吃什么", [], "阿剑")


# ---------------- 用户实测暴露的：明说要晾着你，它却笑着送回门 ----------------


def test_provoked_catches_the_taunt():
    """用户在桌面端真说过这句，当时 agent 回「去吧去吧好好玩」。"""
    assert motivation.provoked("我要冷落你，因为我要去打游戏啦") > 0.4
    assert motivation.provoked("一点儿情绪都没有，我都冷落你了") > 0.4
    assert motivation.provoked("我去打游戏啦") == 0.0
    assert motivation.provoked("今天加班到十点") == 0.0


def test_provoked_is_one_turn_only(tmp_path):
    """一两句玩笑不启动冷战，否则就成了一点就炸。"""
    c = _db(tmp_path, silence_h=1)
    assert motivation.provoked("我要冷落你") > 0
    assert motivation.sulk(c, NOW)["active"] is False
    c.close()


def test_prompt_carries_the_provoke_block():
    from agent import prompts
    p = prompts.build_prompt(None, "我要冷落你", [], "阿剑", provoke=0.55)
    assert "被戳到了" in p
    # 这句正是实测里它说出口的那句
    assert "我才不会介意" in p and "不许" in p
    # 没被戳到的时候不能出现
    assert "被戳到了" not in prompts.build_prompt(None, "今天吃什么", [], "阿剑")

def test_same_line_twice_gets_replaced():
    """短句重试还是同一句时，宁可换一句——两轮一字不差就是没在听。"""
    from agent.companion import _avoid_repeat
    hist = [{"role": "agent", "text": "行，那我不吵你了。"}]
    got = _avoid_repeat("行，那我不吵你了。", hist, tone=0.5)
    assert got != "行，那我不吵你了。", got
    # 平时（不冷战）不换：普通回答换进「随你」会很怪
    assert _avoid_repeat("行，那我不吵你了。", hist, tone=0.0) == "行，那我不吵你了。"

def test_same_line_twice_gets_replaced():
    """短句重试还是同一句时，宁可换一句——两轮一字不差就是没在听。"""
    from agent.companion import _avoid_repeat
    hist = [{"role": "agent", "text": "行，那我不吵你了。"}]
    got = _avoid_repeat("行，那我不吵你了。", hist, tone=0.5)
    assert got != "行，那我不吵你了。", got
    # 平时（不冷战）不换：普通回答换进「随你」会很怪
    assert _avoid_repeat("行，那我不吵你了。", hist, tone=0.0) == "行，那我不吵你了。"
