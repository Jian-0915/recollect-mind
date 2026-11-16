"""实体归一与类型分流（v19）。

三处真实故障逼出这个模块：

1. **非人实体混进人物表**。构建期把「清明」「我们一大家子」「金毛」「医生」
   一视同仁地登记成 personas。后果不止是画像写错：
   ``recall._query_personas`` 一旦命中它们就触发人物硬过滤，实测「谁养了只
   金毛叫布丁」被「金毛」劫持成只剩 1 条候选，正解 0.643 被整条删掉。
   之前只在 config 里挂了个 ``GENERIC_PERSON_NAMES`` 黑名单止血——扩一个词
   才管一个词，永远追不上模型新造出来的名词。

2. **同一个角色裂成多张卡**。「我妈」「妈妈」「母亲」各自 ensure 出一张
   persona，画像、关系、involves 边各记一半，哪张都不完整。这不是模型抽错，
   是写路径缺少归一这一层。

3. **第三方事件被记成「关于对方」**。event.subject 从来不是判定出来的，
   而是 ``DEFAULT_SUBJECT`` 这个默认值——211 条里 187 条 counterpart，其中
   大量是「我外甥上幼儿园」这类第三方事实。subject 错了，透视提示
   （perspective_hint）就会把别人的事说成「你」的事。

``kind`` 的判定一律**保守**：认不出来就是 person。漏判只是少清理几个，
误判会把真人从画像里抹掉——两种错误的代价不对称。
"""

import re

# ---- kind 词表 -------------------------------------------------------------
# 节日/节气/时段：模型常把时间状语当主语抽进 who（"清明回了老家扫墓"）
TIME_WORDS = frozenset("""
清明 中秋 端午 春节 元宵 七夕 重阳 腊八 元旦 国庆 除夕 过年 新年 除夕夜
立春 雨水 惊蛰 春分 清明 谷雨 立夏 小满 芒种 夏至 小暑 大暑 立秋 处暑
白露 秋分 寒露 霜降 立冬 小雪 大雪 冬至 小寒 大寒
周末 假期 寒假 暑假 春节前 年三十 大年初一
""".split())

# 群体/组织：是（或多或少的）一群人，不是一个人
GROUP_WORDS = frozenset("""
我们 咱们 大家 所有人 一家子 一大家子 全家 全家人 家里 家族 家族群
我们组 我们部门 我们团队 我们公司 我们班 我们宿舍 我们单位 组里 部门
团队 小组 项目组 公司 学校 单位 居委会 物业
""".split())
_GROUP_SUFFIX = ("一大家子", "一家子", "家族", "家族群", "们组", "们部门",
                 "们团队", "们公司", "们班", "们宿舍", "们单位", "小组", "团队",
                 "项目组")

# 宠物品种：出现即不是人。宠物**名字**（团子/团子）判不出来，交给别名表
PET_BREEDS = frozenset("""
金毛 金毛犬 拉布拉多 泰迪 柯基 柴犬 哈士奇 边牧 萨摩耶 比熊 博美 雪纳瑞
橘猫 狸花猫 布偶猫 英短 美短 加菲 暹罗 奶牛猫 三花 蓝猫 折耳
猫 猫咪 狗 狗狗 小狗 小猫 兔子 仓鼠 鹦鹉 金鱼 乌龟 宠物
""".split())

# 职业/身份泛称：是"某类人"，不是某个具体的人
OCCUPATION_WORDS = frozenset("""
医生 大夫 护士 老师 律师 司机 教练 警察 保安 快递员 外卖员 服务员 厨师
理发师 房东 中介 邻居 前台 保洁 保姆 月嫂 会计 经理 主管 总监 老板
同事 前同事 同学 大学同学 高中同学 校友 室友 大学室友 舍友 朋友 好朋友
男朋友 女朋友 对象 伴侣 甲方 客户 领导 下属 上司 搭档 合作伙伴
亲戚 家人 家里 长辈 晚辈 陌生人 路人 网友 粉丝 网友
""".split())

# 地点：who 里出现地名/机构名时不该建人物
_PLACE_SUFFIX = ("省", "市", "县", "区", "镇", "村", "路", "街", "大道",
                 "公园", "广场", "医院", "大学", "学院", "中学", "小学",
                 "科技", "公司", "银行", "酒店", "餐厅", "咖啡馆")

_KINDS = ("person", "pet", "group", "time", "place", "other")

# ---- 名称归一 --------------------------------------------------------------
# 「我妈」「妈妈」「母亲」是同一个角色。统一收敛到不带「我」的规范称谓，
# 原名整份保留进 aliases，gist 原文一个字都不动（原文是证据，不能改）。
_PREFIXES = ("我的", "我")

