"""产品记忆边界：核心字段自动保存，其他事实必须主动授权。

字段定义决定标准名称、分类和标识；不是通过聊天关键词创造事实。
模型只选择字段并解释原话，版本、来源、数值和独立审查仍由原流程负责。
"""
import re
from .facts import decode_fact, digest, normalize, numeric_mentions, numeric_quantity, predicate_id, fact_key, query_terms

# label / category / definition / 是否按主题独立保存（避免多个兴趣互相覆盖）
CORE_FIELDS = {
    "name": ("称呼", "profile", "用户明确提供的姓名或希望使用的称呼", False),
    "age": ("年龄", "profile", "用户明确自述的当前年龄，附陈述日期，不推生日", False),
    "student_status": ("学籍", "profile", "用户明确自述的当前学生身份，如大学生", False),
    "grade": ("年级", "profile", "用户明确自述的当前年级，不按时间自行升级", False),
    "occupation": ("职业", "profile", "用户明确自述的当前职业，不把大学生当职业", False),
    "interest": ("兴趣", "preference", "明确的长期兴趣；每个兴趣独立保存", True),
    "like": ("喜好", "preference", "明确喜欢的事物，不从一次购买或行为推断", True),
    "dislike": ("不喜欢", "preference", "明确不喜欢的事物，不从一次行为推断", True),
    "communication": ("交流偏好", "preference", "用户希望机器人怎样交流；按方面保存", True),
    "relationship": ("重要关系", "relationship", "明确的重要人物及关系基本信息，不保存数量/日常琐事", True),
    "pet_kind": ("饲养宠物", "relationship", "用户当前养的宠物种类，只保存种类，不保存数量、年龄或购买细节", True),
    "goal": ("长期目标", "goal", "用户当前明确正在追求或已决定投入的持续数周以上目标；保留原话期限，不视为已经完成，不含临时愿望、假设和日程指令", True),
    "habit": ("稳定习惯", "habit", "用户明确陈述的当前反复发生的稳定行为及频率，不从一次行为推断，不把希望养成的习惯当成已存在", True),
    "pet_name": ("名字", "relationship", "用户当前真实饲养的具名宠物的名字，不含假设、未来取名或别人的宠物；同名不同宠物无法区分时不保存", False),
    "pet_species": ("种类", "relationship", "这只具名宠物明确陈述的种类，不从名字或常识推断；证据必须同时明确关联名字和种类", False),
    "pet_relation": ("与用户关系", "relationship", "这只具名宠物与用户明确的当前饲养关系，不把朋友的宠物或想养的宠物归给用户", False),
}

PET_FIELDS = {"pet_name", "pet_species", "pet_relation"}
EXPANDED_POLICY = """
新增自动边界必须基于语义，不靠关键词判定：先判断否定、假设、转述、归属和长期性。
goal表示当前确实持有的长期目标。值包含目标行动和原话明确期限，topic是连续引用的目标主题，如六级、嵌入式实习。
goal的temporalScope=current表示现在有这个目标，并非目标已实现；半年内/今年等期限保留原话，observedAt提供陈述日期，不编造目标完成日期。
habit的value包含行为和稳定频率，topic是原话中的行为主题。每天/经常只是线索，不能忽略否定和假设。
“我准备半年内考过六级”可记goal；“我每天晚上跑步”可记habit。
“我今天晚上去跑步”“我等下想打游戏”“如果能坚持我就每天跑步”“我希望以后每天跑步”不记为现有habit。
“如果以后我养猫，我想叫它豆包”不记任何当前宠物；“朋友的猫叫豆包”不能记为用户宠物。
具名宠物使用pet_name/pet_species/pet_relation，每项额外给entityName，连续复制该宠物名字；不能用“用户的猫”“它”或种类作为名字。
pet_name的value等于entityName；pet_relation的value固定为“用户饲养的宠物”。pet_species只填种类，不含数量。
“我养了一只猫叫豆包”可提取三个原子属性，共用entityName=豆包，evidence引用完整关联原句。
“我有两只猫，一只叫豆包，一只叫雪球”必须分别用豆包和雪球，最多六项；每项可用完整原句evidence建立归属，不可交换属性。
具名宠物已提取时不重复保存pet_kind；没有名字时仍可沿用pet_kind只记种类。
这批不猜别名合并、改名、同名宠物区分或代词指向；entityName必须在本轮证据出现，无法明确关联时跳过。年龄/体重等新属性暂不自动开放。
value的实体归属、频率、否定方向、期限均须证据支持；助手回答、上下文摘要不能当证据。
"""

