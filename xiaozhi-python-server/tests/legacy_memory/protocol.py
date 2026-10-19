"""提取和审查协议；展示文本与内部字段分离，不限制动态属性。"""
import re


def preferred_label(candidate, canonical):
    """已有ID不变；原话提取的中文同义标签优先于旧英文展示标签。"""
    if re.search(r"[\u4e00-\u9fff]",candidate) and not re.search(r"[\u4e00-\u9fff]",canonical): return candidate
    return canonical

CATEGORY_PROMPT = """从latestUser提取用户明确陈述、具有长期价值的信息。仅按automaticCategories的定义分类，不验证现实真假，不猜测。
先识别每个独立含义，再选择类别，再写具体中文属性。类别不是属性：不要把profile/habit/preference或“习惯”“喜好”直接当所有事实共用的属性。
所有对象、属性和内容用自然中文；本人对象为user，专名、型号按原话保留。不要翻译中文原话为英语。
类别定义是边界，不是允许属性清单。稳定惯用方式属于habit；身份背景属于profile；明确喜恶属于preference；持续持有的计划目标属于goal。
对象区分本人、不同所有者的集合、具体个体。未具名集合按“所有者的成员类别”命名，数量不放进对象名；集合与个体分别保存。
同句并列的集合数量必须拆开：对象分别为用户的猫、用户的狗，属性均为数量。不能用“user/宠物/三只猫两只狗”这种复合内容代替独立数量。其他集合按同样原则处理，不限宠物。
userContext只解释代词；existingExplicit只用于维护已主动授权记录。新内容、数量必须出自本轮原话；旧话、助手话、假设和转述不构成新的用户事实。
意图：普通陈述assert；真实状态变化change；纠正以前说错/记错的内容corrected；相对增减relative及有符号变化量。纠错句中的每个被纠正断言分别用corrected，不能把新总数当增量。
现在持有的计划，即使行动发生在将来，也用current，期限保留在内容中，不编日期。若事实本身为historical/future，必须提供时间精度和时间值；未明确日期的future用approximate和“未定日期”，historical用approximate和“未注明日期的过往”。
completed/cancelled先用goal/current和原目标的属性内容定位目标，由程序转换为历史；invalidated仅表示事实本身无效。
明确但未定日期的长期计划可以保留；仅“也许、有机会、随便想想”等愿望不建立确定计划。日程提醒交Schedule，临时安排不记。
note绝不参与自动提取。自动记录方式只能classified；用户主动要求、无法归入其他六类时才用custom/note并给备注理由。custom须authorizedSegments授权或existingExplicit目标ID。
每项仅一个属性，最多8项。不应保存时输出{"facts":[]}。原话编号只能选择sourceSegments已有整数ID，多项可共用同一个ID，不按事实序号创建ID。
输出JSON，使用以下中文键，枚举值仍使用指定英文代码；不输出证据改写、实体ID、属性ID或数据库操作：
{"facts":[{"方式":"classified","类别":"habit","对象":"user","属性":"惯用手","内容":"左手","意图":"assert","时间范围":"current","原话编号":0}]}
上面只是“我是左撇子”的格式示例，不是允许属性清单。其他可选键：时间精度、时间值、变化量、实体别名、目标ID、备注理由。
密码、验证码、密钥、银行卡等敏感信息不保存。
"""