_SYNONYMS = {
    "母亲": "妈妈", "妈咪": "妈妈", "老妈": "妈妈", "娘": "妈妈", "妈": "妈妈",
    "父亲": "爸爸", "老爸": "爸爸", "爹": "爸爸", "爸": "爸爸",
    "妻子": "老婆", "媳妇": "老婆", "太太": "老婆", "夫人": "老婆",
    "丈夫": "老公", "先生": "老公",
    "儿子": "儿子", "闺女": "女儿", "姑娘": "女儿",
    "祖父": "爷爷", "祖母": "奶奶", "外祖父": "外公", "外祖母": "外婆",
    "姥姥": "外婆", "姥爷": "外公",
    "兄长": "哥哥", "兄": "哥哥", "弟弟": "弟弟",
    "姊姊": "姐姐", "姐妹": "姐姐", "妹": "妹妹",
    "舅舅": "舅舅", "姨": "小姨", "姑": "姑姑",
    "表哥": "表哥", "表兄": "表哥", "表姐": "表姐", "表姊": "表姐",
    "堂哥": "堂哥", "堂兄": "堂哥", "堂姐": "堂姐",
    "侄女": "侄女", "外甥": "外甥", "侄子": "侄子", "外甥女": "外甥女",
    "大伯": "大伯", "叔叔": "叔叔", "伯伯": "大伯", "小姨": "小姨",
}

# 归一后仍然是「同一个人」的等价组：任何一员都收敛到组里第一个
_EQUIV_GROUPS = (
    ("妈妈", "母亲", "妈", "妈咪", "老妈", "娘"),
    ("爸爸", "父亲", "爸", "老爸", "爹"),
    ("老婆", "妻子", "媳妇", "太太", "夫人"),
    ("老公", "丈夫", "先生"),
    ("外公", "姥爷", "外祖父"),
    ("外婆", "姥姥", "外祖母"),
    ("哥哥", "兄长", "兄"),
    ("姐姐", "姊姊"),
    ("大伯", "伯伯"),
)
_SYNONYMS_EXTRA = {}
for _g in _EQUIV_GROUPS:
    for _w in _g:
        _SYNONYMS_EXTRA[_w] = _g[0]

_NAME_OK = re.compile(r"^[\u4e00-\u9fa5A-Za-z·\-\s]{1,12}$")

# 「我 + X」里的 X：命中就说明「我」是**领属限定词**（我外甥），不是说话人。
# 用于 gist 改写：只有真正的第一人称才替换成对方名字，否则会写出
# 「你外甥上幼儿园」——把第三方的事算到对方头上。
KIN_WORDS = tuple(sorted({
    "妈", "妈妈", "母亲", "妈咪", "老妈", "爸", "爸爸", "父亲", "老爹",
    "哥", "哥哥", "姐", "姐姐", "弟", "弟弟", "妹", "妹妹",
    "爷", "爷爷", "奶", "奶奶", "外公", "外婆", "姥爷", "姥姥",
    "叔", "叔叔", "婶", "婶婶", "舅", "舅舅", "舅妈", "姨", "小姨", "大姨",
    "姑", "姑姑", "姑妈", "堂哥", "堂姐", "堂弟", "堂妹",
    "表哥", "表姐", "表弟", "表妹", "表兄",
    "外甥", "外甥女", "侄女", "侄子", "大伯", "伯伯", "伯父",
    "公", "婆婆", "公公", "嫂", "嫂子", "弟妹", "姐夫", "妹夫",
    "老公", "老婆", "夫", "妻", "儿子", "女儿", "闺女", "孙子", "孙女",
    "老伴", "亲家", "岳父", "岳母", "丈母娘", "媳妇",
    # 刻意不收「家 / 老家 / 家乡」：「我家猫叫团子」里的「我」就是说话人，
    # 替换成「你家猫」才对；收进来会把说话人自己的事判成第三方。
    "们",
}, key=len, reverse=True))


def kin_follows(after):
    """``after`` 是否以亲属/领属词开头（判断「我」在这里是不是限定词）。"""
    if not after:
        return False
    return any(after.startswith(w) for w in KIN_WORDS)


def kind_of(name, pet_names=None):
    """判定实体类型。返回 person / pet / group / time / place / other。

    ``pet_names``：别名表里已登记为宠物的名字集合（"团子"这种自造名只能靠它）。
    """
    n = str(name or "").strip()
    if not n:
        return "other"
    if n in TIME_WORDS:
        return "time"
    if n in GROUP_WORDS or any(n.endswith(s) for s in _GROUP_SUFFIX):
        return "group"
    if n in PET_BREEDS:
        return "pet"
    if pet_names and n in pet_names:
        return "pet"
    if any(n.endswith(s) for s in _PLACE_SUFFIX):
        return "place"
    if n in OCCUPATION_WORDS:
        return "other"
    return "person"