CONTROLLED_PROMPT = """你是个人陪伴机器人的受控记忆提取器，只理解latestUser中值得记的明确事实。
默认只选coreFields列出的字段；不得自由创造字段或把全部聊天内容都记下来。
身份和年龄分别提取：大学生用student_status，年龄用age，不能混合成一项，也不从年龄推身份。
喜好必须明确表达，不从一次点餐、购物、行为推断长期偏好。只提取当前核心信息，不把过去状态升级为当前。
没有主动授权或已授权旧事实时，pet_kind仅保存宠物种类，例如“我有两只猫”保存value=猫、topic=猫，不保存两只。
relationship只记重要人物的基本关系，不借此保存物品数量、购买来源或零碎经历。
短期目标、旅行经历、即时安排、持有数量、购买细节等默认不保存，不换个字段绕过边界。
用户主动请求记住的非核心内容才用custom，explicitRequestAllowed由程序提供，模型不能自行批准。
existingExplicit仅列出之前主动记住的事实；本轮明确更新该事实时可用custom并选择对应targetId，不新增未授权事实。
优先级：先维护existingExplicit中本轮明确改变的事实，再提取authorizedSegments里主动要求保存的完整长期事实，最后提取剩余核心信息。
主动记忆中的数量不能退化成宠物种类。与custom同一原话且完全被它包含的core可以省略，不能用core代替custom。
例如授权片段“请记住我有两只猫”→field=custom,subject=用户的猫,predicate=数量,value=两只,category=relationship,intent=assert。
例如existingExplicit中id=11是猫数量两只，本轮“我又养了一只猫”→field=custom,targetId=11,subject=用户的猫,predicate=数量,value=一只,delta=1,intent=relative,category=relationship。
userContext只解代词，角色背景、助手回复、示例和旧用户话不是本轮新值来源。
输出严格JSON {"facts":[]}，最多6项。核心项：
{"field":"coreFields里的代码","value":"单个属性的值","sourceId":0,"intent":"assert","temporalScope":"current"}。
按主题保存的字段另给topic，topic必须连续出现在本轮原文引用中。pet_kind的value与topic都只给种类。
intent核心只用assert/change/corrected/cancelled/invalidated；不再喜欢或不再养某种宠物用cancelled，值/topic仍填原对象。
引用sourceSegments的整数sourceId，由程序取回原文；也可给连续复制的evidence，不得改写。
custom额外必填subject,predicate,category,intent,temporalScope；更新主动记忆另给targetId。
custom历史/未来须timePrecision/timeValue，相对数量须intent=relative及有符号delta，不能把变化量当最终值。
明确用户“请记住”不授权保存密码、令牌等敏感信息，也不代表短期安排有长期价值。
例如“我是大学生，今年21岁” → 两项field=student_status/value=大学生、field=age/value=21岁。
“我喜欢摄影和游泳” → 两项field=interest，分别value/topic=摄影和游泳。
兴趣与喜欢都选interest，topic只填事物本身；明确不喜欢选dislike。程序按同主题统一喜好槽，不自行猜测同义词。
用户说“不再喜欢”是cancelled，仍选interest；“现在讨厌/不喜欢”表达明确负向态度时选dislike/assert。
custom新增的sourceId必须指向authorizedSegments对应分句，不把“请记住”的授权扩大到整轮其他分句。
existingExplicit未找到可靠目标时不要猜targetId，不能把未授权新事实包装为旧事实更新。
没有符合边界的信息输出空facts。"""

