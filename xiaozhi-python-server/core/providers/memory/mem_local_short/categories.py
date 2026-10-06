"""固定大类、开放类内属性；保留原有结构校验与独立语义审查。"""
from .facts import decode_fact, digest, normalize, predicate_id, entity_id

AUTO_CATEGORIES = {
    "profile": "个人信息：真实身份与稳定背景，如年龄、职业、学籍；不包含惯用手和行为习惯",
    "relationship": "关系：重要人物、宠物与用户的关系及明确基础属性；支持具体个体及按所有者和成员类别区分的关系集合",
    "preference": "爱好与偏好：明确的喜欢、不喜欢、兴趣和交流偏好，不从一次行为推断",
    "goal": "计划目标：当前明确持有、需要持续投入的长期目标；不含临时愿望或日程指令",
    "habit": "习惯：长期稳定行为、频率及惯用方式；惯用手归此类，不属于学籍",
    "event": "重要经历：明确发生的毕业、获奖、入职等有长期价值的经历，不含日常流水账",
}

from .protocol import CATEGORY_PROMPT, preferred_label


def dynamic_predicate_code(category, label):
    """新属性身份包含类别；旧属性通过同类别目录继续沿用原ID。"""
    return "attr_"+digest(category+":"+normalize(label))[:32]


def classified_source(provider, raw, base, observed, memories):
    category=raw.get("category")
    if category not in AUTO_CATEGORIES:
        raise ValueError("automatic_category_not_allowed")
    source=provider._source(raw,base,observed)
    if "entityAlias" in raw:
        alias=raw["entityAlias"]
        if raw.get("subject")=="user" or not isinstance(alias,str) or not normalize(alias) or len(alias)>60 or alias not in source["fact"]["sourceEvidence"]:
            raise ValueError("entity_alias_not_supported")
        source["proposedEntityAlias"]=alias
    source["fact"].update(memoryMode="automatic",memoryField=category)
    source["automaticCategoryDefinition"]=AUTO_CATEGORIES[category]
    # 用户本人且名称精确匹配时走确定性路径；同义、指代或其他对象交已有解析器。
    if raw.get("subject")!="user": return source
    label=source["fact"]["predicate"]
    found={}
    for memory in memories:
        f=decode_fact(memory)
        if not f or memory.get("category")!=category or entity_id(f)!="user": continue
        if normalize(label) in {normalize(f["predicate"]),*(normalize(a) for a in f.get("predicateAliases",[]))}:
            found[predicate_id(f)]=f
    if len(found)>1: raise ValueError("automatic_attribute_ambiguous")
    if found:
        old=next(iter(found.values()))
        code=old.get("canonicalPredicate","attr_"+digest(normalize(old["predicate"]))[:16])
        source["fact"].update(predicate=preferred_label(label,old["predicate"]),entityId="user",predicateId=predicate_id(old),
            canonicalPredicate=code,predicateDefinition=old.get("predicateDefinition",label),
            entityAliases=["user"],predicateAliases=old.get("predicateAliases",[label]))
        source["controlledField"]="classified"
    elif not any(decode_fact(m) and m.get("category")==category for m in memories):
        # No retrieved candidate: deterministic IDs, followed by exact slot lookup before commit.
        code=dynamic_predicate_code(category,label)
        source["fact"].update(entityId="user",predicateId="p_"+digest(code)[:40],canonicalPredicate=code,
            predicateDefinition=f"{AUTO_CATEGORIES[category]}；独立属性：{label}"[:160],entityAliases=["user"],predicateAliases=[label])
        source["controlledField"]="classified"
    return source