AUDIT_PROMPT = """只审查本次proposedOperation的一条事实，不提取新事实、不改提案、不审查同句话的其他事实。
latestUser是本轮原话。sourceFact.fact是当前这一条已归一事实。targetMemory是唯一被更新的旧记录，空表示新增。不要假设存在输入之外的对象或旧值。
逐项输出布尔值：
sourceSupported：新值由本轮原话支持；相对增量和旧值允许程序计算。否定、假设和转述不能当作肯定的用户事实。
subjectSupported：当前提案与本条原话指向同一对象及所有者；同句多个对象分别核对，不能将另一条事实视为本条原事实。
attributeSupported：属性、内容、category与automaticCategoryDefinition一致；不存在具体属性白名单。同义名称可对应同一属性，旧错误标签不代表类别定义。
temporalSupported：时间含义忠实。现在持有的未来计划可为current，表示当前意图；不证明行动已经完成。
transitionSupported：corrected是修正以前的错误，旧值不同是正常的，不要求数值单调；changed须明确现实变化。completed允许goal转历史event，cancelled保留历史goal，invalidated才删除。
atomic：一条仅一个属性。
longTerm：保存后的状态具有持续价值；重要关系集合数量、稳定习惯和明确长期目标属于可考虑内容。临时想法、敏感信息不保存。
仅当解析确实用了旧用户上下文，才使用userContext解释对象；不能从中抽新事实。hardChecks仅说明结构检查，不代替语义判断。
category=note时另给noteFallback，只有已主动授权且无法归入其他六类才为true。
输出JSON：七个检查字段及reason。reason用简短中文说明具体不一致；只引用本次输入中出现的对象、属性和值，不能编造反例作为拒绝理由。通过也须客观，不因结构检查通过就一律同意。
"""

KEYS = {"方式":"field","类别":"category","对象":"subject","属性":"predicate","内容":"value",
        "意图":"intent","时间范围":"temporalScope","原话编号":"sourceId","时间精度":"timePrecision",
        "时间值":"timeValue","变化量":"delta","实体别名":"entityAlias","目标ID":"targetId","备注理由":"noteReason"}


def decode_candidate(raw):
    """只转换协议键，不翻译内容，不猜分类；兼容现有内部格式和测试桩。"""
    if not isinstance(raw,dict): raise ValueError("fact_not_object")
    result=dict(raw)
    for label,key in KEYS.items():
        if label not in result: continue
        value=result.pop(label)
        if key in result and result[key]!=value: raise ValueError("candidate_protocol_conflict:"+key)
        result[key]=value
    return result


async def repair_candidate_time(provider, raw, base, observed):
    """仅修一次缺失/错误时间字段，不改对象、类别、内容或用户意图。"""
    try:
        provider._source(raw,base,observed)
        return raw,False
    except ValueError as exc:
        if str(exc) not in {"invalid_time_value","invalid_time_precision","unresolved_calendar_time"}: return raw,False
        error=str(exc)
    result=await provider._ask_json("""只修复这条事实的时间字段。latestUser是唯一日期依据，referenceTime用于解释相对日期。
输出JSON对象；temporalScope选current、historical、future之一；timePrecision选day、year、month、approximate之一，不能输出斜杠连接的选项串。
未定日期的未来事实示例：{"temporalScope":"future","timePrecision":"approximate","timeValue":"未定日期"}。只补字段时也可省略未改动的键。
goal的现在持有的计划是current，current不用编行动日期；如果描述的是未来事实且未定日期，用future/approximate/未定日期。
明确日期不能抹掉或改成未定；不能改变原事实的对象、内容、分类、意图。无法确定输出{"unresolved":true}。
""",{"latestUser":base["latestUser"],"referenceTime":base.get("referenceTime"),"candidate":raw,"error":error},tokens=350)
    if result.get("unresolved") is True: raise ValueError("candidate_time_unresolved")
    if isinstance(result.get("correctedSource"),dict): result=result["correctedSource"]
    result=decode_candidate(result)
    # 修复允许只返回改动字段；缺失键不覆盖原本合法的范围。
    fixed={**raw,**{key:result[key] for key in ("temporalScope","timePrecision","timeValue") if key in result}}
    if fixed.get("temporalScope") not in {"current","historical","future"}:
        raise ValueError("candidate_time_repair_invalid_scope")
    provider._source(fixed,base,observed)
    return fixed,True


def has_compound_quantities(value):
    if not isinstance(value,str): return False
    parts=re.findall(r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+)([^\d零〇一二两三四五六七八九十\s，,。；;！？!?到至~～-]+)",value)
    return len(parts)>1