FOCUSED_PROMPT = """你是个人机器人的记忆提取器。按本轮指定模式提取，不推测事实；输出严格JSON {"facts":[]}，最多6项。
事实证据只来自latestUser。userContext和existingExplicit只解释对象，不能提供本轮新值。每项必须有sourceId（sourceSegments里的整数ID），不要自行改写证据。
输出格式：
核心资料：{"field":"coreFields里的代码","value":"单一值","sourceId":0,"intent":"assert","temporalScope":"current"}。多主题字段必须topic，连续复制原话。
主动事实：{"field":"custom","subject":"具体对象","predicate":"具体属性","value":"单一值","category":"profile/preference/relationship/event/goal/habit/note","sourceId":0,"intent":"assert","temporalScope":"current"}。
更新主动事实必须targetId=existingExplicit中的真实整数ID；相对变化用intent=relative、delta=带正负号的数字、value=变化量（不是最终值）。
完成目标用intent=completed、temporalScope=historical；取消目标用intent=cancelled。历史/未来须timePrecision和timeValue，依referenceTime固定日历，无确切日期用approximate/未注明日期的过往或未定日期。
密码、令牌、短期安排不保存。只有本轮明确表达才能维护；买过不代表喜欢，计划不代表爱好，数量不能塞进relationship。
兴趣/喜欢统一选interest，明确不喜欢选dislike，不再喜欢选interest/cancelled；topic只填主题。
"""


def extraction_prompt(requested, opted):
    from .categories import CATEGORY_PROMPT
    return CATEGORY_PROMPT + ("\n本轮存在主动保存授权，范围仅限authorizedSegments。" if requested else "\n本轮没有新增主动授权；custom只能维护existingExplicit中的已授权记录。")


def refusal_request(text):
    """高频拒记指令保守阻断本轮所有写入，不将拒记解释成删除旧记录。"""
    unquoted=re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"', '', text)
    return bool(re.search(r'(?:不要|别|不用|不许|不需要|不希望你)(?:再|帮我|给我|把.{0,40}?)?(?:记住|记下|记着|保存|记录)|(?:这次|这条|这段|刚才.{0,12})(?:不记|不保存|不记录)', unquoted))


def authorized_segments(text):
    if refusal_request(text): return []
    spans=[]
    for clause in re.split(r"[，,。；;！？!?\n]", text):
        clause=clause.strip()
        if any(mark in clause for mark in ('“','”','"','「','」')): continue
        if re.match(r"^(?:请(?:你)?|帮我|替我|麻烦(?:你)?|给我|我希望你|我想让你)?(?:记住|记下|记一下|记着)",clause):
            if re.search(r"(?:了吗|了没|没有|什么|哪些|能否|是否|吗)$",clause): continue
            spans.append(clause)
    return spans


def explicit_request(text):
    """识别主动记忆指令，不把否定、查询或引述当授权。"""
    return bool(authorized_segments(text))


def authorized_source(raw, text):
    """新增按需事实的证据必须属于一个授权分句，整句引用只能确定性收窄。"""
    quote=raw.get("evidence",raw.get("sourceEvidence",""))
    matches=[span for span in authorized_segments(text) if quote and
             (quote in span or span in quote and isinstance(raw.get("value"),str) and raw["value"] in span)]
    if len(matches)!=1: raise ValueError("custom_evidence_outside_authorization")
    return {**raw,"evidence":matches[0]}