def canonicalize(name):
    """把同一角色的不同叫法收敛成一个规范名。

    「我堂弟」→「堂弟」，「我妈」→「妈妈」，「父亲」→「爸爸」。
    群体词（我们组/我们一大家子）不剥前缀——剥掉会变成「们组」。
    """
    n = str(name or "").strip()
    if not n or len(n) > 12:
        return n
    if n in GROUP_WORDS or any(n.endswith(s) for s in _GROUP_SUFFIX):
        return n
    for p in _PREFIXES:
        if n.startswith(p) and len(n) - len(p) >= 1:
            rest = n[len(p):]
            # 「我们…」剥完是「们…」，不是名字
            if not rest.startswith("们"):
                n = rest
                break
    if n in _SYNONYMS:
        return _SYNONYMS[n]
    if n in _SYNONYMS_EXTRA:
        return _SYNONYMS_EXTRA[n]
    return n


def is_valid_name(name):
    """名字形态校验：挡住 "用户大学认识" 这类粘连片段和纯符号。"""
    n = str(name or "").strip()
    if not n or not _NAME_OK.match(n):
        return False
    if n.startswith("用户"):
        return False
    return True


def is_person(name, pet_names=None):
    """能不能当作「一个真人」参与画像 / 关系 / 召回硬过滤。"""
    return kind_of(name, pet_names=pet_names) == "person"


# ---- 关系性质：社交对象 vs 仅被提及（v20） --------------------------------
# kind='person' 只回答了「是不是人」，没回答「是不是**我认识的**人」。
# 刘慈欣 / 周杰伦 / 王家卫确实是真人，但他们出现在记忆里的方式是「我看他的
# 电影」，不是「我和他有关系」。混进社交图有两个实际后果：
#   ① 问「我喜欢看什么电影」时「王家卫」命中人物 → 触发召回的人物硬过滤，
#      把候选换成字面沾边的那几条（和 v19 修「金毛」是同一类故障）；
#   ② 画像队列可能给他写「刘慈欣最近迷科幻小说」。
# 判定仍然保守：**只有全部证据都是「消费其作品」框架时才降级为 mentioned**，
# 任一条出现社交用语就算 social。误降级会把真人从画像里抹掉，代价不对称。
_SOCIAL_MARK = (
    "我的", "我叫", "叫", "认识", "一块", "一起", "给我", "对我", "带我",
    "和我", "跟我", "陪我", "让我", "帮", "视频", "微信", "电话", "聊天", "见面",
    "朋友", "同事", "同学", "室友", "闺蜜", "恋人", "男友", "女友", "对象",
    "结婚", "婚礼", "生日", "聚", "吃饭", "吵架", "来家", "寄", "送",
)
# 刻意不收裸「说」：「科幻小说」里的"说"会把刘慈欣判成社交对象，
# 而「我表哥说加班比我还狠」里的"说"已经被"我表哥"这类领属覆盖。
_MEDIA_NOUN = (
    "电影", "电视剧", "剧集", "综艺", "歌", "歌单", "专辑", "音乐", "演唱会",
    "小说", "书", "作品", "漫画", "动画", "动漫", "节目", "访谈", "演讲",
    "球星", "球队", "比赛", "直播",
)
_MEDIA_VERB = (
    "看", "听", "读", "追", "喜欢", "迷", "爱", "收藏", "粉", "崇拜", "循环",
    "刷", "翻", "买票", "单曲",
)


def looks_like_mention(gist):
    """这句话是否只是「消费某人的作品」（而非与某人有交往）。"""
    g = str(gist or "")
    if not g:
        return False
    return (any(n in g for n in _MEDIA_NOUN)
            and any(v in g for v in _MEDIA_VERB))


def has_social_mark(gist):
    g = str(gist or "")
    return any(m in g for m in _SOCIAL_MARK)


def bond_of(gists, has_relation=False):
    """``social``（我认识的人）或 ``mentioned``（只在作品语境里出现的人）。

    ``has_relation``：relations 表里有边 → 直接 social，不再看文本。
    """
    if has_relation:
        return "social"
    texts = [g for g in (gists or []) if g]
    if not texts:
        return "social"
    if any(has_social_mark(g) for g in texts):
        return "social"
    if all(looks_like_mention(g) for g in texts):
        return "mentioned"
    return "social"