EXTRACTION_REVIEW_PROMPT = """检查最新原话的原子事实是否漏提或混合，仅处理输入的这一轮。
输出完整替换候选集{"facts":[...]}，每项沿用候选格式，最多8项。没有长期价值的分句仍跳过；不能为了覆盖率保存临时内容。
逐个独立断言检查：不要因为同句已有一个喜好就忽略稳定惯用方式；不同所有者/成员类别的集合数量必须拆成不同subject，通用predicate=数量；不要虚构具名个体。
只有同一主体的同一属性才可合为一项，不把多个数量放到user的泛化属性中。不同喜好用具体主题属性，不能互相覆盖。
category必须使用automaticCategories中的英文类别代码；只有subject/predicate/value用中文，本人subject=user。
每条必须给全协议字段，示例：{"field":"classified","category":"relationship","subject":"用户的猫","predicate":"数量","value":"三只","intent":"assert","temporalScope":"current","sourceId":0}。
此示例仅说明格式，内容和数量必须根据本轮原话填写。若无需修改，原样返回输入候选，不能用空数组表示“没有修改”。
sourceId只能选sourceSegments现有ID，多项可共用；值和关系必须来自latestUser，userContext只解代词，不把旧话补成新事实。
纠错句的每条纠正内容用corrected，新绝对数量不是relative；现在持有的计划用current，不编日期。
不得创造授权：候选field=custom或targetId仅能沿用输入中的授权项，不得把classified改成custom；不改已授权项的目标ID。
"""


async def review_extraction(provider, values, base, categories, max_facts=8):
    candidates=[decode_candidate(raw) for raw in values]
    segments=provider._source_segments(base["latestUser"])
    clauses=segments[1:] if len(segments)>1 else segments
    combined=any(c.get("category")=="relationship" and has_compound_quantities(c.get("value")) for c in candidates)
    # 只在并列分句数量超过候选数或发现复合数量时复核一次，不对所有事实叠加一轮。
    if not combined and (len(clauses)<2 or len(clauses)<=len(candidates)): return candidates
    provider._diagnose("extraction_review","compound_quantities" if combined else "possible_missing_clauses")
    try:
        result=await provider._ask_json(EXTRACTION_REVIEW_PROMPT,{"latestUser":base["latestUser"],
            "userContext":base.get("userContext",[]),"sourceSegments":segments,
            "automaticCategories":categories,"candidates":candidates},tokens=1800)
        revised=result.get("facts")
        if not isinstance(revised,list) or len(revised)>max_facts:
            raise ValueError("extraction_review_envelope_invalid")
        provider._diagnose("extraction_review_result",f"input={len(candidates)}; returned={len(revised)}")
        revised=[decode_candidate(raw) for raw in revised]
        automatic=[]
        for item in revised:
            if item.get("field")=="custom": continue
            # 此入口只能产生自动候选；补协议标记不会授予显式保存权限。
            item.setdefault("field","classified")
            if item["field"]!="classified" or "targetId" in item:
                raise ValueError("extraction_review_unauthorized_candidate")
            required=("category","subject","predicate","value","intent","temporalScope","sourceId")
            if any(key not in item for key in required):
                raise ValueError("extraction_review_candidate_incomplete")
            if item["category"] not in categories:
                raise ValueError("extraction_review_category_invalid")
            if item["category"]=="relationship" and has_compound_quantities(item["value"]):
                raise ValueError("extraction_review_still_compound")
            automatic.append(item)
        if not automatic:
            raise ValueError("extraction_review_empty_automatic_candidates")
    except (ValueError,TypeError,AttributeError) as exc:
        # 复核是增强步骤，不能静默清空提取；原候选仍须通过后续硬校验和语义审查。
        provider._diagnose("extraction_review_fallback",str(exc))
        return candidates
    # 显式授权候选原样保留；复核仅替换自动候选，授权范围仍由custom_source校验。
    explicit=[c for c in candidates if c.get("field")!="classified"]
    if len(automatic)+len(explicit)>max_facts: raise ValueError("facts_envelope_invalid")
    return automatic+explicit