def resolution_keys(sources):
    """固定字段和同主题偏好的兼容标识；不扫描全部记忆或迁移旧记录。"""
    keys=[]
    for source in sources:
        f=source["fact"]; keys.append(fact_key(f))
        if f.get("memoryMode")!="core": continue
        field=f["memoryField"]
        if field in {"interest","like","dislike"}:
            for alias in ("interest","like","dislike"):
                code="core_"+alias+"_"+digest(normalize(source["preferenceTopic"]))[:16]
                keys.append(fact_key({**f,"predicateId":"p_"+digest(code)[:40]}))
        elif field not in PET_FIELDS and not CORE_FIELDS[field][3]:
            keys.append(fact_key({**f,"predicateId":"p_"+digest(normalize(f["predicate"]))[:40]}))
    return list(dict.fromkeys(keys))


def retrieval_terms(text, context=()):
    # 给本轮尾部对象留出预算；上下文只用于召回，不作为新事实的值。
    terms=query_terms(text)
    tail=query_terms(text[-30:])
    recent=query_terms(context[-1]["text"]) if context else []
    return list(dict.fromkeys(terms[:5]+tail[-4:]+recent[-3:]))[:12]


def field_catalog():
    return [{"field":key,"definition":item[2],"topicRequired":item[3],"entityNameRequired":key in PET_FIELDS} for key,item in CORE_FIELDS.items()]


def core_source(provider, raw, base, observed, memories):
    field=raw.get("field")
    if field not in CORE_FIELDS: raise ValueError("automatic_field_not_allowed")
    if field in {"student_status","occupation"} and normalize(raw.get("value","")) in {"左撇子","右撇子","左手","右手","惯用左手","惯用右手"}:
        raise ValueError("handedness_is_not_student_status_or_occupation")
    label,category,definition,multi=CORE_FIELDS[field]
    if raw.get("temporalScope","current")!="current": raise ValueError("automatic_history_not_allowed")
    if raw.get("intent","assert") not in {"assert","change","corrected","cancelled","invalidated"}:
        raise ValueError("automatic_transition_not_allowed")
    topic=raw.get("topic",raw.get("value")) if multi else ""
    quote=raw.get("evidence",raw.get("sourceEvidence",""))
    pet_name = None
    if field in PET_FIELDS:
        pet_name=raw.get("entityName")
        if not isinstance(pet_name,str) or not normalize(pet_name) or len(pet_name)>60 or pet_name not in quote:
            raise ValueError("pet_name_not_in_current_evidence")
        if normalize(pet_name) in {"它","他","她","猫","狗","宠物","用户的猫","用户的狗","用户的宠物"}:
            raise ValueError("pet_requires_distinct_name")
        if field=="pet_name" and raw.get("value")!=pet_name:
            raise ValueError("pet_name_value_mismatch")
        if field=="pet_relation" and raw.get("value")!="用户饲养的宠物":
            raise ValueError("pet_relation_value_invalid")
        if field=="pet_species" and (not isinstance(raw.get("value"),str) or raw["value"] not in quote or numeric_quantity(raw["value"]) is not None):
            raise ValueError("pet_species_not_supported")
    if multi and (not isinstance(topic,str) or not normalize(topic) or len(topic)>80 or topic not in quote):
        raise ValueError("automatic_topic_not_supported")
    if field=="pet_kind" and (raw.get("value")!=topic or numeric_mentions(topic)):
        raise ValueError("pet_kind_cannot_contain_quantity")
    if field=="relationship" and isinstance(raw.get("value"),str) and numeric_quantity(raw["value"]) is not None:
        raise ValueError("relationship_cannot_be_quantity")
    preference=field in {"interest","like","dislike"}
    if preference:
        # 固定偏好字段要求同分句中有明确态度表达，不能把购物/计划推测成喜好。
        # 这只是必要证据，不替代独立语义审查，也不按事物种类列关键词。
        clauses=[c for c in re.split(r"[，,。；;！？!?\n]",quote) if topic in c]
        supported=[c for c in clauses if re.search(r"喜欢|喜爱|爱好|兴趣|热爱|偏爱|讨厌|厌恶|反感",c)]
        if not supported: raise ValueError("preference_requires_stated_attitude")
        # 正向兴趣与喜欢共享主题槽；负向态度也更新同一槽。
        raw={**raw,"value":("不喜欢" if field=="dislike" else "喜欢")+topic}
        field="interest"; label="喜好"
        definition="对这一主题明确表达的喜欢或不喜欢，不从购买、旅行目标或一次行为推断态度"
    # 核心字段的subject、predicate、category不采用模型自由命名。
    subject="宠物："+pet_name if pet_name else "user"
    prepared={**raw,"subject":subject,"predicate":label,"category":category,"temporalScope":"current"}
    source=provider._source(prepared,base,observed)
    code="core_"+field+("_"+digest(normalize(topic))[:16] if multi else "")
    pid="p_"+digest(code)[:40]
    # 单值字段可沿用原本准确的当前同名记录，不自动迁移其他旧记忆。
    same=[decode_fact(m) for m in memories if decode_fact(m) and decode_fact(m).get("subject")=="user"
          and decode_fact(m).get("predicate")==label and decode_fact(m).get("temporalScope")=="current"]
    if not pet_name and not multi and len(same)==1:
        pid=predicate_id(same[0]); code=same[0].get("canonicalPredicate",code)
    source["fact"].update(entityId="e_"+digest("user_pet:"+normalize(pet_name))[:40] if pet_name else "user",predicateId=pid,canonicalPredicate=code,
        predicateDefinition=definition,entityAliases=[subject,pet_name] if pet_name else ["user"],predicateAliases=[label],
        memoryMode="core",memoryField=field)
    source.update(controlledField=field,resolution={"method":"core_schema","field":field})
    if preference:
        source["preferenceTopic"]=topic
        compatible=[decode_fact(m) for m in memories if decode_fact(m) and
                    decode_fact(m).get("memoryMode")=="core" and
                    decode_fact(m).get("memoryField") in {"interest","like","dislike"} and
                    decode_fact(m).get("temporalScope")=="current" and
                    fact_key(decode_fact(m)) in resolution_keys([source])]
        if len(compatible)>1: raise ValueError("preference_targets_ambiguous")
        if compatible:
            old=compatible[0]
            source["fact"].update(predicate=old["predicate"],predicateId=predicate_id(old),
                                  canonicalPredicate=old["canonicalPredicate"])
    return source


def custom_source(provider, raw, base, observed, memories, requested):
    target=raw.get("targetId")
    selected=None
    if target is not None:
        if not isinstance(target,int) or isinstance(target,bool): raise ValueError("explicit_target_invalid")
        selected=next((m for m in memories if m["id"]==target and
            (f:=decode_fact(m)) and f.get("memoryMode")=="explicit"),None)
        if selected is None: raise ValueError("explicit_target_not_authorized")
    if not requested and selected is None: raise ValueError("custom_memory_requires_explicit_request")
    if selected is None:
        raw=authorized_source(raw,base["latestUser"])
    if selected:
        old=decode_fact(selected)
        raw={**raw,"subject":old["subject"],"predicate":old["predicate"],
             "category":selected["category"]}
    source=provider._source(raw,base,observed)
    if source["category"]=="note":
        reason=raw.get("noteReason")
        if not isinstance(reason,str) or not 5<=len(reason.strip())<=160:
            raise ValueError("note_requires_non_category_reason")
        source["noteReason"]=reason.strip()
    source["fact"].update(memoryMode="explicit",memoryField="custom")
    if selected:
        old=decode_fact(selected)
        for key in ("entityId","predicateId","canonicalPredicate","predicateDefinition","entityAliases","predicateAliases"):
            if key in old: source["fact"][key]=old[key]
        source.update(selectedTargetId=target,controlledField="custom",
                      resolution={"method":"explicit_target","targetId":target})
    return source
